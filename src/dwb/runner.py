"""Manifest-driven batch runner (R10)."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dwb.benchmark.freeze import file_hash
from dwb.benchmark.schemas import QuestionRecord, load_records
from dwb.fatal import FatalAPIError, raise_if_fatal
from dwb.graph import Components, build_graph, query_text, trace_from_state
from dwb.log import get_logger
from dwb.manifest import MODEL_NET, USES_CODE_INDEX, USES_WIKI, RunManifest, require_runnable
from dwb.progress import OnProgress, report
from dwb.trace import RunDir, StageRecord, TraceRecord

LOG = get_logger("run")


class QuestionSetError(ValueError):
    pass


def load_question_set(m: RunManifest, base_dir: Path) -> list[QuestionRecord]:
    path = base_dir / m.question_set.path
    if m.question_set.hash is None:
        if "/test/" in f"/{m.question_set.path}":
            raise QuestionSetError(f"test set {path} is not frozen (question_set.hash is null)")
    else:
        actual = file_hash(path)
        if actual != m.question_set.hash:
            raise QuestionSetError(
                f"question set {path} hash {actual} != manifest {m.question_set.hash}"
            )
    records = load_records(path)
    wrong = [r.id for r in records if r.repo_sha != m.repo_sha]
    if wrong:
        raise QuestionSetError(f"records pinned to a different SHA than the manifest: {wrong}")
    return records


class RunStopped(RuntimeError):
    """The run stopped on an error no retry fixes (``dwb.fatal``); the question in progress
    was not saved. ``summary`` is the partial run summary that was written."""

    def __init__(self, message: str, summary: dict[str, Any]):
        super().__init__(message)
        self.summary = summary


async def run_questions(
    m: RunManifest,
    components: Components,
    questions: list[QuestionRecord],
    run_dir: RunDir,
    *,
    run_stages: list[StageRecord] | None = None,
    on_progress: OnProgress | None = None,
    kept: list[TraceRecord] | None = None,
) -> dict[str, Any]:
    """Answer every question and write the traces and the run summary. ``on_progress`` gets a
    ``run`` start (``total`` questions, ``data["traces"]`` the traces file), one step per
    question once its trace is written (``outcome`` ok/failed/degraded/empty, ``data`` the
    ``TraceRecord`` plus the running failed/degraded/empty_context counts) and a done event
    carrying the summary.

    ``kept``: traces of a resumed run (``RunDir.resume``), failed ones included; those
    questions are not run again and count in the summary. An out-of-credit or bad-key error
    stops the run with ``RunStopped`` after writing a partial summary (``stopped``)."""
    # record runs capture web for every eligible item, so a replay of either condition
    # finds it whatever that condition's gate verdict
    graph = build_graph(components, m, capture_all_web=m.web.mode == "record")
    t_run = time.perf_counter()
    run_stages = list(run_stages or [])
    kept = list(kept or [])
    ids = {q.id for q in questions}
    stray = [t.question_id for t in kept if t.question_id not in ids]
    if stray:
        raise QuestionSetError(f"resumed traces for questions not in the set: {stray}")
    done_ids = {t.question_id for t in kept}
    todo = [q for q in questions if q.id not in done_ids]
    if kept:
        LOG.info("Resuming %s: %d of %d questions already done, %d to run", m.run_id,
                 len(kept), len(questions), len(todo))
    LOG.debug("Run [bold]%s[/]: condition %s, %s @ %s, %d questions → %s", m.run_id,
              m.condition, m.repo, m.repo_sha[:12], len(questions), run_dir.root)
    report(on_progress, "run", "start", total=len(todo), label=m.run_id,
           data={"traces": run_dir.traces_path, "run_dir": run_dir.root})
    previous = run_dir.read_summary() if kept else None
    resumes = list((previous or {}).get("resumes", []))
    if kept or (previous and previous.get("stopped")):
        resumes.append({"at": datetime.now(UTC).isoformat(), "kept": len(kept)})
    new: list[TraceRecord] = []
    stopped = None
    async with AsyncExitStack() as stack:
        startup = await start_wiki(stack, components)  # one MCP session for the whole run
        if startup is not None:
            run_stages.append(startup)
        try:
            await _run_all(graph, m, todo, run_dir, on_progress, new)
        except FatalAPIError as e:
            stopped = str(e)
    records = kept + new
    counts = run_counts(records)
    summary = {
        "run_id": m.run_id,
        "condition": m.condition,
        "questions": len(questions),
        "completed": len(records),
        "failed": counts["failed"],
        "degraded": counts["degraded"],
        "empty_context": counts["empty_context"],
        "web_triggered": counts["web_triggered"],
        # record runs: web captures that failed, so replays of those questions may miss
        "web_capture_failed": counts["web_capture_failed"],
        "unresolved_citation_ids": counts["unresolved_citation_ids"],
        "elapsed_s": round(time.perf_counter() - t_run, 3),  # this session only
        "run_stages": [s.model_dump() for s in run_stages],
    }
    if resumes:
        summary["resumes"] = resumes
    if stopped:
        summary["stopped"] = stopped
    run_dir.write_summary(summary)
    if stopped:
        LOG.error("Run stopped after %d of %d questions: %s", len(records), len(questions),
                  stopped)
        raise RunStopped(stopped, summary)
    if counts["web_capture_failed"]:
        LOG.warning("%d web captures failed in this record run; replays of those questions "
                    "will miss if their gate asks for the web. Rerun with --web-mode record.",
                    counts["web_capture_failed"])
    style = "red" if counts["failed"] else "green"
    LOG.debug("[%s]Done[/]: %d questions, %d failed, %d degraded, %d empty context, %d with web, "
              "%d unresolved citation IDs, %.1f s. Traces: %s", style, len(questions),
              counts["failed"], counts["degraded"], counts["empty_context"],
              counts["web_triggered"], counts["unresolved_citation_ids"], summary["elapsed_s"],
              run_dir.traces_path)
    report(on_progress, "run", "done", done=len(todo), total=len(todo),
           label=m.run_id, elapsed_s=summary["elapsed_s"], data=summary)
    return summary


async def start_wiki(stack: AsyncExitStack, components: Components) -> StageRecord | None:
    """Enter the OpenWiki MCP session (if the components have one) on ``stack``. Returns its
    startup stage for the run summary."""
    wiki = components.wiki
    if wiki is None or not hasattr(type(wiki), "__aenter__"):
        return None
    LOG.info("Starting the OpenWiki MCP server")
    await stack.enter_async_context(wiki)
    stage = getattr(wiki, "startup_stage", None)
    if stage is not None:
        LOG.info("OpenWiki MCP server ready in %.0f ms", stage.latency_ms)
    return stage


def run_counts(records: list[TraceRecord]) -> dict[str, int]:
    return {
        "failed": sum(bool(r.errors) for r in records),
        "degraded": sum(bool(r.degraded) for r in records),
        "empty_context": sum(bool(r.empty_context) for r in records),
        "web_triggered": sum(bool(r.web_triggered) for r in records),
        "unresolved_citation_ids": sum(int(r.validation.get("n_unresolved_ids", 0) or 0)
                                       for r in records),
        "web_capture_failed": sum(any(s.name == "web_retrieve" and s.extra.get("used") is False
                                      and s.extra.get("error") for s in r.stages)
                                  for r in records),
    }


async def _run_all(
    graph, m: RunManifest, questions: list[QuestionRecord], run_dir: RunDir,
    on_progress: OnProgress | None, out: list[TraceRecord],
) -> None:
    """Run ``questions`` in order, appending each trace to the run and to ``out``. A fatal
    provider error (``dwb.fatal``) raises ``FatalAPIError`` before that question is saved."""
    total = len(questions)
    with run_dir.writer() as w:
        for i, q in enumerate(questions, start=1):
            LOG.debug("[cyan][%d/%d][/] %s: %s", i, total, q.id, _short(q.question))
            t0 = time.perf_counter()
            try:
                state = await graph.ainvoke({"question": q})
                rec = trace_from_state(state, m)
            except Exception as e:
                raise_if_fatal(e)
                rec = TraceRecord(question_id=q.id, run_id=m.run_id, condition=m.condition,
                                  errors=[f"{type(e).__name__}: {e}"])
            elapsed = time.perf_counter() - t0
            _log_trace(rec, elapsed)
            w.append(rec)
            out.append(rec)
            c = run_counts(out)
            report(on_progress, "run", "step", done=i, total=total, label=q.id,
                   outcome=outcome(rec), elapsed_s=elapsed,
                   data={"trace": rec, "failed": c["failed"], "degraded": c["degraded"],
                         "empty_context": c["empty_context"]})


def outcome(rec: TraceRecord) -> str:
    """One word for a question's result: failed, empty (no context), degraded, or ok."""
    if rec.errors:
        return "failed"
    if rec.empty_context:
        return "empty"
    if rec.degraded:
        return "degraded"
    return "ok"


def _short(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _log_trace(rec: TraceRecord, elapsed: float) -> None:
    for s in rec.stages:
        tokens = f"  {s.input_tokens}+{s.output_tokens} tok" if s.input_tokens or s.output_tokens \
            else ""
        LOG.debug("    %-17s %7.0f ms  n=%-3d%s", s.name, s.latency_ms, s.count, tokens)
    if rec.errors:
        LOG.warning("    [red]✗ failed[/] after %.1f s: %s", elapsed, _short(rec.errors[0], 200))
        return
    for w in rec.warnings:
        LOG.warning("    [yellow]degraded[/]: %s", _short(w, 200))
    if rec.empty_context:
        LOG.warning("    [yellow]∅ empty context[/] (no candidates from the enabled branches), "
                    "%.1f s", elapsed)
        return
    n_unres = rec.validation.get("n_unresolved_ids", 0)
    cited = sum(1 for c in rec.claims if c.evidence_ids)
    extra = []
    if rec.wiki_section_refs:
        extra.append(f"{len(rec.wiki_section_refs)} wiki hits")
    if rec.unresolved_refs:
        extra.append(f"{len(rec.unresolved_refs)} unresolved refs")
    if rec.web_triggered:
        extra.append("web")
    n_cand = sum(len(rec.candidates.get(k, [])) for k in ("repo", "pdf", "web"))
    LOG.debug("    [green]✓[/] %d candidates → %d in context → %d claim(s) (%d cited, %s "
              "unresolved IDs)%s, %.1f s", n_cand, len(rec.final_context_ids), len(rec.claims),
              cited,
              f"[red]{n_unres}[/]" if n_unres else "0",
              f" [{', '.join(extra)}]" if extra else "", elapsed)


def build_components(
    m: RunManifest,
    *,
    base_dir: Path,
    index_root: Path,
    tavily_root: Path,
    wiki_root: Path = Path("artifacts/wikis"),
    openwiki_bin: str | None = None,
    restore_wiki: bool = True,
) -> Components:
    """Real components for a manifest: Chroma retrievers, Tavily store, OpenAI models."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    from dwb.generate import Generator
    from dwb.rerank import LLMListwiseReranker
    from dwb.retrieval.code_index import CodeRetriever
    from dwb.retrieval.pdf_index import PdfRetriever
    from dwb.retrieval.vector_store import VectorIndex
    from dwb.web.tavily_store import TavilyStore

    LOG.info("Models: generator %s, reranker %s, embeddings %s (temperature %s)",
             m.models.generator, m.models.reranker, m.models.embedding, m.models.temperature)
    net = MODEL_NET
    emb = OpenAIEmbeddings(model=m.models.embedding, **net)
    gen_llm = ChatOpenAI(model=m.models.generator, temperature=m.models.temperature, **net)
    rr_llm = ChatOpenAI(model=m.models.reranker, temperature=m.models.temperature, **net)
    c = Components(
        reranker=LLMListwiseReranker(rr_llm, pool_cap=m.budgets.rerank_pool,
                                     top_n=m.budgets.rerank_top_n, model_id=m.models.reranker),
        generator=Generator(gen_llm, base_dir / m.prompts["generate"].path,
                            model_id=m.models.generator),
    )
    if m.pdf_enabled:
        LOG.info("PDF index: %s", m.indexes.pdf_index_id)
        c.pdf_retriever = PdfRetriever(VectorIndex.open(index_root, m.indexes.pdf_index_id, emb))
    if m.web.enabled:
        LOG.info("Web: Tavily store %s in [bold]%s[/] mode", m.web.store_id, m.web.mode)
        c.web_store = TavilyStore(tavily_root, m.web.store_id or "live", mode=m.web.mode)
        if m.web.gated:
            from dwb.sufficiency import SufficiencyJudge

            LOG.info("Web gate: searched only when %s judges the local evidence insufficient",
                     m.models.generator)
            c.judge = SufficiencyJudge(gen_llm, base_dir / m.prompts["sufficiency"].path,
                                       model_id=m.models.generator)
    if m.condition in USES_CODE_INDEX:
        LOG.info("Repository branch %s: code index %s", m.condition, m.indexes.code_index_id)
        index = VectorIndex.open(index_root, m.indexes.code_index_id, emb)
        check_code_index(index.info, m)
        c.code_retriever = CodeRetriever(index)
    if m.condition in USES_WIKI:
        from dwb.repo import RepoReader
        from dwb.retrieval.openwiki_client import OpenWikiClient, restore_snapshot
        from dwb.retrieval.resolver import WikiResolver

        if not m.checkout:
            raise ValueError(f"condition {m.condition} needs manifest.checkout (git checkout "
                             f"at repo_sha)")
        checkout = base_dir / m.checkout
        if restore_wiki:
            LOG.info("Repository branch %s: restoring wiki snapshot %s into %s",
                     m.condition, m.indexes.wiki_snapshot_id, checkout)
            restore_snapshot(wiki_root / m.indexes.wiki_snapshot_id, checkout)
        else:
            LOG.info("Repository branch %s: OpenWiki in %s", m.condition, checkout / "openwiki")
        c.wiki = OpenWikiClient(checkout, command=openwiki_bin)
        c.resolver = WikiResolver(RepoReader.from_git(checkout, m.repo_sha),
                                  max_span_tokens=m.budgets.max_span_tokens)
    return c


class IndexMismatchError(ValueError):
    pass


def check_code_index(info, m: RunManifest) -> None:
    """The code index must match the manifest: same SHA, and spans cut with the shared
    ``max_span_tokens`` budget that condition B's resolver uses (spike §10)."""
    parts = info.parts or {}
    if parts.get("sha") and parts["sha"] != m.repo_sha:
        raise IndexMismatchError(f"code index {info.index_id} is for {parts['sha'][:12]}, "
                                 f"the manifest pins {m.repo_sha[:12]}")
    from dwb.retrieval.chunking import ChunkingConfig

    chunking = parts.get("chunking") or {}
    current = ChunkingConfig.chunker_version
    if chunking and chunking.get("chunker_version", 1) != current:
        raise IndexMismatchError(
            f"code index {info.index_id} was built by chunker version "
            f"{chunking.get('chunker_version', 1)}, the current one is {current}. Rebuild it: "
            f"dwb index code {parts.get('repo', '<repo>')} (then update the manifest), or "
            "dwb index inside a project")
    built = chunking.get("max_span_tokens")
    if built is not None and built != m.budgets.max_span_tokens:
        raise IndexMismatchError(
            f"code index {info.index_id} was chunked with max_span_tokens={built}, the manifest "
            f"budget is {m.budgets.max_span_tokens}; rebuild it with "
            f"--max-span-tokens {m.budgets.max_span_tokens}")


def web_eligible(m: RunManifest, questions: list[QuestionRecord]) -> list[QuestionRecord]:
    """The questions whose graph run may search the web (the same rule as the graph's)."""
    return [q for q in questions if m.web.enabled and "web" in q.allowed_sources]


def capture_web(m: RunManifest, questions: list[QuestionRecord], store: Any, *,
                on_progress: OnProgress | None = None) -> dict[str, Any]:
    """Web-only record pass: store the Tavily result of every web-eligible question, with no
    retrieval, reranking or generation. A record-mode graph run captures the same requests
    (``capture_all_web``), since the request is built from the question text and the
    manifest's web settings only, never from a condition's evidence. Requests already in the
    store are kept as they are (record mode reuses them). Out of credit or a bad key stops
    it; rerunning continues where it stopped."""
    eligible = web_eligible(m, questions)
    LOG.info("Web capture into store %s: %d of %d questions allow the web", m.web.store_id,
             len(eligible), len(questions))
    report(on_progress, "web", "start", total=len(eligible), label=m.web.store_id)
    stored = live = 0
    failed: list[dict[str, str]] = []
    for i, q in enumerate(eligible, start=1):
        try:
            store.search(query_text(q), max_results=m.web.max_results,
                         search_depth=m.web.search_depth, exclude_domains=m.web.exclude_domains)
        except Exception as e:
            raise_if_fatal(e)
            failed.append({"question_id": q.id, "error": f"{type(e).__name__}: {e}"})
            LOG.warning("    %s: web capture failed: %s", q.id, failed[-1]["error"])
            out = "failed"
        else:
            src = getattr(store, "last_call", {}).get("source")
            live += src == "live"
            stored += src == "store"
            out = "ok"
        report(on_progress, "web", "step", done=i, total=len(eligible), label=q.id, outcome=out)
    summary = {"store_id": m.web.store_id, "question_set": m.question_set.id,
               "questions": len(questions), "eligible": len(eligible),
               "captured_live": live, "already_stored": stored,
               "web_capture_failed": len(failed), "failures": failed}
    report(on_progress, "web", "done", done=len(eligible), total=len(eligible),
           label=m.web.store_id, data=summary)
    return summary


def run_manifest(
    m: RunManifest,
    components: Components,
    *,
    base_dir: Path,
    runs_dir: Path,
    run_stages: list[StageRecord] | None = None,
    overwrite: bool = False,
    resume: bool = False,
    on_progress: OnProgress | None = None,
) -> dict[str, Any]:
    """Check the question set before anything else, then process every question
    (``resume``: only the ones an interrupted run has not finished)."""
    if overwrite and resume:
        raise ValueError("--overwrite and --resume exclude each other")
    require_runnable(m)
    questions = load_question_set(m, base_dir)
    LOG.info("Question set %s: %d questions (%s)", m.question_set.id, len(questions),
             "frozen, hash verified" if m.question_set.hash else "not frozen (dev)")
    m = m.resolve_prompts(base_dir)
    kept: list[TraceRecord] = []
    if resume:
        run_dir, kept = RunDir.resume(m, runs_dir)
    else:
        run_dir = RunDir.start(m, runs_dir, overwrite=overwrite)
    return asyncio.run(run_questions(m, components, questions, run_dir, run_stages=run_stages,
                                     on_progress=on_progress, kept=kept))
