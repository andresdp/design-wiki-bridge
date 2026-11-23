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
from dwb.manifest import (
    USES_WIKI,
    Indexes,
    Models,
    PromptRef,
    QuestionSetRef,
    RunManifest,
    WebConfig,
)
from dwb.prompts import GENERATE_PROMPT, SUFFICIENCY_PROMPT
from dwb.trace import TraceRecord
from dwb.workspace import Workspace, WorkspaceError

LOG = get_logger("chat")


@dataclass(frozen=True)
class Sources:
    wiki: bool = False
    pdf: bool = True
    web: bool = False
    web_gated: bool = False  # web only when the local evidence is judged insufficient

    def describe(self) -> str:
        on = ["repository (" + ("OpenWiki-guided" if self.wiki else "vector search") + ")"]
        if self.pdf:
            on.append("pdf")
        if self.web:
            on.append("web (when local evidence is insufficient)" if self.web_gated else "web")
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
    gen = s.generator or cfg.models.generator
    emb = s.embedding or cfg.models.embedding
    if not gen or not emb:
        raise WorkspaceError("set models.generator and models.embedding in configs/chat.yaml")
    # --model replaces both chat models; otherwise rerank with the configured reranker
    reranker = gen if s.generator else (cfg.models.reranker or gen)
    models = Models(generator=gen, reranker=reranker, embedding=emb,
                    temperature=cfg.models.temperature)
    rcfg = cfg.repo(s.repo)

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
            corpus_path = rcfg.pdf_corpus or "configs/pdfs/<repo>.yaml"
            notes.append(f"PDFs skipped: no PDF index for {s.repo} (build one with: dwb index pdf "
                         f"{corpus_path} --embedding-model {emb})")
            sources = replace(sources, pdf=False)
        else:
            indexes.pdf_index_id = pdf

    excl = s.web_exclude_domains if s.web_exclude_domains is not None else \
        rcfg.web_exclude_domains
    web = WebConfig(enabled=sources.web, mode="record", store_id=f"chat-{s.repo}",
                    max_results=cfg.web.max_results, search_depth=cfg.web.search_depth,
                    exclude_domains=list(excl),
                    conditional_web_search=cfg.web.conditional_web_search)
    prompts = {"generate": PromptRef(path=cfg.prompt or str(GENERATE_PROMPT))}
    if web.gated:
        prompts["sufficiency"] = PromptRef(path=str(SUFFICIENCY_PROMPT))
    m = RunManifest(
        run_id=f"chat-{s.repo}", condition="B" if sources.wiki else "A", repo=s.repo,
        repo_sha=repo.sha, checkout=str(repo.checkout),
        question_set=QuestionSetRef(id="chat", path="-"), indexes=indexes,
        pdf_enabled=sources.pdf, models=models,
        prompts=prompts,
        budgets=cfg.budgets.model_copy(), web=web,
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
        return Sources(wiki=m.condition in USES_WIKI, pdf=m.pdf_enabled, web=m.web.enabled,
                       web_gated=m.web.gated)

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
    if trace.web_verdict:
        stats += {"sufficient": ", web not searched (local evidence judged sufficient)",
                  "insufficient": ", web searched (local evidence insufficient)",
                  "judge_failed": ", web searched (the sufficiency check failed)",
                  }[trace.web_verdict]
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


def sources_line(session: ChatSession) -> str:
    """The one wording of the active sources, in the header and for /sources."""
    return f"Sources: {session.sources.describe()}"


def print_header(session: ChatSession, context: tuple[str, ...], console) -> None:
    """The framed chat header: ``dwb <version>``, repository, mode, commit, sources, and how
    to leave (R13). Rich draws the frame with ASCII when the console cannot encode boxes."""
    from rich.markup import escape
    from rich.panel import Panel

    from dwb import __version__, ui

    name, mode, sha = (list(context) + ["", "", ""])[:3]
    rows = [f"[bold]dwb {__version__}[/]"]
    rows += [f"[dim]{k}:[/] {escape(v)}" for k, v in
             (("Repository", name), ("Mode", mode), ("Commit", sha)) if v]
    rows.append(escape(sources_line(session)))
    rows.append(f"[dim]/help {ui.glyphs(console).sep} /quit or Ctrl-D to leave[/]")
    console.print(Panel("\n".join(rows), title="chat", title_align="left",
                        border_style="cyan", expand=False))


def _reader(session: ChatSession, history: Path | None):
    """The prompt: prompt_toolkit (line editing, history) at a terminal, else plain lines."""
    import sys

    label = f"{session.settings.repo}> "
    if sys.stdin.isatty():
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
        except ImportError:  # pragma: no cover
            pass
        else:
            pt = PromptSession(history=FileHistory(str(history)) if history else None)

            async def read() -> str:
                return await pt.prompt_async(label)

            return read

    async def read_plain() -> str:  # blocking is fine: nothing else runs meanwhile
        return input(label)

    return read_plain


async def answer(session: ChatSession, question: str) -> TraceRecord | None:
    """Answer one question; ``None`` when Ctrl-C stopped it.

    While it runs, SIGINT cancels only this question's task (the loop's own handling would
    cancel the whole session, and prompt_toolkit leaves the default handler behind), and the
    previous handler comes back afterwards. A ``KeyboardInterrupt`` raised inside the
    question (no signal handler possible) counts as a stop too.
    """
    import asyncio
    import signal

    async def guarded() -> TraceRecord | None:
        try:
            return await session.ask(question)
        except KeyboardInterrupt:
            return None

    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(guarded())
    prev = signal.getsignal(signal.SIGINT)
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
        installed = True
    except (NotImplementedError, RuntimeError, ValueError):  # not the main thread, Windows
        installed = False
    try:
        return await task
    except asyncio.CancelledError:
        me = asyncio.current_task()
        if not task.cancelled() or (me is not None and me.cancelling()):
            raise  # the session itself is being cancelled, not just this question
        return None
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGINT)
            if prev is not None:
                signal.signal(signal.SIGINT, prev)


async def repl(session: ChatSession, console=None, *, history: Path | None = None,
               context: tuple[str, ...] | None = None, read=None) -> None:
    """The chat loop. ``context`` is the header's (repository, mode, commit); ``read`` is the
    prompt (an async callable returning a line), by default ``_reader``. Ctrl-C or Ctrl-D at
    the prompt leaves; Ctrl-C while answering stops that question only."""
    from dwb import ui

    console = console or ui.out()
    read = read or _reader(session, history)
    if context is None:
        sha = session.manifest.repo_sha[:12] if session.manifest else ""
        context = (session.settings.repo, "", sha)
    print_header(session, context, console)
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
            console.print(sources_line(session))
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
                console.print(sources_line(session))
            except WorkspaceError as e:
                console.print(f"[red]{e}[/]")
                await session.set_sources(**{cmd: not (arg == "on")})
        elif cmd == "ask":
            with ui.progress() as p:
                if p.animate:
                    p.show("answer", "Answering", note="Ctrl-C stops this question ")
                elif ui.interactive():
                    console.print(f"[dim]Answering{ui.glyphs(console).ellipsis}[/]")
                trace = await answer(session, arg or "")
            if trace is None:
                ui.stopped({}, console=console)
                continue
            last = trace
            print_answer(last, console=console)
        else:
            console.print(f"Unknown command /{cmd}. Type /help.")
