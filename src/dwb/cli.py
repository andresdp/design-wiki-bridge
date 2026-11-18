"""``dwb``: ask questions about a repository with cited answers, and run the experiments.

Run every command from the design-wiki-bridge folder: configuration (``configs/``), artifacts
(``artifacts/``) and API keys (``.env``) are resolved relative to it. See docs/usage.md.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
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
    from typer._click.exceptions import Abort, ClickException, Exit

    kinds: list[type[BaseException]] = [Exit, Abort, ClickException, typer.Exit, typer.Abort]
    try:
        import click

        kinds += [click.exceptions.Exit, click.exceptions.Abort, click.ClickException]
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
        except _control_flow():
            raise
        except KeyboardInterrupt:
            ui.stopped(ctx.meta.get(ui.STOP_KEY, {}))
            raise typer.Exit(130) from None
        except Exception as e:
            if _is_expected(e):
                ui.error(str(e), getattr(e, "fix", None))
                raise typer.Exit(2) from None
            ui.unexpected(e, verbose=(ctx.params.get("verbose") or 0) > 0)
            raise typer.Exit(1) from None


app = typer.Typer(
    name="dwb", cls=_App, no_args_is_help=True, rich_markup_mode="rich", add_completion=False,
    help="Ask questions about a software repository and get answers that cite the original "
         "code, PDFs and web pages. Also runs the A/B retrieval experiments.\n\n"
         "Run from the design-wiki-bridge folder. Guide: docs/usage.md",
)
index_app = typer.Typer(invoke_without_command=True,
                        help="Build the search indexes used to answer. Inside a project: "
                             "[bold]dwb index[/] builds everything .dwb/config.yaml lists.")
wiki_app = typer.Typer(no_args_is_help=True, help="Register OpenWiki snapshots.")
exp_app = typer.Typer(no_args_is_help=True,
                      help="Run and evaluate the paired A/B experiments (research workflow).")
app.add_typer(index_app, name="index")
app.add_typer(wiki_app, name="wiki")
bench_app = typer.Typer(no_args_is_help=True,
                        help="Curate question sets: draft, validate, freeze, and check them "
                             "(research workflow).")
app.add_typer(exp_app, name="experiment")
app.add_typer(bench_app, name="benchmark")

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
    return WS, repo


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

    ui.configure(json=as_json, no_color=no_color)
    setup_logging(-1 if quiet else verbose, console=ui.err())
    for p in load_env():
        LOG.debug("Loaded environment from %s", p)


@app.command()
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


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="The question, in quotes.")],
    repo: RepoOpt = None,
    wiki: WikiOpt = None, pdf: PdfOpt = None, web: WebOpt = None, model: ModelOpt = None,
    show_context: Annotated[bool, typer.Option(
        "--show-context", help="Also print every passage given to the model.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full trace as JSON.")] = False,
) -> None:
    """Answer one question about the repository, citing the code, PDF pages and web pages used.

    [dim]Examples:[/] dwb ask "How are requests routed?"   (inside a project set up with dwb init)
              dwb ask "How does Flask answer OPTIONS?" --repo flask   (research mode)
    """
    from dwb.chat import ChatSession, print_answer

    ws, name = _workspace(repo)

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


@app.command()
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
            await repl(s, ui.out(), history=hist)

    asyncio.run(go())


@app.command()
def status(embedding_model: Annotated[str | None, typer.Option(
        help="Research mode: embedding model the indexes must match.")] = None) -> None:
    """Show which sources are ready: for this project, or for every research repository."""
    from dwb.project import ProjectWorkspace, find_project

    out = ui.out()
    root = find_project()
    if root is not None:
        pws = ProjectWorkspace(root)
        t = Table(title=f"{pws.config.name} ({root})")
        for col in ("Source", "State", "Detail"):
            t.add_column(col)
        style = {"ok": "green", "on": "green", "missing": "red", "stale": "yellow", "off": "dim"}
        for src, state, detail in pws.status_rows():
            t.add_row(src, f"[{style.get(state, 'white')}]{state}[/]", detail)
        out.print(t)
        if not pws.state or any(s in ("missing", "stale") for _, s, _ in pws.status_rows()):
            out.print("Run [bold]dwb index[/] to build what is missing.")
        return
    if not WS.resolve(WS.repos_yaml).exists():
        raise _fail("not a dwb project (run `dwb init` here) and not the design-wiki-bridge "
                    "folder (configs/repos.yaml).")
    emb = embedding_model or _default_embedding()
    rows = WS.status(emb)

    def mark(v):
        return f"[green]✓[/] {v}" if v and v is not True else ("[green]✓[/]" if v else "[red]✗[/]")

    t = Table(title=f"Repositories (indexes built with {emb})")
    for col in ("Repo", "Version", "Checkout", "Code index", "PDF index", "Wiki snapshot"):
        t.add_column(col)
    for r in rows:
        t.add_row(r["repo"], r["tag"], mark(r["checkout"]), mark(r["code_index"]),
                  mark(r["pdf_index"]) if r["pdf_corpus"] else "[dim]no corpus[/]",
                  mark(r["wiki"]))
    out.print(t)
    missing = [r for r in rows if not (r["checkout"] and r["code_index"])]
    for r in missing:
        out.print(f"[yellow]{r['repo']}[/]: run [bold]dwb index code {r['repo']}[/] "
                  "(and clone the checkout if it is missing)")


# ------------------------------------------------------------------------------ index


@index_app.callback()
def index_all(
    ctx: typer.Context,
    generate_wiki: Annotated[bool | None, typer.Option(
        "--generate-wiki/--no-generate-wiki",
        help="Run OpenWiki when openwiki/ is missing or older than HEAD. "
             "[dim]Default: wiki.generate in .dwb/config.yaml[/]")] = None,
    fake: Annotated[bool, typer.Option(hidden=True)] = False,
) -> None:
    """Index everything the project's .dwb/config.yaml lists, for the HEAD commit."""
    if ctx.invoked_subcommand is not None:
        return
    from dwb.project import ProjectWorkspace, find_project, index_project

    root = find_project()
    if root is None:
        raise _fail("this folder is not a dwb project: run `dwb init` first. "
                    "(Research mode: dwb index code <repo> / dwb index pdf <corpus>.)")
    pws = ProjectWorkspace(root)
    state = index_project(pws, embedding=_embedding("fake", True) if fake else None,
                          generate_wiki=generate_wiki)
    ui.out().print(f"[green]Indexed[/] {pws.config.name} at {state['sha'][:12]}: code"
              + (", PDFs" if state.get("pdf_index_id") else "")
              + (", wiki" if state.get("wiki_snapshot_id") else "")
              + ". Next: [bold]dwb chat[/]")


@index_app.command("code")
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
    """Chunk REPO's code at its pinned commit and embed it (needed for vector search)."""
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
    LOG.info("Indexing code for [bold]%s[/] at %s with %s", repo, reader.sha[:12], model)
    idx = build_code_index(root, reader, repo, _embedding(model, fake), model,
                           ChunkingConfig(max_span_tokens=max_span_tokens))
    print(json.dumps(asdict(idx.info), indent=2))


@index_app.command("pdf")
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
    """Read the PDFs listed in CORPUS page by page and embed them."""
    from dwb.retrieval.pdf_index import PdfCorpus, build_pdf_index

    model = embedding_model or _default_embedding()
    c = PdfCorpus.load(corpus)
    missing = [d.path for d in c.documents if not (base_dir / d.path).exists()]
    if missing:
        raise _fail(f"PDF files not found (relative to {base_dir}): {', '.join(missing)}")
    LOG.info("Indexing PDF corpus [bold]%s[/] (%d documents) with %s", c.corpus_id,
             len(c.documents), model)
    idx = build_pdf_index(root, c, base_dir, _embedding(model, fake), model)
    print(json.dumps(asdict(idx.info), indent=2))


# ------------------------------------------------------------------------------- wiki


@wiki_app.command("snapshot")
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
    if version is None:
        version = openwiki_version() or "unknown"
    LOG.info("Registering the OpenWiki tree in %s (openwiki %s, %s/%s)", r.checkout, version,
             provider, model)
    snap = register_snapshot(r.checkout, repo, r.sha, root, provider=provider, model=model,
                             version=version, effort=effort, elapsed_s=elapsed_s,
                             cost_usd=cost_usd)
    LOG.info("Snapshot [bold]%s[/]: %d pages, %d claims", snap.snapshot_id, snap.pages,
             snap.claims)
    print(snap.model_dump_json(indent=2))


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


@bench_app.command("draft")
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
    from dwb import curation

    res = curation.draft(WS, selection, overwrite=overwrite)
    print(json.dumps({"records": str(res.records_path), "n_records": res.n_records,
                      "exclusions": str(res.exclusions_path),
                      "n_excluded": res.n_excluded}, indent=2))


@bench_app.command("validate")
def benchmark_validate(records: RecordsArg, pdf_corpus: PdfCorpusOpt = None) -> None:
    """Check question records and report every problem; exits 1 when any is found.

    Checks the schema, duplicate IDs, the pinned SHA, code spans at the pinned commit, and PDF
    spans against the repository's PDF corpus.
    """
    from dwb import curation

    rep = curation.validate(WS, records, reader_for=curation.git_reader, pdf_corpus=pdf_corpus)
    if rep.problems:
        _problems(f"{len(rep.problems)} problems in {records}", rep.problems)
    print(json.dumps({"path": str(records), "ok": rep.ok, "counts": rep.counts,
                      "problems": rep.problems}, indent=2))
    if not rep.ok:
        raise typer.Exit(1)


@bench_app.command("freeze")
def benchmark_freeze(records: RecordsArg, pdf_corpus: PdfCorpusOpt = None) -> None:
    """Freeze a validated test set by its hash.

    Validates the records and checks them against the repository's dev set, then writes
    <records>.sha256. Copy the printed hash into the manifests' question_set.hash.
    """
    from dwb import curation

    res = curation.freeze_set(WS, records, reader_for=curation.git_reader,
                              pdf_corpus=pdf_corpus)
    if not res.ok:
        _problems(f"Not frozen: {len(res.report.problems)} problems in {records}",
                  res.report.problems)
        raise typer.Exit(1)
    ui.err().print(f"[green]Frozen.[/] Set question_set.hash: {res.digest} in the manifests that "
              f"use {records}.")
    print(json.dumps({"path": str(records), "hash": res.digest,
                      "counts": res.report.counts}, indent=2))


@bench_app.command("check")
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

    res = curation.check_sets(dev, test)
    for w in res.warnings:
        LOG.warning(w)
    if res.problems:
        _problems(f"{dev} vs {test}", res.problems)
    print(json.dumps({"dev": str(dev), "test": str(test), "ok": res.ok,
                      "problems": res.problems, "warnings": res.warnings}, indent=2))
    if not res.ok:
        raise typer.Exit(1)


# ------------------------------------------------------------------------- experiment


@exp_app.command("run")
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
    """Run one condition of the experiment over its question set and save the traces."""
    from dwb.manifest import load_manifest
    from dwb.runner import build_components, load_question_set, run_manifest

    if web_mode not in (None, "record", "replay", "live"):
        raise _fail("--web-mode must be record, replay or live")
    m = load_manifest(manifest)
    if web_mode:
        m = m.model_copy(update={"web": m.web.model_copy(update={"mode": web_mode})})
        if web_mode == "live":
            m = m.model_copy(update={"run_id": f"{m.run_id}-live"})
    base = Path(".")
    load_question_set(m, base)  # before touching the checkout (B restores the wiki into it)
    comps = build_components(m, base_dir=base, index_root=WS.index_root,
                             tavily_root=WS.tavily_root, wiki_root=WS.wiki_root)
    try:
        summary = run_manifest(m, comps, base_dir=base, runs_dir=runs_dir, overwrite=overwrite)
    except FileExistsError as e:
        raise _fail(f"{e} (add --overwrite to replace it)") from None
    print(json.dumps(summary, indent=2))
    if summary["failed"]:
        raise typer.Exit(1)


@exp_app.command("check")
def experiment_check(
    a: Annotated[Path, typer.Argument(help="Condition A manifest.", exists=True)],
    b: Annotated[Path, typer.Argument(help="Condition B manifest.", exists=True)],
) -> None:
    """Check that an A/B manifest pair differs only in the repository branch."""
    from dwb.manifest import check_paired, load_manifest

    res = check_paired(load_manifest(a), load_manifest(b))
    for f in res.failures:
        LOG.error("Shared-stage difference: %s", f)
    for n in res.notes:
        LOG.info("Note: %s", n)
    LOG.info("Paired check %s", "[green]passed[/]" if res.ok else "[red]failed[/]")
    print(json.dumps(res.model_dump(), indent=2))
    if not res.ok:
        raise typer.Exit(1)


@exp_app.command("evaluate")
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
    """Score two saved runs offline and write the paired report tables."""
    from dwb.eval.command import evaluate_runs

    res = evaluate_runs(run_a, run_b, out_dir, config_path=config, use_judge=not no_judge,
                        deepeval=deepeval or None, allow_unpaired=allow_unpaired)
    print(json.dumps(res, indent=2))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
