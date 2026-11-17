"""Structured-claim generation and citation validation (KTD9, R2, R3)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from dwb.budget import Context
from dwb.manifest import prompt_hash
from dwb.trace import AnswerClaim, StageRecord

USER_MARKER = "---user---"


class GeneratedClaim(BaseModel):
    """One claim and the evidence IDs (E1, E2, ...) that support it."""

    text: str = Field(description="A short, independently checkable claim.")
    evidence_ids: list[str] = Field(
        default_factory=list, description='Supporting evidence IDs, e.g. ["E1", "E3"].'
    )


class GeneratedAnswer(BaseModel):
    """An answer as a list of cited claims."""

    claims: list[GeneratedClaim]
    summary: str | None = Field(default=None, description="Optional one-sentence summary.")


def load_prompt(path: str | Path) -> tuple[ChatPromptTemplate, str]:
    text = Path(path).read_text()
    if USER_MARKER not in text:
        raise ValueError(f"prompt {path} lacks the {USER_MARKER} separator")
    system, user = (p.strip() for p in text.split(USER_MARKER, 1))
    # literal braces in the system text must not be treated as template variables
    system = system.replace("{", "{{").replace("}", "}}")
    return ChatPromptTemplate.from_messages([("system", system), ("human", user)]), prompt_hash(
        text
    )


class Generator:
    def __init__(self, llm: BaseChatModel, prompt_path: str | Path, *,
                 model_id: str | None = None, structured_method: str | None = None):
        self.prompt, self.prompt_hash = load_prompt(prompt_path)
        kw = {"method": structured_method} if structured_method else {}
        self.chain = self.prompt | llm.with_structured_output(GeneratedAnswer, **kw)
        self.model_id = model_id or getattr(llm, "model_name", None) or type(llm).__name__
        self.structured_method = structured_method

    async def agenerate(
        self, question: str, context: Context
    ) -> tuple[GeneratedAnswer | None, StageRecord]:
        usage = UsageMetadataCallbackHandler()
        t0 = time.perf_counter()
        answer: GeneratedAnswer | None = None
        error = None
        try:
            answer = await self.chain.ainvoke(
                {"question": question, "context": context.text}, config={"callbacks": [usage]}
            )
            if not isinstance(answer, GeneratedAnswer):
                answer = GeneratedAnswer.model_validate(answer)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            answer = None
        stage = StageRecord(
            name="generate",
            latency_ms=(time.perf_counter() - t0) * 1000,
            input_tokens=sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values()),
            output_tokens=sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values()),
            count=len(answer.claims) if answer else 0,
            extra={"model": self.model_id, "prompt_hash": self.prompt_hash,
                   "structured_method": self.structured_method, "error": error},
        )
        return answer, stage


def validate_claims(
    answer: GeneratedAnswer, context: Context
) -> tuple[list[AnswerClaim], dict[str, Any]]:
    """Map aliases to stable IDs. Unknown aliases are logged; their claim is kept (R2)."""
    by_id = {v: v for v in context.aliases.values()}
    claims: list[AnswerClaim] = []
    unresolved: list[dict[str, Any]] = []
    for idx, c in enumerate(answer.claims):
        resolved, missing = [], []
        for ref in c.evidence_ids:
            ref = ref.strip().strip("[]")
            sid = context.aliases.get(ref) or by_id.get(ref)
            if sid and sid not in resolved:
                resolved.append(sid)
            elif not sid:
                missing.append(ref)
        if missing:
            unresolved.append({"claim_index": idx, "aliases": missing})
        claims.append(AnswerClaim(text=c.text, cited_aliases=list(c.evidence_ids),
                                  evidence_ids=resolved, unresolved_aliases=missing))
    report = {
        "n_claims": len(claims),
        "uncited_claims": [i for i, c in enumerate(claims) if c.uncited],
        "unresolved": unresolved,
        "n_unresolved_ids": sum(len(u["aliases"]) for u in unresolved),
    }
    return claims, report


def render_answer(claims: list[AnswerClaim], summary: str | None = None,
                  aliases: dict[str, str] | None = None) -> str:
    inv = {v: k for k, v in (aliases or {}).items()}
    parts = [summary.strip()] if summary else []
    for c in claims:
        cites = [inv.get(i, i) for i in c.evidence_ids]
        parts.append(f"- {c.text}" + (f" [{', '.join(cites)}]" if cites else ""))
    return "\n".join(parts)
