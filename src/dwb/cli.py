"""``dwb``: ask questions about a repository with cited answers, and run the experiments.

Two modes. User mode: inside any git repository, ``dwb init`` writes ``.dwb/config.yaml`` and
the other commands use it. Research mode: run from the design-wiki-bridge folder, where
configuration (``configs/``), artifacts (``artifacts/``) and API keys (``.env``) are resolved
relative to the current folder, and pick a repository with ``--repo``. See docs/usage.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from typer.core import TyperGroup

from dwb import __version__, ui
from dwb.log import get_logger, setup_logging
from dwb.workspace import Workspace, WorkspaceError

LOG = get_logger("cli")

# Expected errors: shown as `✗ <message>` (plus `→ <fix>` when the error has a `fix`), exit 2.
# Matched by name so the boundary imports nothing heavy.
EXPECTED_ERRORS = (
    "dwb.workspace.WorkspaceError", "dwb.retrieval.openwiki_client.OpenWikiError",
    "dwb.runner.QuestionSetError", "dwb.runner.IndexMismatchError",
    "dwb.manifest.ManifestError", "dwb.evidence_text.EvidenceTextError",
    "dwb.eval.command.UnpairedRunsError",
)


def _control_flow() -> tuple[type[BaseException], ...]:
    """Click's own exits and usage errors (RuntimeError subclasses): never ours to report."""
    from typer._click.exceptions import ClickException, Exit

    kinds: list[type[BaseException]] = [Exit, ClickException, typer.Exit]
    try:
        import click

        kinds += [click.exceptions.Exit, click.ClickException]
    except ImportError:  # pragma: no cover
        pass
    return tuple(kinds)


def _stops() -> tuple[type[BaseException], ...]:
    """A user stop: Ctrl-C anywhere, or Ctrl-C / Ctrl-D (end of input) at a prompt, which
    Click's prompt turns into Abort and rich's into EOFError. dwb never raises Abort itself."""
    from typer._click.exceptions import Abort

    kinds: list[type[BaseException]] = [KeyboardInterrupt, EOFError, Abort, typer.Abort]
    try:
        import click

        kinds.append(click.exceptions.Abort)
    except ImportError:  # pragma: no cover
        pass
    return tuple(kinds)


def _is_expected(e: BaseException) -> bool:
    return any(f"{c.__module__}.{c.__qualname__}" in EXPECTED_ERRORS for c in type(e).__mro__)


class _App(TyperGroup):
    """The one exception boundary (KTD5): every command runs inside ``invoke``."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except _stops():
            ui.stopped(ctx.meta.get(ui.STOP_KEY, {}))
            raise typer.Exit(130) from None
        except _control_flow():
            raise
        except Exception as e:
            if _is_expected(e):
                ui.error(str(e), getattr(e, "fix", None))
                raise typer.Exit(2) from None
            ui.unexpected(e, verbose=(ctx.params.get("verbose") or 0) > 0)
            raise typer.Exit(1) from None


def _examples(*lines: str) -> str:
    """Help epilog: one example per line (a blank line between them keeps rich from joining)."""
    return "[bold]Examples[/]\n\n" + "\n\n".join(f"  [dim]$[/] {ln}" for ln in lines)


ASK, USER, RESEARCH = "Ask", "User mode", "Research mode"

app = typer.Typer(
    name="dwb", cls=_App, no_args_is_help=True, rich_markup_mode="rich", add_completion=False,
    help="Ask questions about a software repository and get answers that cite the original "
         "code, PDFs and web pages. Also runs the A/B retrieval experiments.\n\n"
         "[bold]User mode[/]: inside any git repository, run [bold]dwb init[/] once, then "
         "[bold]dwb index[/] and [bold]dwb ask[/] / [bold]dwb chat[/].\n\n"
         "[bold]Research mode[/]: run from the design-wiki-bridge folder and name a repository "
         "with [bold]--repo[/]; also the experiment and benchmark commands.\n\n"
         "Guide: docs/usage.md",
    epilog=_examples('dwb init', 'dwb ask "How does routing work?"'),
)
index_app = typer.Typer(
    invoke_without_command=True,
    help="Build the search indexes used to answer. Inside a project: [bold]dwb index[/] "
         "builds everything .dwb/config.yaml lists. Research mode: [bold]dwb index code "
         "<repo>[/] and [bold]dwb index pdf <corpus>[/]. Ctrl-C stops; finished indexes "
         "are kept.",
    epilog=_examples("dwb index", "dwb index code flask"))
wiki_app = typer.Typer(
    no_args_is_help=True, help="Register OpenWiki snapshots (research mode).",
    epilog=_examples("dwb wiki snapshot flask --provider openai --model gpt-5.6-luna "
                     "--effort high --elapsed-s 600"))
exp_app = typer.Typer(
    no_args_is_help=True,
    help="Run and evaluate the paired A/B experiments (research mode, from the "
         "design-wiki-bridge folder).",
    epilog=_examples("dwb experiment check configs/manifests/flask-A.yaml "
                     "configs/manifests/flask-B.yaml",
                     "dwb experiment run configs/manifests/flask-A.yaml"))
bench_app = typer.Typer(
    no_args_is_help=True,
    help="Curate question sets: draft, validate, freeze, and check them (research mode).",
    epilog=_examples("dwb benchmark draft data/questions/selections/flask.yaml",
                     "dwb benchmark check data/questions/dev/flask.jsonl "
                     "data/questions/test/flask.jsonl"))
app.add_typer(index_app, name="index", rich_help_panel=USER)
app.add_typer(wiki_app, name="wiki", rich_help_panel=RESEARCH)
app.add_typer(exp_app, name="experiment", rich_help_panel=RESEARCH)
app.add_typer(bench_app, name="benchmark", rich_help_panel=RESEARCH)

WS = Workspace()  # research mode (design-wiki-bridge: configs/repos.yaml)


def _workspace(repo: str | None) -> tuple[Workspace, str]:
    """User mode inside a repo with .dwb/config.yaml; research mode with --repo."""
    from dwb.project import ProjectWorkspace, find_project

    if repo is None:
        root = find_project()
        if root is None:
            raise WorkspaceError(
                "this folder is not a dwb project. Run `dwb init` inside the repository you want "
                "to ask about. (Research mode: run from design-wiki-bridge with --repo <name>.)")
        pws = ProjectWorkspace(root)
        pws.require_indexed()
        return pws, pws.config.name
    WS.repo(repo)  # an unknown name fails here, listing the configured repositories
    return WS, repo


def _context(ws: Workspace, name: str) -> tuple[str, str, str]:
    """The R2 header parts for a repository: name, mode, commit."""
    from dwb.project import ProjectWorkspace

    mode = "user mode" if isinstance(ws, ProjectWorkspace) else "research mode"
    return name, mode, ws.repo(name).sha[:12]


def _open(*parts: str) -> None:
    """The opening header of a data command, shown only with human output."""
    if ui.human():
        ui.header(*parts)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"dwb {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True,
                                         help="More detail (per-stage timings). Repeatable.")] = 0,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only.")] = False,
    as_json: Annotated[bool, typer.Option(
        "--json", help="Print results as JSON, even at a terminal.")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="No colors or animations.")]
    = False,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True,
                                          help="Print the version and exit.")] = False,
) -> None:
    from dwb.project import load_env

    ui.configure(json=as_json, no_color=no_color, verbosity=-1 if quiet else verbose)
    setup_logging(-1 if quiet else verbose, console=ui.err)
    for p in load_env():
        LOG.debug("Loaded environment from %s", p)


@app.command(rich_help_panel=USER, epilog=_examples("dwb init", "dwb init --yes"))
def init(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Accept every default, no prompts.")]
    = False,
    force: Annotated[bool, typer.Option(help="Overwrite an existing .dwb/config.yaml.")] = False,
) -> None:
    """Set up the repository in the current folder: writes .dwb/config.yaml.

    Asks about models, PDF documents, web search, the OpenWiki source and API keys.
    Then run [bold]dwb index[/] and [bold]dwb chat[/].
    """
    from dwb.init_wizard import run_init

    run_init(Path.cwd(), ui.out(), yes=yes, force=force)


def _fail(msg: str, code: int = 2) -> typer.Exit:
    """A usage error found inside a command: print it and return the exit to raise."""
    ui.error(msg)
    return typer.Exit(code)


def _default_embedding() -> str:
    return WS.chat_config().models.embedding or "text-embedding-3-large"


def _embedding(model: str, fake: bool):
    if fake:
        from langchain_core.embeddings import DeterministicFakeEmbedding

        return DeterministicFakeEmbedding(size=64)
    from langchain_openai import OpenAIEmbeddings

    from dwb.manifest import MODEL_NET

    return OpenAIEmbeddings(model=model, **MODEL_NET)


# ------------------------------------------------------------------------- ask / chat

RepoArg = Annotated[str, typer.Argument(help="Repository name from configs/repos.yaml "
                                             "(see: dwb status).")]
RepoOpt = Annotated[str | None, typer.Option(
    "--repo", "-r", help="Research mode: a repository from configs/repos.yaml. Omit it inside a "
                         "project set up with `dwb init`.")]
WikiOpt = Annotated[bool | None, typer.Option(
    "--wiki/--no-wiki", help="Find repository evidence through the OpenWiki snapshot instead "
                             "of vector search. [dim]Default: configs/chat.yaml[/]")]
PdfOpt = Annotated[bool | None, typer.Option(
    "--pdf/--no-pdf", help="Use the repository's PDF corpus. [dim]Default: configs/chat.yaml[/]")]
WebOpt = Annotated[bool | None, typer.Option(
    "--web/--no-web", help="Use live web search (Tavily; needs TAVILY_API_KEY). "
                           "[dim]Default: configs/chat.yaml[/]")]
ModelOpt = Annotated[str | None, typer.Option(
    "--model", "-m", help="Chat model for answering and reranking. [dim]Default: "
                          "configs/chat.yaml[/]")]


def _settings(ws: Workspace, repo: str, wiki: bool | None, pdf: bool | None, web: bool | None,
              model: str | None):
    from dwb.chat import ChatSettings, Sources

    d = ws.chat_config().sources
    sources = Sources(wiki=d.wiki if wiki is None else wiki, pdf=d.pdf if pdf is None else pdf,
                      web=d.web if web is None else web)
    return ChatSettings(repo=repo, sources=sources, generator=model)


@app.command(rich_help_panel=ASK, epilog=_examples(
    'dwb ask "How does routing work?"   [dim](user mode)[/]',
    'dwb ask "How does Flask answer OPTIONS?" --repo flask   [dim](research mode)[/]'))
def ask(
    question: Annotated[str | None, typer.Argument(
        help="The question, in quotes. Asked for at a terminal when omitted.")] = None,
    repo: RepoOpt = None,
    wiki: WikiOpt = None, pdf: PdfOpt = None, web: WebOpt = None, model: ModelOpt = None,
    show_context: Annotated[bool, typer.Option(
        "--show-context", help="Also print every passage given to the model.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full trace as JSON.")] = False,
) -> None:
    """Answer one question about the repository, citing the code, PDF pages and web pages used.

    Inside a project set up with [bold]dwb init[/], no [bold]--repo[/] is needed; in research
    mode (design-wiki-bridge folder) name one with [bold]--repo[/].
    """
    from dwb.chat import ChatSession, print_answer

    usage = ('no question given. Usage: dwb ask "<question>" (inside a project), or '
             'dwb ask "<question>" --repo <name> (research mode).')
    question = (question or "").strip()
    if not question and not ui.interactive():
        raise _fail(usage)
    ws, name = _workspace(repo)
    as_json = as_json or ui.json_requested()
    if not as_json:
        ui.header(*_context(ws, name))
    if not question:
        question = typer.prompt("Question", err=True).strip()
        if not question:
            raise _fail(usage)

    async def go():
        async with ChatSession(ws, _settings(ws, name, wiki, pdf, web, model)) as s:
            return await s.ask(question)

    trace = asyncio.run(go())
    if as_json:
        print(trace.model_dump_json(indent=2))
    else:
        print_answer(trace, show_context=show_context, console=ui.out())
    if trace.errors:
        raise typer.Exit(1)


@app.command(rich_help_panel=ASK, epilog=_examples("dwb chat", "dwb chat --repo flask --wiki"))
def chat(repo: RepoOpt = None, wiki: WikiOpt = None, pdf: PdfOpt = None, web: WebOpt = None,
         model: ModelOpt = None) -> None:
    """Interactive session about the repository; switch sources with /wiki, /pdf, /web.

    Each question is answered on its own, from the evidence retrieved for it.
    """
    from dwb.chat import ChatSession, repl

    ws, name = _workspace(repo)

    async def go():
        async with ChatSession(ws, _settings(ws, name, wiki, pdf, web, model)) as s:
            hist = ws.history_path
            hist.parent.mkdir(parents=True, exist_ok=True)
            await repl(s, ui.out(), history=hist, context=_context(ws, name))

    asyncio.run(go())


_STATE_STYLE = {"ok": "ok", "on": "ok", "missing": "fail", "stale": "warn", "off": "secondary"}


def _status_table(title: str, rows: list[tuple[str, str, str]]) -> None:
    """The one `dwb status` table shape, used in both modes: Source, State, Detail."""
    ui.table(["Source", "State", "Detail"], [list(r) for r in rows], title=title,
             styles={1: lambda v: _STATE_STYLE.get(v, "")})


def _research_rows(r: dict) -> list[tuple[str, str, str]]:
    """A `Workspace.status` row as (source, state, detail) rows."""
    name = r["repo"]
    rows = [("checkout", "ok" if r["checkout"] else "missing",
             f"pinned {r['tag']}" if r["checkout"] else "clone it (configs/repos.yaml)"),
            ("code", "ok" if r["code_index"] else "missing",
             r["code_index"] or f"run dwb index code {name}")]
    if not r["pdf_corpus"]:
        rows.append(("pdf", "off", "no PDF corpus"))
    else:
        rows.append(("pdf", "ok" if r["pdf_index"] else "missing",
                     f"{r['pdf_corpus']}, {r['pdf_index'] or 'run dwb index pdf <corpus file>'}"))
    rows.append(("wiki", "ok" if r["wiki"] else "missing",
                 r["wiki"] or f"no snapshot (dwb wiki snapshot {name})"))
    return rows


@app.command(rich_help_panel=USER, epilog=_examples("dwb status",
                                                    "dwb status --embedding-model "
                                                    "text-embedding-3-small"))
def status(embedding_model: Annotated[str | None, typer.Option(
        help="Research mode: embedding model the indexes must match.")] = None) -> None:
    """Show which sources are ready: for this project, or for every research repository.

    Inside a project it reports that project; from the design-wiki-bridge folder (research
    mode) it reports every repository, and [bold]--embedding-model[/] picks the model the
    indexes must match.
    """
    from dwb.project import ProjectWorkspace, find_project

    root = find_project()
    if root is not None:
        pws = ProjectWorkspace(root)
        name = pws.config.name
        rows = pws.status_rows()
        _open(name, "user mode", pws.repo().sha[:12])

        def show(_):
            _status_table(f"{name} ({root})", rows)
            if any(st in ("missing", "stale") for _, st, _ in rows):
                ui.warning("Some sources are missing or stale.")
                ui.next_step("dwb index")
            else:
                ui.outcome(f"{name} is ready", "dwb chat")

        ui.emit({"name": name, "root": str(root),
                 "sources": [{"source": src, "state": st, "detail": d} for src, st, d in rows]},
                human=show)
        return
    if not WS.resolve(WS.repos_yaml).exists():
        raise _fail("not a dwb project (run `dwb init` here) and not the design-wiki-bridge "
                    "folder (configs/repos.yaml).")
    emb = embedding_model or _default_embedding()
    _open("all repositories", "research mode", f"indexes built with {emb}")
    rows = WS.status(emb)

    def show_research(rows):
        for i, r in enumerate(rows):
            if i:
                ui.out().print()
            _status_table(f"{r['repo']} ({r['tag']})", _research_rows(r))
        missing = [r["repo"] for r in rows if not (r["checkout"] and r["code_index"])]
        if missing:
            ui.warning(f"Not ready: {', '.join(missing)}.")
            ui.next_step(f"dwb index code {missing[0]}")
        else:
            ui.outcome(f"{len(rows)} repositories ready", "dwb chat --repo <name>")

    ui.emit(rows, human=show_research)


# ------------------------------------------------------------------------------ index


@index_app.callback(epilog=_examples("dwb index", "dwb index --no-generate-wiki"))
def index_all(
    ctx: typer.Context,
    generate_wiki: Annotated[bool | None, typer.Option(
        "--generate-wiki/--no-generate-wiki",
        help="Run OpenWiki when openwiki/ is missing or older than HEAD. "
             "[dim]Default: wiki.generate in .dwb/config.yaml[/]")] = None,
    fake: Annotated[bool, typer.Option(hidden=True)] = False,
) -> None:
    """Index everything the project's .dwb/config.yaml lists, for the HEAD commit.

    Ctrl-C stops it; indexes already finished are kept.
    """
    if ctx.invoked_subcommand is not None:
        return
    from dwb.project import ProjectWorkspace, find_project, head_sha, index_project

    root = find_project()
    if root is None:
        raise _fail("this folder is not a dwb project: run `dwb init` first. "
                    "(Research mode: dwb index code <repo> / dwb index pdf <corpus>.)")
    pws = ProjectWorkspace(root)
    name = pws.config.name
    _open(name, "user mode", head_sha(root)[:12])
    with ui.progress(STOP_INDEX) as on_progress:
        state = index_project(pws, embedding=_embedding("fake", True) if fake else None,
                              generate_wiki=generate_wiki, on_progress=on_progress,
                              wiki_output=ui.verbose())

    def show(st):
        ui.kv([("code", st.get("code_index_id")),
               ("pdf", st.get("pdf_index_id") or ("not indexed" if pws.config.pdfs else "off")),
               ("wiki", st.get("wiki_snapshot_id") or st.get("wiki_note")
                or ("not indexed" if pws.config.wiki.enabled else "off"))])
        ui.outcome(f"Indexed {name} at {st['sha'][:12]}", "dwb chat")

    ui.emit(state, human=show)


STOP_INDEX = "Ctrl-C stops; finished indexes are kept."


def _index_summary(info) -> None:
    ui.kv([("index", info.index_id + (" (reused)" if info.reused else "")),
           ("items", info.n_items), ("embedding", info.embedding_model)])


@index_app.command("code", rich_help_panel=RESEARCH, epilog=_examples(
    "dwb index code flask", "dwb index code flask --embedding-model text-embedding-3-small"))
def index_code(
    repo: RepoArg,
    embedding_model: Annotated[str | None, typer.Option(
        help="OpenAI embedding model. [dim]Default: configs/chat.yaml[/]")] = None,
    max_span_tokens: Annotated[int, typer.Option(help="Split code spans above this size.")]
    = 1500,
    path: Annotated[Path | None, typer.Option(hidden=True)] = None,
    sha: Annotated[str | None, typer.Option(hidden=True)] = None,
    root: Annotated[Path, typer.Option(hidden=True)] = Path("artifacts/indexes"),
    fake: Annotated[bool, typer.Option(hidden=True)] = False,
) -> None:
    """Chunk REPO's code at its pinned commit and embed it (needed for vector search).

    Ctrl-C stops it; indexes already finished are kept.
    """
    from dwb.repo import RepoReader
    from dwb.retrieval.chunking import ChunkingConfig
    from dwb.retrieval.code_index import build_code_index

    model = embedding_model or _default_embedding()
    if path:
        reader = RepoReader.from_dir(path, sha=sha or "fixture")
    else:
        r = WS.repo(repo)
        if not r.checkout_ok:
            raise WorkspaceError(f"checkout for {repo} not found at {r.checkout}")
        reader = RepoReader.from_git(r.checkout, r.sha)
    _open(repo, "research mode", reader.sha[:12])
    LOG.info("Indexing code for [bold]%s[/] at %s with %s", repo, reader.sha[:12], model)
    with ui.progress(STOP_INDEX) as on_progress:
        idx = build_code_index(root, reader, repo, _embedding(model, fake), model,
                               ChunkingConfig(max_span_tokens=max_span_tokens),
                               on_progress=on_progress)

    def show(info):
        _index_summary(info)
        ui.outcome(f"Indexed {repo} at {reader.sha[:12]}: {info.n_items} code spans",
                   f"dwb chat --repo {repo}")

    ui.emit(idx.info, human=show)


@index_app.command("pdf", rich_help_panel=RESEARCH,
                   epilog=_examples("dwb index pdf configs/pdfs/flask.yaml"))
def index_pdf(
    corpus: Annotated[Path, typer.Argument(help="PDF corpus file, e.g. configs/pdfs/flask.yaml",
                                           exists=True, dir_okay=False)],
    embedding_model: Annotated[str | None, typer.Option(
        help="OpenAI embedding model. [dim]Default: configs/chat.yaml[/]")] = None,
    base_dir: Annotated[Path, typer.Option(help="Folder the corpus' PDF paths are relative to.")]
    = Path("."),
    root: Annotated[Path, typer.Option(hidden=True)] = Path("artifacts/indexes"),
    fake: Annotated[bool, typer.Option(hidden=True)] = False,
) -> None:
    """Read the PDFs listed in CORPUS page by page and embed them.

    Ctrl-C stops it; indexes already finished are kept.
    """
    from dwb.retrieval.pdf_index import PdfCorpus, build_pdf_index

    model = embedding_model or _default_embedding()
    c = PdfCorpus.load(corpus)
    missing = [d.path for d in c.documents if not (base_dir / d.path).exists()]
    if missing:
        raise _fail(f"PDF files not found (relative to {base_dir}): {', '.join(missing)}")
    _open(c.corpus_id, "research mode")
    LOG.info("Indexing PDF corpus [bold]%s[/] (%d documents) with %s", c.corpus_id,
             len(c.documents), model)
    with ui.progress(STOP_INDEX) as on_progress:
        idx = build_pdf_index(root, c, base_dir, _embedding(model, fake), model,
                              on_progress=on_progress)

    def show(info):
        _index_summary(info)
        ui.outcome(f"Indexed {c.corpus_id}: {info.n_items} pages from {len(c.documents)} PDFs",
                   "dwb status")

    ui.emit(idx.info, human=show)


# ------------------------------------------------------------------------------- wiki


@wiki_app.command("snapshot", epilog=_examples(
    "dwb wiki snapshot flask --provider openai --model gpt-5.6-luna --effort high "
    "--elapsed-s 600"))
def wiki_snapshot(
    repo: RepoArg,
    provider: Annotated[str, typer.Option(help="Provider OpenWiki used, e.g. openai.")],
    model: Annotated[str, typer.Option(help="Model OpenWiki used, e.g. gpt-5.6-luna.")],
    effort: Annotated[str | None, typer.Option(help="Reasoning effort shown by OpenWiki.")]
    = None,
    version: Annotated[str | None, typer.Option(
        help="OpenWiki version. [dim]Default: read from the installed CLI[/]")] = None,
    elapsed_s: Annotated[float | None, typer.Option(
        help="Generation wall time in seconds (the 'real' value from /usr/bin/time).")] = None,
    cost_usd: Annotated[float | None, typer.Option(help="Generation cost, if reported.")] = None,
    root: Annotated[Path, typer.Option(hidden=True)] = Path("artifacts/wikis"),
) -> None:
    """Register the openwiki/ folder generated in REPO's checkout as a pinned snapshot.

    Generate it first, inside the checkout:
    OPENWIKI_TELEMETRY_DISABLED=1 OPENWIKI_PROVIDER=... OPENWIKI_MODEL_ID=... openwiki code --init
    """
    from dwb.retrieval.openwiki_client import openwiki_version, register_snapshot

    r = WS.repo(repo)
    _open(repo, "research mode", r.sha[:12])
    if version is None:
        version = openwiki_version() or "unknown"
    LOG.info("Registering the OpenWiki tree in %s (openwiki %s, %s/%s)", r.checkout, version,
             provider, model)
    snap = register_snapshot(r.checkout, repo, r.sha, root, provider=provider, model=model,
                             version=version, effort=effort, elapsed_s=elapsed_s,
                             cost_usd=cost_usd)
    LOG.info("Snapshot [bold]%s[/]: %d pages, %d claims", snap.snapshot_id, snap.pages,
             snap.claims)

    def show(sn):
        ui.kv([("snapshot", sn.snapshot_id), ("pages", sn.pages), ("claims", sn.claims),
               ("generator", f"openwiki {sn.version}, {sn.provider}/{sn.model}")])
        ui.outcome(f"Registered {sn.snapshot_id}", f"dwb chat --repo {repo} --wiki")

    ui.emit(snap, human=show)


# -------------------------------------------------------------------------- benchmark

RecordsArg = Annotated[Path, typer.Argument(help="Question records (JSONL), e.g. "
                                                 "data/questions/test/flask.jsonl",
                                            exists=True, dir_okay=False)]
PdfCorpusOpt = Annotated[Path | None, typer.Option(
    help="PDF corpus to check PDF spans against. [dim]Default: the repository's pdf_corpus in "
         "configs/chat.yaml[/]")]


def _problems(title: str, problems: list[str]) -> None:
    t = Table(title=title, show_header=False)
    t.add_column("problem")
    for p in problems:
        t.add_row(p)
    ui.err().print(t)


def _counts(counts: dict) -> None:
    ui.kv([(k, v) for k, v in counts.items()])


@bench_app.command("draft", epilog=_examples(
    "dwb benchmark draft data/questions/selections/flask.yaml"))
def benchmark_draft(
    selection: Annotated[Path, typer.Argument(help="Selection file, e.g. "
                                                   "data/questions/selections/flask.yaml",
                                              exists=True, dir_okay=False)],
    overwrite: Annotated[bool, typer.Option(help="Replace existing drafts files.")] = False,
) -> None:
    """Draft public question records from the SWE-QA rows a selection file includes.

    Writes data/questions/drafts/<repo>.public.jsonl and <repo>.excluded.jsonl. Annotate the
    drafts and copy them into data/questions/test/<repo>.jsonl.
    """
    import yaml

    from dwb import curation

    try:
        sel = yaml.safe_load(selection.read_text()) or {}
    except yaml.YAMLError:
        sel = {}
    if isinstance(sel, dict):
        _open(str(sel.get("repo") or selection.stem), "research mode",
              str(sel.get("repo_sha") or "")[:12])
    res = curation.draft(WS, selection, overwrite=overwrite)

    def show(d):
        ui.kv([("records", f"{d['n_records']} in {d['records']}"),
               ("excluded", f"{d['n_excluded']} in {d['exclusions']}")])
        ui.outcome(f"Drafted {d['n_records']} records; annotate them, then validate",
                   f"dwb benchmark validate {d['records']}")

    ui.emit({"records": str(res.records_path), "n_records": res.n_records,
             "exclusions": str(res.exclusions_path), "n_excluded": res.n_excluded}, human=show)


@bench_app.command("validate", epilog=_examples(
    "dwb benchmark validate data/questions/test/flask.jsonl"))
def benchmark_validate(records: RecordsArg, pdf_corpus: PdfCorpusOpt = None) -> None:
    """Check question records and report every problem; exits 1 when any is found.

    Checks the schema, duplicate IDs, the pinned SHA, code spans at the pinned commit, and PDF
    spans against the repository's PDF corpus.
    """
    from dwb import curation

    _open(records.name, "research mode")
    rep = curation.validate(WS, records, reader_for=curation.git_reader, pdf_corpus=pdf_corpus)
    if rep.problems:
        _problems(f"{len(rep.problems)} problems in {records}", rep.problems)

    def show(d):
        _counts(d["counts"])
        if d["ok"]:
            ui.outcome(f"{records} is valid", f"dwb benchmark freeze {records}")
        else:
            ui.error(f"{len(d['problems'])} problems in {records}",
                     "fix the records listed above, then validate again")

    ui.emit({"path": str(records), "ok": rep.ok, "counts": rep.counts,
             "problems": rep.problems}, human=show)
    if not rep.ok:
        raise typer.Exit(1)


@bench_app.command("freeze", epilog=_examples(
    "dwb benchmark freeze data/questions/test/flask.jsonl"))
def benchmark_freeze(records: RecordsArg, pdf_corpus: PdfCorpusOpt = None) -> None:
    """Freeze a validated test set by its hash.

    Validates the records and checks them against the repository's dev set, then writes
    <records>.sha256. Copy the printed hash into the manifests' question_set.hash.
    """
    from dwb import curation

    _open(records.name, "research mode")
    res = curation.freeze_set(WS, records, reader_for=curation.git_reader,
                              pdf_corpus=pdf_corpus)
    if not res.ok:
        _problems(f"Not frozen: {len(res.report.problems)} problems in {records}",
                  res.report.problems)
        raise typer.Exit(1)

    def show(d):
        ui.kv([("hash", d["hash"])])
        _counts(d["counts"])

    ui.emit({"path": str(records), "hash": res.digest, "counts": res.report.counts},
            human=show)
    # shown in both modes (as before): the hash has to be copied into the manifests
    ui.outcome(f"Frozen {records}. Set question_set.hash: {res.digest} in the manifests that "
               "use it.")


@bench_app.command("check", epilog=_examples(
    "dwb benchmark check data/questions/dev/flask.jsonl data/questions/test/flask.jsonl"))
def benchmark_check(
    dev: Annotated[Path, typer.Argument(help="Dev question records, e.g. "
                                             "data/questions/dev/flask.jsonl")],
    test: Annotated[Path, typer.Argument(help="Test question records, e.g. "
                                              "data/questions/test/flask.jsonl")],
) -> None:
    """Check that the dev and test sets are disjoint and a frozen test set is unchanged.

    Fails on a shared ID, question text, or SWE-QA row, or when the test file no longer
    matches its .sha256. A missing test file counts as empty.
    """
    from dwb import curation

    _open(f"{dev.name} vs {test.name}", "research mode")
    res = curation.check_sets(dev, test)
    for w in res.warnings:
        LOG.warning(w)
    if res.problems:
        _problems(f"{dev} vs {test}", res.problems)

    def show(d):
        if d["ok"]:
            ui.outcome(f"{dev} and {test} are disjoint and the test set is unchanged")
        else:
            ui.error(f"{len(d['problems'])} problems between {dev} and {test}",
                     "fix the overlaps listed above, then check again")

    ui.emit({"dev": str(dev), "test": str(test), "ok": res.ok,
             "problems": res.problems, "warnings": res.warnings}, human=show)
    if not res.ok:
        raise typer.Exit(1)


# ------------------------------------------------------------------------- experiment


@exp_app.command("run", epilog=_examples(
    "dwb experiment run configs/manifests/flask-A.yaml",
    "dwb experiment run configs/manifests/flask-B.yaml --web-mode replay --overwrite"))
def experiment_run(
    manifest: Annotated[Path, typer.Argument(help="Run manifest, e.g. "
                                                  "configs/manifests/flask-A.yaml",
                                             exists=True, dir_okay=False)],
    web_mode: Annotated[str | None, typer.Option(
        help="Override web.mode: record (live, saved), replay (saved only), live (latency; "
             "renames the run).")] = None,
    overwrite: Annotated[bool, typer.Option(help="Replace an existing run directory.")] = False,
    runs_dir: Annotated[Path, typer.Option(help="Where run directories go.")] = Path("runs"),
) -> None:
    """Run one condition of the experiment over its question set and save the traces.

    Ctrl-C stops it; traces already written are kept.
    """
    from dwb.manifest import load_manifest, require_runnable
    from dwb.runner import build_components, load_question_set, run_manifest

    if web_mode not in (None, "record", "replay", "live"):
        raise _fail("--web-mode must be record, replay or live")
    m = load_manifest(manifest)
    require_runnable(m)
    if web_mode:
        m = m.model_copy(update={"web": m.web.model_copy(update={"mode": web_mode})})
        if web_mode == "live":
            m = m.model_copy(update={"run_id": f"{m.run_id}-live"})
    base = Path(".")
    _open(m.repo, "research mode", m.repo_sha[:12], f"run {m.run_id}")
    load_question_set(m, base)  # before touching the checkout (B restores the wiki into it)
    comps = build_components(m, base_dir=base, index_root=WS.index_root,
                             tavily_root=WS.tavily_root, wiki_root=WS.wiki_root)
    try:
        with ui.progress("Ctrl-C stops; traces already written are kept.") as on_progress:
            summary = run_manifest(m, comps, base_dir=base, runs_dir=runs_dir,
                                   overwrite=overwrite, on_progress=on_progress)
    except FileExistsError as e:
        raise _fail(f"{e} (add --overwrite to replace it)") from None

    def show(sm):
        ui.kv([("questions", sm["questions"]), ("failed", sm["failed"]),
               ("degraded", sm["degraded"]), ("empty context", sm["empty_context"]),
               ("web triggered", sm["web_triggered"]),
               ("unresolved citation IDs", sm["unresolved_citation_ids"]),
               ("elapsed", f"{sm['elapsed_s']:.1f} s"), ("run", runs_dir / m.run_id)])
        run_dir = runs_dir / m.run_id
        pair = (f"{run_dir} runs/<B>" if m.condition == "A" else f"runs/<A> {run_dir}")
        nxt = f"dwb experiment evaluate {pair} --out reports/<name>"
        if sm["failed"]:
            ui.error(f"{sm['failed']} of {sm['questions']} questions failed",
                     f"see the errors in {run_dir}/traces.jsonl")
            ui.next_step(nxt)
        else:
            ui.outcome(f"Run {m.run_id} finished", nxt)

    ui.emit(summary, human=show)
    if summary["failed"]:
        raise typer.Exit(1)


@exp_app.command("check", epilog=_examples(
    "dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml"))
def experiment_check(
    a: Annotated[Path, typer.Argument(help="Condition A manifest.", exists=True)],
    b: Annotated[Path, typer.Argument(help="Condition B manifest.", exists=True)],
) -> None:
    """Check that an A/B manifest pair differs only in the repository branch."""
    from dwb.manifest import check_paired, load_manifest

    ma, mb = load_manifest(a), load_manifest(b)
    _open(ma.repo, "research mode", ma.repo_sha[:12], f"{ma.run_id} vs {mb.run_id}")
    res = check_paired(ma, mb, runnable=True)
    if ui.json_mode():
        for f in res.failures:
            LOG.error("Shared-stage difference: %s", f)
        for n in res.notes:
            LOG.info("Note: %s", n)
        LOG.info("Paired check %s", "[green]passed[/]" if res.ok else "[red]failed[/]")

    def show(d):
        rows = [["difference", f] for f in d["failures"]] + [["note", n] for n in d["notes"]]
        if rows:
            ui.table(["Kind", "Detail"], rows,
                     styles={0: lambda v: "fail" if v == "difference" else "secondary"})
        if d["ok"]:
            ui.outcome("Paired check passed: the manifests differ only in the repository "
                       "branch", f"dwb experiment run {a}")
        else:
            ui.error(f"Paired check failed: {len(d['failures'])} shared-stage differences",
                     "make the shared stages identical in both manifests")

    ui.emit(res.model_dump(), human=show)
    if not res.ok:
        raise typer.Exit(1)


@exp_app.command("evaluate", epilog=_examples(
    "dwb experiment evaluate runs/flask-A-test-001 runs/flask-B-test-001 --out reports/flask-test",
    "dwb experiment evaluate runs/flask-A-test-001 runs/flask-B-test-001 "
    "--out reports/flask-dev --no-judge"))
def experiment_evaluate(
    run_a: Annotated[Path, typer.Argument(help="Run directory of condition A.", exists=True)],
    run_b: Annotated[Path, typer.Argument(help="Run directory of condition B.", exists=True)],
    out_dir: Annotated[Path, typer.Option("--out", help="Report directory.")],
    config: Annotated[Path, typer.Option(help="Evaluation config.")] = Path("configs/eval.yaml"),
    no_judge: Annotated[bool, typer.Option("--no-judge", help="Recall and cost only; no LLM "
                                                              "judge calls.")] = False,
    deepeval: Annotated[bool, typer.Option(help="Also run DeepEval diagnostics.")] = False,
    allow_unpaired: Annotated[bool, typer.Option(
        help="Score runs that fail the A/B pairing check anyway (recorded in the report).")]
        = False,
) -> None:
    """Score two saved runs offline and write the paired report tables.

    Ctrl-C stops it; the report is written only at the end, so nothing is kept.
    """
    from dwb.eval.command import evaluate_runs

    _open(f"{run_a.name} vs {run_b.name}", "research mode")
    with ui.progress("Ctrl-C stops; the report is written only at the end.") as on_progress:
        res = evaluate_runs(run_a, run_b, out_dir, config_path=config, use_judge=not no_judge,
                            deepeval=deepeval or None, allow_unpaired=allow_unpaired,
                            on_progress=on_progress)

    def show(d):
        ui.kv([(k, v if not isinstance(v, (list, dict)) else (", ".join(map(str, v)) or "-"))
               for k, v in d.items()])
        ui.outcome(f"Report written to {out_dir}")

    ui.emit(res, human=show)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
