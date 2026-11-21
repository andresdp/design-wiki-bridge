"""Versioned prompt files: ``<system text>\\n---user---\\n<user template>``."""

from __future__ import annotations

from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from dwb.manifest import prompt_hash

USER_MARKER = "---user---"
PROMPT_DIR = Path(__file__).parent / "prompt_files"  # shipped with the package
GENERATE_PROMPT = PROMPT_DIR / "generate_v2.md"
SUFFICIENCY_PROMPT = PROMPT_DIR / "sufficiency_v1.md"  # the check_local web gate's judge


def load_prompt(path: str | Path) -> tuple[ChatPromptTemplate, str]:
    """Return the chat template and the sha256 of the whole file."""
    text = Path(path).read_text()
    if USER_MARKER not in text:
        raise ValueError(f"prompt {path} lacks the {USER_MARKER} separator")
    system, user = (p.strip() for p in text.split(USER_MARKER, 1))
    # literal braces in the system text must not be treated as template variables
    system = system.replace("{", "{{").replace("}", "}}")
    template = ChatPromptTemplate.from_messages([("system", system), ("human", user)])
    return template, prompt_hash(text)
