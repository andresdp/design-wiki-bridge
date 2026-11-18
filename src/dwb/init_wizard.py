"""``dwb init``: an interactive guide that writes ``<repo>/.dwb/config.yaml``."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from dwb import __version__, ui
from dwb.project import (
    CONFIG_NAME,
    DWB_DIR,
    PdfEntry,
    ProjectConfig,
    folder_pdfs,
    git_root,
    head_sha,
    hide_from_git,
    is_dirty,
    is_url,
    openwiki_status,
    openwiki_version,
    project_env,
    save_key,
    suggest_exclude_domains,
    write_config,
)
from dwb.workspace import WorkspaceError

STEPS = ["Models (OpenAI)", "PDF documents (optional)", "Web search (optional, Tavily)",
         "OpenWiki (optional)", "Default sources for ask/chat", "API keys"]
_NOT_WRITTEN = f"{DWB_DIR}/{CONFIG_NAME} was not written."


def mask_secret(value: str) -> str:
    """``sk-*****xyz``: enough to recognize a key, never enough to use it."""
    return f"{value[:3]}*****{value[-3:]}" if len(value) >= 12 else "*****"


class Asker:
    """Prompts, or defaults when ``yes`` (non-interactive)."""

    def __init__(self, console: Console, yes: bool):
        self.c, self.yes = console, yes

    def text(self, q: str, default: str = "") -> str:
        return default if self.yes else Prompt.ask(q, default=default, console=self.c)

    def confirm(self, q: str, default: bool) -> bool:
        return default if self.yes else Confirm.ask(q, default=default, console=self.c)

    def secret(self, q: str) -> str:
        """Read a secret, echoing ``*`` per character, then confirm it masked."""
        if self.yes:
            return ""
        value = ""
        if sys.stdin.isatty():
            try:
                from prompt_toolkit import prompt
            except ImportError:  # pragma: no cover
                prompt = None
            if prompt is not None:
                value = prompt(f"{q}: ", is_password=True)
        else:  # piped input (tests, scripts): nothing to echo
            value = Prompt.ask(q, password=True, default="", console=self.c, show_default=False)
        value = value.strip()
        if value:
            self.c.print(f"  Received {mask_secret(value)}")
        return value


def _section(c: Console, title: str) -> None:
    """``Step 2 of 6: PDF documents (optional)``, the details after the name dimmed."""
    n = STEPS.index(title) + 1
    name, _, rest = title.partition(" (")
    extra = f" [dim]({rest}[/]" if rest else ""
    c.print(f"\n[bold cyan]Step {n} of {len(STEPS)}: {name}[/]{extra}")


def require_terminal(yes: bool) -> None:
    """Without a terminal, ``dwb init`` never prompts: it needs ``--yes`` (R14)."""
    if not yes and not ui.interactive():
        raise WorkspaceError("dwb init asks questions and needs a terminal. Rerun with --yes "
                             "to accept every default (then edit .dwb/config.yaml).")


def run_init(start: Path, console: Console | None = None, *, yes: bool = False,
             force: bool = False) -> Path:
    c = console or ui.out()
    ask = Asker(c, yes)
    root = git_root(start)
    if root is None:
        raise WorkspaceError(f"{start} is not inside a git repository. dwb indexes committed "
                             "code; run `git init` and commit first, or cd into a repository.")
    require_terminal(yes)
    ui.note_on_stop("Nothing was written.", key="init")
    c.print(f"[bold]dwb {__version__}[/]")
    cfg_path = root / DWB_DIR / CONFIG_NAME
    if cfg_path.exists() and not force and not ask.confirm(
            f"{cfg_path} already exists. Overwrite it?", default=False):
        raise WorkspaceError(f"kept the existing {cfg_path} (edit it, or rerun with --force)")

    sha = head_sha(root)
    c.print(Panel(f"Repository: [bold]{root}[/]\nCommit to index: HEAD [bold]{sha[:12]}[/]"
                  + ("\n[yellow]Uncommitted changes are not indexed; commit them first if they "
                     "matter.[/]" if is_dirty(root) else ""),
                  title="init", title_align="left", border_style="cyan"))
    name = ask.text("Project name", default=re.sub(r"\W+", "-", root.name).strip("-") or "repo")

    _section(c, "Models (OpenAI)")
    chat_model = ask.text("Chat model (answers and reranking)", default="gpt-5.6-luna")
    emb_model = ask.text("Embedding model (indexes)", default="text-embedding-3-large")

    _section(c, "PDF documents (optional)")
    c.print("Specifications, papers or manuals that explain what the code implements. "
            "Enter local files or folders (relative to the repository; a folder adds every "
            "PDF inside it) or URLs, one per line; an empty line finishes.")
    pdfs: list[PdfEntry] = []
    added: set[str] = set()
    while not yes:
        src = Prompt.ask("PDF file, folder or URL", default="", console=c,
                         show_default=False).strip()
        if not src:
            break
        key = src if is_url(src) else str((root / src).resolve())
        if key in added:
            c.print(f"[yellow]Already added:[/] {src}")
            continue
        if not is_url(src) and not (root / src).exists():
            c.print(f"[red]Not found:[/] {root / src}")
            continue
        if not is_url(src) and (root / src).is_dir():
            n = len(folder_pdfs(root / src))
            if n == 0:
                c.print(f"[red]No PDF files in[/] {root / src}")
                continue
            c.print(f"  Folder with {n} PDF files (each titled by its file name)")
            pdfs.append(PdfEntry(source=src))
        else:
            title = Prompt.ask("  Title", default=Path(src.split("?")[0]).stem, console=c)
            pdfs.append(PdfEntry(source=src, title=title))
        added.add(key)

    _section(c, "Web search (optional, Tavily)")
    web = ask.confirm("Use web search by default when answering?", default=False)
    excl = suggest_exclude_domains(root)
    c.print("The project's own docs and source hosts are excluded, so web evidence stays "
            "external to the code.")
    excl_txt = ask.text("Excluded domains (comma-separated)", default=", ".join(excl))
    exclude = [d.strip() for d in excl_txt.split(",") if d.strip()]

    _section(c, "OpenWiki (optional)")
    ver = openwiki_version()
    ow = openwiki_status(root)
    wiki_enabled = wiki_generate = False
    wiki_model = chat_model
    if ow["exists"]:
        state = "matches HEAD" if ow["git_head"] == sha else \
            f"generated at {str(ow['git_head'])[:12]}, not HEAD"
        c.print(f"Found openwiki/ ({state}, model {ow['model']}).")
    else:
        c.print("No openwiki/ folder yet.")
    if ver is None:
        c.print("[dim]openwiki is not installed (npm install -g openwiki@0.9.0), so the wiki "
                "source stays off. Rerun dwb init after installing it.[/]")
    else:
        wiki_enabled = ask.confirm("Enable the wiki source (repository evidence found through "
                                   "the generated wiki)?", default=ow["exists"])
        if wiki_enabled and not (ow["exists"] and ow["git_head"] == sha):
            c.print("Generating the wiki makes model calls with your API key (about 15 minutes "
                    "for a mid-size repository) and writes openwiki/, AGENTS.md and a GitHub "
                    "workflow into the repository.")
            wiki_generate = ask.confirm("Generate or update it during `dwb index`?", default=False)
            if wiki_generate:
                wiki_model = ask.text("  OpenWiki model", default=ow["model"] or chat_model)

    _section(c, "Default sources for ask/chat")
    c.print("The repository is always on. Switch the others per question with --pdf/--web/--wiki "
            "or /pdf /web /wiki in chat.")
    src_wiki = wiki_enabled and ask.confirm("Use the wiki by default (instead of vector search)?",
                                            default=False)

    _section(c, "API keys")
    env = project_env(root)
    saved = []
    if key := _ensure_key(c, ask, env, "OPENAI_API_KEY", "embeddings and answers",
                          required=True):
        saved.append(key)
    if web and (key := _ensure_key(c, ask, env, "TAVILY_API_KEY", "web search",
                                   required=False)):
        saved.append(key)

    cfg = ProjectConfig(
        name=name,
        models={"chat": chat_model, "embedding": emb_model},
        sources={"wiki": src_wiki, "pdf": bool(pdfs), "web": web},
        pdfs=pdfs,
        web={"exclude_domains": exclude},
        wiki={"enabled": wiki_enabled, "generate": wiki_generate, "model": wiki_model},
    )
    path = write_config(root, cfg)
    ui.keep(str(path))
    ui.note_on_stop(None, key="init")
    c.print("\n[bold]Summary[/]")
    ui.bullet(f"{DWB_DIR}/ is hidden from git through .git/info/exclude", console=c)
    for k in saved:
        ui.bullet(f"saved {k} to {env}", console=c)
    ui.bullet("review or edit the configuration any time; after indexing: dwb chat", console=c)
    ui.outcome(f"Wrote {path}", "dwb index", console=c)
    return path


def _saved_value(env: Path, name: str) -> str | None:
    """The value of ``name`` in the project's .dwb/.env, or None (missing file or key)."""
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover
        return None
    return dotenv_values(env).get(name) if env.exists() else None


def _ensure_key(c: Console, ask: Asker, env: Path, name: str, purpose: str, *,
                required: bool) -> str | None:
    """Offer to save a missing key in the project's .dwb/.env, or to replace the one saved
    there; returns its name when a key was saved."""
    current = os.environ.get(name)
    if current:
        saved_here = _saved_value(env, name) == current
        where = env if saved_here else "your shell environment"
        c.print(f"{name}: [green]found[/] in {where} ({mask_secret(current)})")
        if not saved_here:  # a shell variable wins over the file: a new key there would not apply
            c.print("  [dim]To use another key, change or unset it in your shell.[/]")
            return None
        if ask.yes or ask.confirm(f"Use this {name}?", default=True):
            return None
        prompt = f"Paste the new {name} to save it in {env} (Enter to keep the current one)"
    else:
        c.print(f"{name} ({purpose}) is not set.")
        prompt = f"Paste {name} to save it in {env} (Enter to skip)"
    value = ask.secret(prompt)
    if value:
        hide_from_git(env.parent.parent)  # before the key exists on disk
        save_key(name, value, env)
        ui.keep(f"{name} in {env}")
        ui.note_on_stop(_NOT_WRITTEN, key="init")
        c.print(f"Saved to {env} (readable only by you, hidden from git).")
        return name
    if required and not current:
        c.print(f"[yellow]Set {name} before `dwb index`: rerun dwb init, or put it in {env}.[/]")
