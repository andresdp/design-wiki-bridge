"""The query graph (KTD11): an explicit async StateGraph with deterministic routing.

prepare -> {repo branch (A: code_retrieve | B: wiki_search -> wiki_read -> resolve),
pdf_retrieve} -> join_local (defer=True) -> [check_local] -> [web_retrieve] -> rerank ->
assemble_context -> generate -> validate. With no candidates the graph ends at
``empty_context`` (R5) and never substitutes another retriever.

Web is a wider search after a narrow one: it runs only for items that allow it and, when
``web.conditional_web_search`` is on, only after ``check_local``'s judge finds the local
evidence insufficient (KTD7 as revised by the conditional-web-search plan).
"""

from __future__ import annotations

import asyncio
import operator
import time
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from dwb.benchmark.schemas import QuestionRecord
from dwb.budget import Context, assemble_context
from dwb.evidence import EvidenceItem
from dwb.generate import GeneratedAnswer, Generator, render_answer, validate_claims
from dwb.log import get_logger
from dwb.manifest import RunManifest
from dwb.merge import merge_candidates
from dwb.rerank import Reranker, interleave_pool
from dwb.retrieval.wiki_types import Resolver, Section, SectionRef, WikiClient
from dwb.sufficiency import SufficiencyJudge
from dwb.trace import AnswerClaim, StageRecord, TraceRecord
from dwb.usage import embedding_usage
from dwb.web.tavily_store import ReplayMiss

LOG = get_logger("graph")


class GraphState(TypedDict, total=False):
    question: QuestionRecord
    query: str
    stages: Annotated[list[StageRecord], operator.add]
    repo_candidates: Annotated[list[EvidenceItem], operator.add]
    pdf_candidates: Annotated[list[EvidenceItem], operator.add]
    unresolved_refs: Annotated[list[dict], operator.add]
    wiki_refs: list[SectionRef]
    wiki_sections: list[Section]
    merged: list[EvidenceItem]
    merged_ids: dict[str, str]
    web_check: dict[str, Any]  # check_local's verdict: sufficient, reason, judged, seen_ids
    web_candidates: list[EvidenceItem]
    web_triggered: bool
    reranked: list[EvidenceItem]
    context: Context
    answer: GeneratedAnswer | None
    claims: list[AnswerClaim]
    validation: dict[str, Any]
    rendered: str | None
    empty_context: bool
    errors: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]


@dataclass
class Components:
    reranker: Reranker
    generator: Generator
    code_retriever: Any = None  # .retrieve(query, k) -> list[EvidenceItem]   (condition A)
    pdf_retriever: Any = None  # .retrieve(query, k) -> list[EvidenceItem]
    wiki: WikiClient | None = None  # condition B
    resolver: Resolver | None = None  # condition B
    web_store: Any = None  # TavilyStore
    judge: SufficiencyJudge | None = None  # the web gate (web.conditional_web_search)


def _stage(name: str, t0: float, items: list[EvidenceItem] | None = None, **extra) -> StageRecord:
    items = items or []
    return StageRecord(name=name, latency_ms=(time.perf_counter() - t0) * 1000,
                       candidate_ids=[i.id for i in items], count=len(items), extra=extra)


def _embed_stage(name: str, t0: float, items: list[EvidenceItem], query: str, retriever,
                 **extra) -> StageRecord:
    """Retrieval stage record including the (approximate) query-embedding tokens."""
    model = getattr(retriever, "embedding_model", None)
    u = embedding_usage(query, model)
    st = _stage(name, t0, items, embedding_model=model, **u.extra(), **extra)
    st.input_tokens = u.input_tokens
    return st


def _degraded(name: str, t0: float, e: Exception, **out) -> dict:
    """A source failed: record it and let the other branches answer (R5)."""
    msg = f"{type(e).__name__}: {e}"
    LOG.warning("    %s failed, continuing without it: %s", name, msg)
    return {"stages": [_stage(name, t0, error=msg)], "warnings": [f"{name}: {msg}"], **out}


def build_graph(c: Components, m: RunManifest, *, capture_all_web: bool = False):
    """``capture_all_web`` (experiment record runs): search the web for every web-eligible
    item even when the gate finds the local evidence sufficient, store the result and add
    nothing, so a replay of either condition never misses because its verdict differed."""
    b = m.budgets
    gated = m.web.gated
    if gated and c.judge is None:
        raise ValueError("web.conditional_web_search is on but no sufficiency judge was "
                         "supplied (Components.judge)")
    # repository branch entry and exit nodes, used by both the router and the edges
    repo_entry, repo_exit = ("code_retrieve", "code_retrieve") if m.condition == "A" else (
        "wiki_search", "resolve")

    def wants_pdf(q: QuestionRecord) -> bool:
        return m.pdf_enabled and "pdf" in q.allowed_sources and c.pdf_retriever is not None

    def wants_web(q: QuestionRecord) -> bool:
        return m.web.enabled and "web" in q.allowed_sources and c.web_store is not None

    async def prepare(s: GraphState) -> dict:
        t0 = time.perf_counter()
        q = s["question"]
        query = " ".join(q.question.split())
        return {"query": query, "stages": [_stage("prepare", t0, allowed=q.allowed_sources)]}

    def route_local(s: GraphState) -> list[str]:
        targets = [repo_entry]
        if wants_pdf(s["question"]):
            targets.append("pdf_retrieve")
        return targets

    async def code_retrieve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        try:
            items = await asyncio.to_thread(c.code_retriever.retrieve, s["query"], b.code_top_k)
        except Exception as e:
            return _degraded("code_retrieve", t0, e)
        st = _embed_stage("code_retrieve", t0, items, s["query"], c.code_retriever, k=b.code_top_k)
        return {"repo_candidates": items, "stages": [st]}

    async def wiki_search(s: GraphState) -> dict:
        t0 = time.perf_counter()
        try:
            refs = await c.wiki.search(s["query"], b.wiki_search_k)
        except Exception as e:
            return _degraded("wiki_search", t0, e, wiki_refs=[])
        st = _stage("wiki_search", t0, k=b.wiki_search_k, refs=[r.ref for r in refs])
        st.count = len(refs)
        return {"wiki_refs": refs, "stages": [st]}

    async def wiki_read(s: GraphState) -> dict:
        t0 = time.perf_counter()
        refs = s.get("wiki_refs", [])[: b.wiki_read_sections]
        try:
            sections = await c.wiki.read(refs) if refs else []
        except Exception as e:
            return _degraded("wiki_read", t0, e, wiki_sections=[])
        st = _stage("wiki_read", t0, sections=[x.ref.ref for x in sections])
        st.count = len(sections)
        return {"wiki_sections": sections, "stages": [st]}

    async def resolve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        try:
            res = c.resolver.resolve(s.get("wiki_sections", []), b.wiki_max_spans)
        except Exception as e:
            return _degraded("resolve", t0, e)
        unresolved = [u.model_dump() for u in res.unresolved]
        return {
            "repo_candidates": res.items,
            "unresolved_refs": unresolved,
            "stages": [_stage("resolve", t0, res.items, unresolved=len(unresolved),
                              ambiguous=res.ambiguous, max_spans=b.wiki_max_spans)],
        }

    async def pdf_retrieve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        try:
            items = await asyncio.to_thread(c.pdf_retriever.retrieve, s["query"], b.pdf_top_k)
        except Exception as e:
            return _degraded("pdf_retrieve", t0, e)
        st = _embed_stage("pdf_retrieve", t0, items, s["query"], c.pdf_retriever, k=b.pdf_top_k)
        return {"pdf_candidates": items, "stages": [st]}

    async def join_local(s: GraphState) -> dict:
        t0 = time.perf_counter()
        repo, pdf = s.get("repo_candidates", []), s.get("pdf_candidates", [])
        merged, alias = merge_candidates(repo + pdf)
        return {"merged": merged, "merged_ids": alias,
                "stages": [_stage("join_local", t0, merged, repo_count=len(repo),
                                  pdf_count=len(pdf), merged_away=len(alias))]}

    def route_after_join(s: GraphState) -> str:
        if wants_web(s["question"]):
            return "check_local" if gated else "web_retrieve"
        return "rerank" if s.get("merged") else "empty_context"

    async def check_local(s: GraphState) -> dict:
        """Judge whether the local evidence is enough. The judge reads the local pool in
        branch-interleaved order cut to the context budget (no rerank), so its slice can
        differ from the generator's; ``seen_ids`` records what it read."""
        t0 = time.perf_counter()
        merged = s.get("merged", [])
        ctx = assemble_context(interleave_pool(merged, b.rerank_pool), b.context_tokens) \
            if merged else None
        if ctx is None or ctx.empty:  # insufficient by definition: no judge call
            return {"web_check": {"sufficient": False, "reason": "no local evidence",
                                  "judged": False, "seen_ids": []},
                    "stages": [_stage("check_local", t0, sufficient=False, judged=False)]}
        verdict, st = await c.judge.ajudge(s["question"].question, ctx)
        out: dict = {"web_check": {"sufficient": verdict.sufficient, "reason": verdict.reason,
                                   "judged": True, "seen_ids": list(ctx.ids)},
                     "stages": [st]}
        if verdict.sufficient is None:  # failed judge: search the web (degraded, KTD-W4)
            LOG.warning("    check_local failed, searching the web: %s", st.extra["error"])
            out["warnings"] = [f"check_local: {st.extra['error']} (searched the web)"]
        return out

    def route_after_check(s: GraphState) -> str:
        if s["web_check"]["sufficient"] is not True or capture_all_web:
            return "web_retrieve"
        return "rerank"

    async def web_retrieve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        # capture only: a record run storing web results the gate decided not to use
        capture_only = capture_all_web and (s.get("web_check") or {}).get("sufficient") is True
        try:
            items = await asyncio.to_thread(
                c.web_store.search, s["query"], max_results=m.web.max_results,
                search_depth=m.web.search_depth, exclude_domains=m.web.exclude_domains,
            )
        except ReplayMiss:
            raise  # a replay run must not quietly answer without its frozen web evidence
        except Exception as e:
            if capture_only:  # nothing was going to use it: not a degraded answer
                return {"web_candidates": [], "stages": [_stage(
                    "web_retrieve", t0, error=f"{type(e).__name__}: {e}", used=False)]}
            return _degraded("web_retrieve", t0, e, web_candidates=[], web_triggered=True)
        extra = {"mode": m.web.mode, "used": not capture_only,
                 **getattr(c.web_store, "last_call", {})}
        if capture_only:
            return {"web_candidates": [],
                    "stages": [_stage("web_retrieve", t0, captured=len(items), **extra)]}
        return {"web_candidates": items, "web_triggered": True,
                "stages": [_stage("web_retrieve", t0, items, **extra)]}

    def route_after_web(s: GraphState) -> str:
        return "rerank" if (s.get("merged") or s.get("web_candidates")) else "empty_context"

    async def rerank(s: GraphState) -> dict:
        pool, alias = merge_candidates(s.get("merged", []) + s.get("web_candidates", []))
        res = await c.reranker.arerank(s["query"], pool)
        out: dict = {"reranked": res.items, "merged_ids": {**s.get("merged_ids", {}), **alias},
                     "stages": [res.stage]}
        if res.stage.extra.get("error"):  # answered from pool order: degraded, not failed
            out["warnings"] = [f"rerank: {res.stage.extra['error']} (fell back to pool order)"]
        return out

    async def assemble(s: GraphState) -> dict:
        t0 = time.perf_counter()
        ctx = assemble_context(s["reranked"], b.context_tokens)
        st = _stage("assemble_context", t0, ctx.items, tokens=ctx.tokens,
                    budget=b.context_tokens, excluded=ctx.excluded_ids)
        st.input_tokens = ctx.tokens
        return {"context": ctx, "stages": [st]}

    def route_after_assemble(s: GraphState) -> str:
        # nothing fit the budget (e.g. one oversized passage): do not generate without evidence
        return "empty_context" if s["context"].empty else "generate"

    async def generate(s: GraphState) -> dict:
        answer, stage = await c.generator.agenerate(s["question"].question, s["context"])
        out: dict = {"answer": answer, "stages": [stage]}
        if stage.extra.get("error"):
            out["errors"] = [f"generate: {stage.extra['error']}"]
        return out

    async def validate(s: GraphState) -> dict:
        t0 = time.perf_counter()
        answer = s.get("answer")
        if answer is None:
            return {"claims": [], "validation": {"skipped": "no answer"}, "rendered": None,
                    "stages": [_stage("validate", t0)]}
        claims, report = validate_claims(answer, s["context"])
        st = _stage("validate", t0, unresolved=report["n_unresolved_ids"])
        st.count = len(claims)
        return {"claims": claims, "validation": report,
                "rendered": render_answer(claims, answer.summary, s["context"].aliases),
                "stages": [st]}

    async def empty_context(s: GraphState) -> dict:
        t0 = time.perf_counter()
        return {"empty_context": True, "stages": [_stage("empty_context", t0)]}

    g = StateGraph(GraphState)
    g.add_node("prepare", prepare)
    if m.condition == "A":
        g.add_node("code_retrieve", code_retrieve)
    else:
        g.add_node("wiki_search", wiki_search)
        g.add_node("wiki_read", wiki_read)
        g.add_node("resolve", resolve)
        g.add_edge("wiki_search", "wiki_read")
        g.add_edge("wiki_read", "resolve")
    g.add_node("pdf_retrieve", pdf_retrieve)
    g.add_node("join_local", join_local, defer=True)
    if gated:
        g.add_node("check_local", check_local)
    g.add_node("web_retrieve", web_retrieve)
    g.add_node("rerank", rerank)
    g.add_node("assemble_context", assemble)
    g.add_node("generate", generate)
    g.add_node("validate", validate)
    g.add_node("empty_context", empty_context)

    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", route_local, [repo_entry, "pdf_retrieve"])
    g.add_edge(repo_exit, "join_local")
    g.add_edge("pdf_retrieve", "join_local")
    after_join = ["check_local" if gated else "web_retrieve", "rerank", "empty_context"]
    g.add_conditional_edges("join_local", route_after_join, after_join)
    if gated:
        g.add_conditional_edges("check_local", route_after_check, ["web_retrieve", "rerank"])
    g.add_conditional_edges("web_retrieve", route_after_web, ["rerank", "empty_context"])
    g.add_edge("rerank", "assemble_context")
    g.add_conditional_edges("assemble_context", route_after_assemble,
                            ["generate", "empty_context"])
    g.add_edge("generate", "validate")
    g.add_edge("validate", END)
    g.add_edge("empty_context", END)
    return g.compile()


def _evidence_dump(item: EvidenceItem, with_text: bool) -> dict:
    d = {"source_type": item.source_type, "locator": item.locator.model_dump(),
         "provenance": item.provenance}
    if with_text:
        d["text"] = item.text
    return d


def trace_from_state(s: GraphState, m: RunManifest, *,
                     keep_code_text: bool = False) -> TraceRecord:
    """Final graph state -> trace. Code passages are stored by reference (commit, path,
    lines) unless ``keep_code_text``: their text is exactly ``git show <commit>:<path>``
    sliced to those lines, and ``dwb.evidence_text.hydrate`` restores it for evaluation.
    PDF and Web passage text is kept for the final context (and all Web passages)."""
    q = s["question"]
    ctx: Context | None = s.get("context")
    candidates = {
        "repo": [i.id for i in s.get("repo_candidates", [])],
        "pdf": [i.id for i in s.get("pdf_candidates", [])],
        "merged": [i.id for i in s.get("merged", [])],
        "web": [i.id for i in s.get("web_candidates", [])],
        "reranked": [i.id for i in s.get("reranked", [])],
    }
    evidence: dict[str, dict] = {}
    final_ids = set(ctx.ids) if ctx else set()
    for key in ("repo_candidates", "pdf_candidates", "web_candidates", "merged", "reranked"):
        for it in s.get(key, []):
            if it.id not in evidence or it.id in final_ids:
                # text kept for final context (judge) and web passages (KTD15 matching);
                # code text is recoverable from git, so it is stored by reference
                keep = (it.id in final_ids or it.source_type == "web") and (
                    keep_code_text or it.source_type != "code")
                evidence[it.id] = _evidence_dump(it, with_text=keep)
    answer = s.get("answer")
    return TraceRecord(
        question_id=q.id,
        run_id=m.run_id,
        condition=m.condition,
        stages=s.get("stages", []),
        candidates=candidates,
        evidence=evidence,
        wiki_section_refs=[r.ref for r in s.get("wiki_refs", [])],
        unresolved_refs=s.get("unresolved_refs", []),
        merged_ids=s.get("merged_ids", {}),
        web_triggered=s.get("web_triggered", False),
        web_check=s.get("web_check"),
        empty_context=s.get("empty_context", False),
        final_context_ids=ctx.ids if ctx else [],
        aliases=ctx.aliases if ctx else {},
        claims=s.get("claims", []),
        summary=answer.summary if answer else None,
        answer=s.get("rendered"),
        validation=s.get("validation", {}),
        errors=s.get("errors", []),
        warnings=s.get("warnings", []),
    )
