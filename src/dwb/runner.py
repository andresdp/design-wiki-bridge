"""Manifest-driven batch runner (R10)."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dwb.benchmark.freeze import file_hash
from dwb.benchmark.schemas import QuestionRecord, load_records
from dwb.graph import Components, build_graph, trace_from_state
from dwb.log import get_logger
from dwb.manifest import MODEL_NET, RunManifest, require_runnable
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


async def run_questions(
    m: RunManifest,
    components: Components,
    questions: list[QuestionRecord],
    run_dir: RunDir,
    *,
    run_stages: list[StageRecord] | None = None,
    on_progress: OnProgress | None = None,
) -> dict[str, Any]:
    """Answer every question and write the traces and the run summary. ``on_progress`` gets a
    ``run`` start (``total`` questions, ``data["traces"]`` the traces file), one step per
    question once its trace is written (``outcome`` ok/failed/degraded/empty, ``data`` the
    ``TraceRecord`` plus the running failed/degraded/empty_context counts) and a done event
    carrying the summary."""
    # record runs capture web for every eligible item, so a replay of either condition
    # finds it whatever that condition's gate verdict
    graph = build_graph(components, m, capture_all_web=m.web.mode == "record")
    t_run = time.perf_counter()
    run_stages = list(run_stages or [])
    LOG.debug("Run [bold]%s[/]: condition %s, %s @ %s, %d questions → %s", m.run_id,
              m.condition, m.repo, m.repo_sha[:12], len(questions), run_dir.root)
    report(on_progress, "run", "start", total=len(questions), label=m.run_id,
           data={"traces": run_dir.traces_path, "run_dir": run_dir.root})
    async with AsyncExitStack() as stack:
        startup = await start_wiki(stack, components)  # one MCP session for the whole run
        if startup is not None:
            run_stages.append(startup)
        counts = await _run_all(graph, m, questions, run_dir, on_progress)
    n_failed, n_degraded, n_empty, n_web, n_unresolved = counts
    summary = {
        "run_id": m.run_id,
        "condition": m.condition,
        "questions": len(questions),
        "failed": n_failed,
        "degraded": n_degraded,
        "empty_context": n_empty,
        "web_triggered": n_web,
        "unresolved_citation_ids": n_unresolved,
        "elapsed_s": round(time.perf_counter() - t_run, 3),
        "run_stages": [s.model_dump() for s in run_stages],
    }
    run_dir.write_summary(summary)
    style = "red" if n_failed else "green"
    LOG.debug("[%s]Done[/]: %d questions, %d failed, %d degraded, %d empty context, %d with web, "
              "%d unresolved citation IDs, %.1f s. Traces: %s", style, len(questions), n_failed,
              n_degraded, n_empty, n_web, n_unresolved, summary["elapsed_s"], run_dir.traces_path)
    report(on_progress, "run", "done", done=len(questions), total=len(questions),
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


async def _run_all(
    graph, m: RunManifest, questions: list[QuestionRecord], run_dir: RunDir,
    on_progress: OnProgress | None,
) -> tuple[int, int, int, int, int]:
    n_failed = n_degraded = n_empty = n_web = n_unresolved = 0
    total = len(questions)
    with run_dir.writer() as w:
        for i, q in enumerate(questions, start=1):
            LOG.debug("[cyan][%d/%d][/] %s: %s", i, total, q.id, _short(q.question))
            t0 = time.perf_counter()
            try:
                state = await graph.ainvoke({"question": q})
                rec = trace_from_state(state, m)
            except Exception as e:
                rec = TraceRecord(question_id=q.id, run_id=m.run_id, condition=m.condition,
                                  errors=[f"{type(e).__name__}: {e}"])
            elapsed = time.perf_counter() - t0
            _log_trace(rec, elapsed)
            n_failed += bool(rec.errors)
            n_degraded += rec.degraded
            n_empty += rec.empty_context
            n_web += rec.web_triggered
            n_unresolved += int(rec.validation.get("n_unresolved_ids", 0) or 0)
            w.append(rec)
            report(on_progress, "run", "step", done=i, total=total, label=q.id,
                   outcome=outcome(rec), elapsed_s=elapsed,
                   data={"trace": rec, "failed": n_failed, "degraded": n_degraded,
                         "empty_context": n_empty})
    return n_failed, n_degraded, n_empty, n_web, n_unresolved


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
    if m.condition == "A":
        LOG.info("Repository branch A: code index %s", m.indexes.code_index_id)
        index = VectorIndex.open(index_root, m.indexes.code_index_id, emb)
        check_code_index(index.info, m)
        c.code_retriever = CodeRetriever(index)
    else:
        from dwb.repo import RepoReader
        from dwb.retrieval.openwiki_client import OpenWikiClient, restore_snapshot
        from dwb.retrieval.resolver import WikiResolver

        if not m.checkout:
            raise ValueError("condition B needs manifest.checkout (git checkout at repo_sha)")
        checkout = base_dir / m.checkout
        if restore_wiki:
            LOG.info("Repository branch B: restoring wiki snapshot %s into %s",
                     m.indexes.wiki_snapshot_id, checkout)
            restore_snapshot(wiki_root / m.indexes.wiki_snapshot_id, checkout)
        else:
            LOG.info("Repository branch B: OpenWiki in %s", checkout / "openwiki")
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


def run_manifest(
    m: RunManifest,
    components: Components,
    *,
    base_dir: Path,
    runs_dir: Path,
    run_stages: list[StageRecord] | None = None,
    overwrite: bool = False,
    on_progress: OnProgress | None = None,
) -> dict[str, Any]:
    """Check the question set before anything else, then process every question."""
    require_runnable(m)
    questions = load_question_set(m, base_dir)
    LOG.info("Question set %s: %d questions (%s)", m.question_set.id, len(questions),
             "frozen, hash verified" if m.question_set.hash else "not frozen (dev)")
    m = m.resolve_prompts(base_dir)
    run_dir = RunDir.start(m, runs_dir, overwrite=overwrite)
    return asyncio.run(run_questions(m, components, questions, run_dir, run_stages=run_stages,
                                     on_progress=on_progress))
