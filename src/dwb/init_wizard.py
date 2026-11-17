"""``dwb init``: an interactive guide that writes ``<repo>/.dwb/config.yaml``."""

from __future__ import annotations

import os
import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from dwb.project import (
    CONFIG_NAME,
    DWB_DIR,
    GLOBAL_ENV,
    PdfEntry,
    ProjectConfig,
    git_root,
    head_sha,
    is_dirty,
    openwiki_status,
    openwiki_version,
    save_global_key,
    suggest_exclude_domains,
    write_config,
)
from dwb.workspace import WorkspaceError


class Asker:
    """Prompts, or defaults when ``yes`` (non-interactive)."""

    def __init__(self, console: Console, yes: bool):
        self.c, self.yes = console, yes

    def text(self, q: str, default: str = "") -> str:
        return default if self.yes else Prompt.ask(q, default=default, console=self.c)

    def confirm(self, q: str, default: bool) -> bool:
        return default if self.yes else Confirm.ask(q, default=default, console=self.c)

    def secret(self, q: str) -> str:
        return "" if self.yes else Prompt.ask(q, password=True, default="", console=self.c,
                                              show_default=False)


def _section(c: Console, title: str) -> None:
    c.print(f"\n[bold cyan]{title}[/]")


def run_init(start: Path, console: Console | None = None, *, yes: bool = False,
             force: bool = False) -> Path:
    c = console or Console()
    ask = Asker(c, yes)
    root = git_root(start)
    if root is None:
        raise WorkspaceError(f"{start} is not inside a git repository. dwb indexes committed "
                             "code; run `git init` and commit first, or cd into a repository.")
    cfg_path = root / DWB_DIR / CONFIG_NAME
    if cfg_path.exists() and not force and not ask.confirm(
            f"{cfg_path} already exists. Overwrite it?", default=False):
        raise WorkspaceError(f"kept the existing {cfg_path} (edit it, or rerun with --force)")

    sha = head_sha(root)
    c.print(Panel(f"Repository: [bold]{root}[/]\nCommit to index: HEAD [bold]{sha[:12]}[/]"
                  + ("\n[yellow]Uncommitted changes are not indexed; commit them first if they "
                     "matter.[/]" if is_dirty(root) else ""),
                  title="dwb init", border_style="cyan"))
    name = ask.text("Project name", default=re.sub(r"\W+", "-", root.name).strip("-") or "repo")

    _section(c, "Models (OpenAI)")
    chat_model = ask.text("Chat model (answers and reranking)", default="gpt-5.6-luna")
    emb_model = ask.text("Embedding model (indexes)", default="text-embedding-3-large")

    _section(c, "PDF documents (optional)")
    c.print("Specifications, papers or manuals that explain what the code implements. "
            "Enter local paths (relative to the repository) or URLs, one per line; "
            "an empty line finishes.")
    pdfs: list[PdfEntry] = []
    while not yes:
        src = Prompt.ask("PDF", default="", console=c, show_default=False).strip()
        if not src:
            break
        if not re.match(r"https?://", src) and not (root / src).exists():
            c.print(f"[red]Not found:[/] {root / src}")
            continue
        title = Prompt.ask("  Title", default=Path(src.split("?")[0]).stem, console=c)
        pdfs.append(PdfEntry(source=src, title=title))

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
    _ensure_key(c, ask, "OPENAI_API_KEY", "embeddings and answers", required=True)
    if web:
        _ensure_key(c, ask, "TAVILY_API_KEY", "web search", required=False)

    cfg = ProjectConfig(
        name=name,
        models={"chat": chat_model, "embedding": emb_model},
        sources={"wiki": src_wiki, "pdf": bool(pdfs), "web": web},
        pdfs=pdfs,
        web={"exclude_domains": exclude},
        wiki={"enabled": wiki_enabled, "generate": wiki_generate, "model": wiki_model},
    )
    path = write_config(root, cfg)
    c.print(Panel(f"Wrote [bold]{path}[/] (review or edit it any time).\n"
                  f"{DWB_DIR}/ is hidden from git through .git/info/exclude.\n\n"
                  "Next: [bold]dwb index[/]   then   [bold]dwb chat[/]",
                  title="Done", border_style="green"))
    return path


def _ensure_key(c: Console, ask: Asker, name: str, purpose: str, *, required: bool) -> None:
    if os.environ.get(name):
        c.print(f"{name}: [green]found[/]")
        return
    c.print(f"{name} ({purpose}) is not set.")
    value = ask.secret(f"Paste {name} to save it in {GLOBAL_ENV} (Enter to skip)")
    if value:
        save_global_key(name, value)
        c.print(f"Saved to {GLOBAL_ENV} (readable only by you).")
    elif required:
        c.print(f"[yellow]Set {name} before `dwb index`: export it, or put it in {GLOBAL_ENV}.[/]")
