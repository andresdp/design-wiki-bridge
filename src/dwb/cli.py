"""``dwb``: ask questions about a repository with cited answers, and run the experiments.

Run every command from the design-wiki-bridge folder: configuration (``configs/``), artifacts
(``artifacts/``) and API keys (``.env``) are resolved relative to it. See docs/usage.md.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dwb.log import get_logger, setup_logging
from dwb.workspace import Workspace, WorkspaceError

LOG = get_logger("cli")
out = Console()  # results (stdout)
err = Console(stderr=True)  # messages

app = typer.Typer(
    name="dwb", no_args_is_help=True, rich_markup_mode="rich", add_completion=False,
    help="Ask questions about a software repository and get answers that cite the original "
         "code, PDFs and web pages. Also runs the A/B retrieval experiments.\n\n"
         "Run from the design-wiki-bridge folder. Guide: docs/usage.md",
)
index_app = typer.Typer(no_args_is_help=True, help="Build the search indexes used to answer.")
wiki_app = typer.Typer(no_args_is_help=True, help="Register OpenWiki snapshots.")
exp_app = typer.Typer(no_args_is_help=True,
                      help="Run and evaluate the paired A/B experiments (research workflow).")
app.add_typer(index_app, name="index")
app.add_typer(wiki_app, name="wiki")
app.add_typer(exp_app, name="experiment")

WS = Workspace()


def _load_dotenv() -> bool:
    """Load API keys from ./.env (gitignored). Variables already set in the shell win."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    return load_dotenv(Path(".env"), override=False)


@app.callback()
def _main(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True,
                                         help="More detail (per-stage timings). Repeatable.")] = 0,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only.")] = False,
) -> None:
    setup_logging(-1 if quiet else verbose)
    if _load_dotenv():
        LOG.debug("Loaded environment from .env")


def _fail(msg: str, code: int = 2) -> typer.Exit:
    err.print(f"[red]Error:[/] {msg}")
    return typer.Exit(code)


def _default_embedding() -> str:
    return (WS.chat_config().get("models") or {}).get("embedding", "text-embedding-3-large")


def _embedding(model: str, fake: bool):
    if fake:
        from langchain_core.embeddings import DeterministicFakeEmbedding

        return DeterministicFakeEmbedding(size=64)
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=model)


# ------------------------------------------------------------------------- ask / chat

RepoArg = Annotated[str, typer.Argument(help="Repository name from configs/repos.yaml "
                                             "(see: dwb status).")]
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


def _settings(repo: str, wiki: bool | None, pdf: bool | None, web: bool | None,
              model: str | None):
    from dwb.chat import ChatSettings, Sources

    d = WS.chat_config().get("sources") or {}
    sources = Sources(wiki=d.get("wiki", False) if wiki is None else wiki,
                      pdf=d.get("pdf", True) if pdf is None else pdf,
                      web=d.get("web", False) if web is None else web)
    return ChatSettings(repo=repo, sources=sources, generator=model)


@app.command()
def ask(
    repo: RepoArg,
    question: Annotated[str, typer.Argument(help="The question, in quotes.")],
    wiki: WikiOpt = None, pdf: PdfOpt = None, web: WebOpt = None, model: ModelOpt = None,
    show_context: Annotated[bool, typer.Option(
        "--show-context", help="Also print every passage given to the model.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full trace as JSON.")] = False,
) -> None:
    """Answer one question about REPO, citing the code, PDF pages and web pages it used.

    [dim]Example:[/] dwb ask flask "How does Flask answer an OPTIONS request?" --pdf
    """
    from dwb.chat import ChatSession, render_answer

    async def go():
        async with ChatSession(WS, _settings(repo, wiki, pdf, web, model)) as s:
            return await s.ask(question)

    try:
        trace = asyncio.run(go())
    except WorkspaceError as e:
        raise _fail(str(e)) from None
    if as_json:
        print(trace.model_dump_json(indent=2))
    else:
        render_answer(trace, show_context=show_context, console=out)
    if trace.errors:
        raise typer.Exit(1)


@app.command()
def chat(repo: RepoArg, wiki: WikiOpt = None, pdf: PdfOpt = None, web: WebOpt = None,
         model: ModelOpt = None) -> None:
    """Interactive session: ask questions about REPO, switch sources with /wiki, /pdf, /web.

    Each question is answered on its own, from the evidence retrieved for it.
    """
    from dwb.chat import ChatSession, repl

    async def go():
        async with ChatSession(WS, _settings(repo, wiki, pdf, web, model)) as s:
            hist = WS._p(Path("artifacts/chat_history"))
            hist.parent.mkdir(parents=True, exist_ok=True)
            await repl(s, out, history=hist)

    try:
        asyncio.run(go())
    except WorkspaceError as e:
        raise _fail(str(e)) from None


@app.command()
def status(embedding_model: Annotated[str | None, typer.Option(
        help="Embedding model the indexes must match. [dim]Default: configs/chat.yaml[/]")]
           = None) -> None:
    """Show the configured repositories and which sources are ready for each."""
    emb = embedding_model or _default_embedding()
    try:
        rows = WS.status(emb)
    except WorkspaceError as e:
        raise _fail(str(e)) from None

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
    try:
        if path:
            reader = RepoReader.from_dir(path, sha=sha or "fixture")
        else:
            r = WS.repo(repo)
            if not r.checkout_ok:
                raise WorkspaceError(f"checkout for {repo} not found at {r.checkout}")
            reader = RepoReader.from_git(r.checkout, r.sha)
    except WorkspaceError as e:
        raise _fail(str(e)) from None
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
    from dwb.retrieval.openwiki_client import OpenWikiError, register_snapshot

    try:
        r = WS.repo(repo)
    except WorkspaceError as e:
        raise _fail(str(e)) from None
    if version is None:
        exe = shutil.which("openwiki")
        pkg = Path(exe).resolve().parents[2] / "package.json" if exe else None
        version = json.loads(pkg.read_text())["version"] if pkg and pkg.exists() else "unknown"
    LOG.info("Registering the OpenWiki tree in %s (openwiki %s, %s/%s)", r.checkout, version,
             provider, model)
    try:
        snap = register_snapshot(r.checkout, repo, r.sha, root, provider=provider, model=model,
                                 version=version, effort=effort, elapsed_s=elapsed_s,
                                 cost_usd=cost_usd)
    except OpenWikiError as e:
        raise _fail(str(e)) from None
    LOG.info("Snapshot [bold]%s[/]: %d pages, %d claims", snap.snapshot_id, snap.pages,
             snap.claims)
    print(snap.model_dump_json(indent=2))


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
    from dwb.runner import build_components, run_manifest

    if web_mode not in (None, "record", "replay", "live"):
        raise _fail("--web-mode must be record, replay or live")
    m = load_manifest(manifest)
    if web_mode:
        m = m.model_copy(update={"web": m.web.model_copy(update={"mode": web_mode})})
        if web_mode == "live":
            m = m.model_copy(update={"run_id": f"{m.run_id}-live"})
    base = Path(".")
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
) -> None:
    """Score two saved runs offline and write the paired report tables."""
    from dwb.eval.command import evaluate_runs

    res = evaluate_runs(run_a, run_b, out_dir, config_path=config, use_judge=not no_judge,
                        deepeval=deepeval or None)
    print(json.dumps(res, indent=2))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
