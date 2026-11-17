"""Interactive question answering over a configured repository (``dwb ask`` / ``dwb chat``).

Chat reuses the experiment pipeline unchanged: each question runs the same query graph as
condition A (wiki off: vector code search) or condition B (wiki on: OpenWiki-guided code
spans). The repository is always a source; PDFs and the Web are optional. Questions are
answered independently (no conversation memory), so every answer is grounded only in the
evidence retrieved for it.
"""

from __future__ import annotations

import itertools
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dwb.graph import Components, build_graph, trace_from_state
from dwb.log import get_logger
from dwb.manifest import Budgets, Indexes, Models, PromptRef, QuestionSetRef, RunManifest, WebConfig
from dwb.prompts import GENERATE_PROMPT
from dwb.trace import TraceRecord
from dwb.workspace import Workspace, WorkspaceError

LOG = get_logger("chat")


@dataclass(frozen=True)
class Sources:
    wiki: bool = False
    pdf: bool = True
    web: bool = False

    def describe(self) -> str:
        on = ["repository (" + ("OpenWiki-guided" if self.wiki else "vector search") + ")"]
        on += [s for s in ("pdf", "web") if getattr(self, s)]
        return ", ".join(on)


@dataclass
class ChatSettings:
    repo: str
    sources: Sources = field(default_factory=Sources)
    generator: str | None = None
    embedding: str | None = None
    web_exclude_domains: list[str] | None = None


@dataclass
class ChatQuestion:
    """What the graph needs from a question (a subset of ``QuestionRecord``)."""

    id: str
    question: str
    allowed_sources: list[str]


def build_manifest(ws: Workspace, s: ChatSettings) -> tuple[RunManifest, list[str]]:
    """In-memory manifest for one source configuration. Returns (manifest, notes); raises
    ``WorkspaceError`` with the fixing command when a required artifact is missing."""
    cfg = ws.chat_config()
    repo = ws.repo(s.repo)
    notes: list[str] = []
    if not repo.checkout_ok:
        raise WorkspaceError(
            f"checkout for {s.repo} not found at {repo.checkout}. Clone {repo.url} and check out "
            f"{repo.sha} there (see docs/usage.md, 'Adding a repository')."
        )
    mcfg = cfg.get("models", {})
    gen = s.generator or mcfg.get("generator")
    emb = s.embedding or mcfg.get("embedding")
    if not gen or not emb:
        raise WorkspaceError("set models.generator and models.embedding in configs/chat.yaml")
    models = Models(generator=gen, reranker=mcfg.get("reranker") if not s.generator else gen,
                    embedding=emb, temperature=mcfg.get("temperature", 0.0))

    indexes = Indexes()
    sources = s.sources
    if sources.wiki:
        snap = ws.find_wiki_snapshot(s.repo, repo.sha)
        if snap is None:
            raise WorkspaceError(
                f"no OpenWiki snapshot for {s.repo} at {repo.sha[:12]}. Generate the wiki in "
                f"{repo.checkout} and run: dwb wiki snapshot {s.repo} --provider ... --model ... "
                "(or answer without it: --no-wiki)"
            )
        indexes.wiki_snapshot_id = snap["snapshot_id"]
    else:
        code = ws.find_code_index(s.repo, repo.sha, emb)
        if code is None:
            raise WorkspaceError(
                f"no code index for {s.repo} at {repo.sha[:12]} with {emb}. Run: "
                f"dwb index code {s.repo} --embedding-model {emb}"
            )
        indexes.code_index_id = code
    if sources.pdf:
        corpus = ws.pdf_corpus_id(s.repo)
        pdf = ws.find_pdf_index(corpus, emb) if corpus else None
        if pdf is None:
            corpus_path = ws.repo_chat_config(s.repo).get("pdf_corpus", "configs/pdfs/<repo>.yaml")
            notes.append(f"PDFs skipped: no PDF index for {s.repo} (build one with: dwb index pdf "
                         f"{corpus_path} --embedding-model {emb})")
            sources = replace(sources, pdf=False)
        else:
            indexes.pdf_index_id = pdf

    rcfg = ws.repo_chat_config(s.repo)
    wcfg = cfg.get("web", {})
    excl = s.web_exclude_domains if s.web_exclude_domains is not None else \
        rcfg.get("web_exclude_domains", [])
    web = WebConfig(enabled=sources.web, mode="record", store_id=f"chat-{s.repo}",
                    max_results=wcfg.get("max_results", 5),
                    search_depth=wcfg.get("search_depth", "basic"), exclude_domains=excl)
    m = RunManifest(
        run_id=f"chat-{s.repo}", condition="B" if sources.wiki else "A", repo=s.repo,
        repo_sha=repo.sha, checkout=str(repo.checkout),
        question_set=QuestionSetRef(id="chat", path="-"), indexes=indexes,
        pdf_enabled=sources.pdf, models=models,
        prompts={"generate": PromptRef(path=cfg.get("prompt") or str(GENERATE_PROMPT))},
        budgets=Budgets(**(cfg.get("budgets") or {})), web=web,
    )
    return m, notes


class ChatSession:
    """``async with ChatSession(ws, settings) as chat: trace = await chat.ask("...")``."""

    def __init__(self, ws: Workspace, settings: ChatSettings, *,
                 components: Components | None = None):
        self.ws = ws
        self.settings = settings
        self._injected = components
        self._stack: AsyncExitStack | None = None
        self._graph = None
        self.manifest: RunManifest | None = None
        self.notes: list[str] = []
        self._ids = itertools.count(1)

    @property
    def sources(self) -> Sources:
        m = self.manifest
        if m is None:
            return self.settings.sources
        return Sources(wiki=m.condition == "B", pdf=m.pdf_enabled, web=m.web.enabled)

    async def _open(self) -> None:
        from dwb.runner import build_components, start_wiki

        self.manifest, self.notes = build_manifest(self.ws, self.settings)
        for n in self.notes:
            LOG.warning(n)
        ws = self.ws
        c = self._injected or build_components(
            self.manifest, base_dir=ws.root, index_root=ws.resolve(ws.index_root),
            tavily_root=ws.resolve(ws.tavily_root), wiki_root=ws.resolve(ws.wiki_root),
            restore_wiki=ws.restores_wiki)
        stack = AsyncExitStack()
        try:
            await start_wiki(stack, c)
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._graph = build_graph(c, self.manifest)
        LOG.info("Sources: %s", self.sources.describe())

    async def __aenter__(self) -> ChatSession:
        await self._open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack:
            await self._stack.aclose()

    async def set_sources(self, **changes: bool) -> None:
        """Switch sources mid-session (restarts components when the repository branch changes)."""
        old = (self.settings, self._injected, self._stack, self._graph, self.manifest, self.notes)
        self.settings = replace(self.settings, sources=replace(self.settings.sources, **changes))
        self._injected = None if "wiki" in changes else self._injected
        try:
            await self._open()  # the new session first: on failure the old one keeps working
        except BaseException:
            (self.settings, self._injected, self._stack, self._graph, self.manifest,
             self.notes) = old
            raise
        if old[2] is not None:
            await old[2].aclose()

    async def ask(self, question: str) -> TraceRecord:
        assert self.manifest is not None and self._graph is not None, "use 'async with'"
        allowed = ["code"] + [s for s in ("pdf", "web") if getattr(self.sources, s)]
        q = ChatQuestion(id=f"chat-{next(self._ids)}", question=question, allowed_sources=allowed)
        t0 = time.perf_counter()
        try:
            state = await self._graph.ainvoke({"question": q})
            trace = trace_from_state(state, self.manifest, keep_code_text=True)
        except Exception as e:
            trace = TraceRecord(question_id=q.id, run_id=self.manifest.run_id,
                                condition=self.manifest.condition,
                                errors=[f"{type(e).__name__}: {e}"])
        trace.validation.setdefault("elapsed_s", round(time.perf_counter() - t0, 2))
        return trace


# --------------------------------------------------------------------------- rendering


def locator_label(ev: dict[str, Any]) -> str:
    loc = ev["locator"]
    if ev["source_type"] == "code":
        return f"{loc['path']}:{loc['start_line']}-{loc['end_line']}"
    if ev["source_type"] == "pdf":
        return f"{loc['doc_id']} p.{loc['page']}"
    return loc["url"]


def how_found(ev: dict[str, Any]) -> str:
    p = ev.get("provenance", {})
    if ev["source_type"] != "code":
        return ev["source_type"]
    if p.get("branch") == "wiki":
        return f"wiki ({p.get('resolution_method', '?')})"
    return "vector search"


def print_answer(trace: TraceRecord, *, show_context: bool = False, console=None) -> None:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table

    console = console or Console()
    if trace.errors:
        console.print(Panel("\n".join(trace.errors), title="[red]Error", border_style="red"))
        return
    if trace.empty_context:
        console.print(Panel("No evidence was found in the enabled sources, so no answer was "
                            "generated. Try enabling more sources (/pdf on, /web on) or "
                            "switching the repository search (/wiki on|off).",
                            title="[yellow]No evidence", border_style="yellow"))
        return
    inv = {v: k for k, v in trace.aliases.items()}
    lines = [trace.summary.strip(), ""] if trace.summary else []
    for c in trace.claims:
        if c.kind == "gap":
            lines.append(f"- ⚠ *Not established by the evidence:* {c.text}")
            continue
        cites = " ".join(f"`[{inv.get(i, i)}]`" for i in c.evidence_ids)
        bad = " ".join(f"`[{a}?]`" for a in c.unresolved_aliases)
        lines.append(f"- {c.text} {cites} {bad}".rstrip())
    console.print(Panel(Markdown("\n".join(lines) or "_(no claims)_"), title="Answer",
                        border_style="green"))
    cited = {i for c in trace.claims for i in c.evidence_ids}
    table = Table(title="Sources", show_lines=False, expand=False)
    table.add_column("ID", style="bold")
    table.add_column("Type")
    table.add_column("Location", overflow="fold")
    table.add_column("Found via")
    for alias, sid in trace.aliases.items():
        ev = trace.evidence.get(sid, {})
        if not ev or (sid not in cited and not show_context):
            continue
        table.add_row(alias, ev["source_type"], locator_label(ev), how_found(ev))
    if table.row_count:
        console.print(table)
    if show_context:
        for alias, sid in trace.aliases.items():
            ev = trace.evidence.get(sid, {})
            console.print(Panel(ev.get("text", "")[:1500], title=f"{alias} {locator_label(ev)}",
                                border_style="dim"))
    stats = f"{len(trace.final_context_ids)} passages in context, {len(trace.claims)} claims"
    unresolved = trace.validation.get("n_unresolved_ids", 0)
    if unresolved:
        stats += f", [red]{unresolved} citation IDs did not resolve[/]"
    if trace.wiki_section_refs:
        stats += f", {len(trace.wiki_section_refs)} wiki sections searched"
    stats += f", {trace.validation.get('elapsed_s', 0):.1f} s"
    console.print(f"[dim]{stats}[/]")


HELP = """[bold]Commands[/]
  /wiki on|off   repository evidence via the OpenWiki snapshot, or via vector search
  /pdf on|off    use the PDF corpus
  /web on|off    use live web search (Tavily; the project's own docs are excluded)
  /sources       show the active sources
  /context       show the passages behind the last answer
  /help          this help
  /quit          leave (also Ctrl-D)
Anything else is a question about the repository."""


def parse_command(line: str) -> tuple[str, str | None]:
    """'/web on' -> ('web', 'on'); '/quit' -> ('quit', None); a question -> ('ask', line)."""
    text = line.strip()
    if not text.startswith("/"):
        return ("ask", text) if text else ("noop", None)
    parts = text[1:].split()
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].lower() if len(parts) > 1 else None
    aliases = {"q": "quit", "exit": "quit", "h": "help", "?": "help"}
    return aliases.get(cmd, cmd), arg


async def repl(session: ChatSession, console=None, *, history: Path | None = None) -> None:
    from rich.console import Console

    console = console or Console()
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory

        pt = PromptSession(history=FileHistory(str(history)) if history else None)

        async def read() -> str:
            return await pt.prompt_async(f"{session.settings.repo}> ")
    except ImportError:  # pragma: no cover
        import asyncio

        async def read() -> str:
            return await asyncio.to_thread(input, f"{session.settings.repo}> ")

    console.print(f"[bold]dwb chat[/] · {session.settings.repo} · sources: "
                  f"{session.sources.describe()}\nType a question, or /help for commands.")
    last: TraceRecord | None = None
    while True:
        try:
            line = await read()
        except (EOFError, KeyboardInterrupt):
            break
        cmd, arg = parse_command(line)
        if cmd == "noop":
            continue
        if cmd == "quit":
            break
        if cmd == "help":
            console.print(HELP)
        elif cmd == "sources":
            console.print(f"Sources: {session.sources.describe()}")
        elif cmd == "context":
            if last:
                print_answer(last, show_context=True, console=console)
            else:
                console.print("[dim]No answer yet.[/]")
        elif cmd in ("wiki", "pdf", "web"):
            if arg not in ("on", "off"):
                console.print(f"Usage: /{cmd} on|off")
                continue
            try:
                await session.set_sources(**{cmd: arg == "on"})
                console.print(f"Sources: {session.sources.describe()}")
            except WorkspaceError as e:
                console.print(f"[red]{e}[/]")
                await session.set_sources(**{cmd: not (arg == "on")})
        elif cmd == "ask":
            with console.status("Retrieving evidence and answering…"):
                last = await session.ask(arg or "")
            print_answer(last, console=console)
        else:
            console.print(f"Unknown command /{cmd}. Type /help.")
