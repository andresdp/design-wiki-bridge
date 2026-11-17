"""Manifest-driven batch runner (R10)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dwb.benchmark.freeze import file_hash
from dwb.benchmark.schemas import QuestionRecord, load_records
from dwb.graph import Components, build_graph, trace_from_state
from dwb.manifest import RunManifest
from dwb.trace import RunDir, StageRecord, TraceRecord


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
    on_trace: Callable[[TraceRecord], None] | None = None,
) -> dict[str, Any]:
    graph = build_graph(components, m)
    n_failed = n_empty = n_web = n_unresolved = 0
    t_run = time.perf_counter()
    run_stages = list(run_stages or [])
    async with AsyncExitStack() as stack:
        wiki = components.wiki
        if wiki is not None and hasattr(type(wiki), "__aenter__"):
            await stack.enter_async_context(wiki)  # one MCP session for the whole run
            if getattr(wiki, "startup_stage", None) is not None:
                run_stages.append(wiki.startup_stage)
        counts = await _run_all(graph, m, questions, run_dir, on_trace)
    n_failed, n_empty, n_web, n_unresolved = counts
    summary = {
        "run_id": m.run_id,
        "condition": m.condition,
        "questions": len(questions),
        "failed": n_failed,
        "empty_context": n_empty,
        "web_triggered": n_web,
        "unresolved_citation_ids": n_unresolved,
        "elapsed_s": round(time.perf_counter() - t_run, 3),
        "run_stages": [s.model_dump() for s in run_stages],
    }
    run_dir.write_summary(summary)
    return summary


async def _run_all(graph, m: RunManifest, questions: list[QuestionRecord], run_dir: RunDir,
                   on_trace: Callable[[TraceRecord], None] | None) -> tuple[int, int, int, int]:
    n_failed = n_empty = n_web = n_unresolved = 0
    with run_dir.writer() as w:
        for q in questions:
            try:
                state = await graph.ainvoke({"question": q})
                rec = trace_from_state(state, m)
            except Exception as e:
                rec = TraceRecord(question_id=q.id, run_id=m.run_id, condition=m.condition,
                                  errors=[f"{type(e).__name__}: {e}"])
            n_failed += bool(rec.errors)
            n_empty += rec.empty_context
            n_web += rec.web_triggered
            n_unresolved += int(rec.validation.get("n_unresolved_ids", 0) or 0)
            w.append(rec)
            if on_trace:
                on_trace(rec)
    return n_failed, n_empty, n_web, n_unresolved


def build_components(
    m: RunManifest,
    *,
    base_dir: Path,
    index_root: Path,
    tavily_root: Path,
    wiki_root: Path = Path("artifacts/wikis"),
    openwiki_bin: str | None = None,
) -> Components:
    """Real components for a manifest: Chroma retrievers, Tavily store, OpenAI models."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    from dwb.generate import Generator
    from dwb.rerank import LLMListwiseReranker
    from dwb.retrieval.code_index import CodeRetriever
    from dwb.retrieval.pdf_index import PdfRetriever
    from dwb.retrieval.vector_store import VectorIndex
    from dwb.web.tavily_store import TavilyStore

    emb = OpenAIEmbeddings(model=m.models.embedding)
    gen_llm = ChatOpenAI(model=m.models.generator, temperature=m.models.temperature)
    rr_llm = ChatOpenAI(model=m.models.reranker, temperature=m.models.temperature)
    c = Components(
        reranker=LLMListwiseReranker(rr_llm, pool_cap=m.budgets.rerank_pool,
                                     top_n=m.budgets.rerank_top_n, model_id=m.models.reranker),
        generator=Generator(gen_llm, base_dir / m.prompts["generate"].path,
                            model_id=m.models.generator),
    )
    if m.pdf_enabled:
        c.pdf_retriever = PdfRetriever(VectorIndex.open(index_root, m.indexes.pdf_index_id, emb))
    if m.web.enabled:
        c.web_store = TavilyStore(tavily_root, m.web.store_id or "live", mode=m.web.mode)
    if m.condition == "A":
        c.code_retriever = CodeRetriever(
            VectorIndex.open(index_root, m.indexes.code_index_id, emb))
    else:
        from dwb.repo import RepoReader
        from dwb.retrieval.openwiki_client import OpenWikiClient, restore_snapshot
        from dwb.retrieval.resolver import WikiResolver

        if not m.checkout:
            raise ValueError("condition B needs manifest.checkout (git checkout at repo_sha)")
        checkout = base_dir / m.checkout
        restore_snapshot(wiki_root / m.indexes.wiki_snapshot_id, checkout)
        c.wiki = OpenWikiClient(checkout, command=openwiki_bin)
        c.resolver = WikiResolver(RepoReader.from_git(checkout, m.repo_sha),
                                  max_span_tokens=m.budgets.max_span_tokens)
    return c


def run_manifest(
    m: RunManifest,
    components: Components,
    *,
    base_dir: Path,
    runs_dir: Path,
    run_stages: list[StageRecord] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Check the question set before anything else, then process every question."""
    questions = load_question_set(m, base_dir)
    m = m.resolve_prompts(base_dir)
    run_dir = RunDir.start(m, runs_dir, overwrite=overwrite)
    return asyncio.run(run_questions(m, components, questions, run_dir, run_stages=run_stages))
