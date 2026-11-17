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
index_app = typer.Typer(invoke_without_command=True,
                        help="Build the search indexes used to answer. Inside a project: "
                             "[bold]dwb index[/] builds everything .dwb/config.yaml lists.")
wiki_app = typer.Typer(no_args_is_help=True, help="Register OpenWiki snapshots.")
exp_app = typer.Typer(no_args_is_help=True,
                      help="Run and evaluate the paired A/B experiments (research workflow).")
app.add_typer(index_app, name="index")
app.add_typer(wiki_app, name="wiki")
app.add_typer(exp_app, name="experiment")

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


@app.callback()
def _main(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True,
                                         help="More detail (per-stage timings). Repeatable.")] = 0,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only.")] = False,
) -> None:
    from dwb.project import load_env

    setup_logging(-1 if quiet else verbose)
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

    try:
        run_init(Path.cwd(), out, yes=yes, force=force)
    except WorkspaceError as e:
        raise _fail(str(e)) from None


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

    d = ws.chat_config().get("sources") or {}
    sources = Sources(wiki=d.get("wiki", False) if wiki is None else wiki,
                      pdf=d.get("pdf", True) if pdf is None else pdf,
                      web=d.get("web", False) if web is None else web)
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
    from dwb.chat import ChatSession, render_answer

    try:
        ws, name = _workspace(repo)

        async def go():
            async with ChatSession(ws, _settings(ws, name, wiki, pdf, web, model)) as s:
                return await s.ask(question)

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
def chat(repo: RepoOpt = None, wiki: WikiOpt = None, pdf: PdfOpt = None, web: WebOpt = None,
         model: ModelOpt = None) -> None:
    """Interactive session about the repository; switch sources with /wiki, /pdf, /web.

    Each question is answered on its own, from the evidence retrieved for it.
    """
    from dwb.chat import ChatSession, repl

    try:
        ws, name = _workspace(repo)

        async def go():
            async with ChatSession(ws, _settings(ws, name, wiki, pdf, web, model)) as s:
                hist = getattr(ws, "dir", None)
                hist = (hist / "chat_history") if hist else ws._p(Path("artifacts/chat_history"))
                hist.parent.mkdir(parents=True, exist_ok=True)
                await repl(s, out, history=hist)

        asyncio.run(go())
    except WorkspaceError as e:
        raise _fail(str(e)) from None


@app.command()
def status(embedding_model: Annotated[str | None, typer.Option(
        help="Research mode: embedding model the indexes must match.")] = None) -> None:
    """Show which sources are ready: for this project, or for every research repository."""
    from dwb.project import ProjectWorkspace, find_project

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
    if not WS._p(WS.repos_yaml).exists():
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
    try:
        state = index_project(pws, embedding=_embedding("fake", True) if fake else None,
                              generate_wiki=generate_wiki)
    except WorkspaceError as e:
        raise _fail(str(e)) from None
    out.print(f"[green]Indexed[/] {pws.config.name} at {state['sha'][:12]}: code"
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
