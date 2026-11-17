"""The query graph (KTD11): an explicit async StateGraph with deterministic routing.

prepare -> {repo branch (A: code_retrieve | B: wiki_search -> wiki_read -> resolve),
pdf_retrieve} -> join_local (defer=True) -> [web_retrieve] -> rerank -> assemble_context
-> generate -> validate. With no candidates the graph ends at ``empty_context`` (R5) and
never substitutes another retriever.
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
from dwb.manifest import RunManifest
from dwb.merge import merge_candidates
from dwb.rerank import Reranker
from dwb.retrieval.wiki_types import Resolver, Section, SectionRef, WikiClient
from dwb.trace import AnswerClaim, StageRecord, TraceRecord


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


@dataclass
class Components:
    reranker: Reranker
    generator: Generator
    code_retriever: Any = None  # .retrieve(query, k) -> list[EvidenceItem]   (condition A)
    pdf_retriever: Any = None  # .retrieve(query, k) -> list[EvidenceItem]
    wiki: WikiClient | None = None  # condition B
    resolver: Resolver | None = None  # condition B
    web_store: Any = None  # TavilyStore


def _stage(name: str, t0: float, items: list[EvidenceItem] | None = None, **extra) -> StageRecord:
    items = items or []
    return StageRecord(name=name, latency_ms=(time.perf_counter() - t0) * 1000,
                       candidate_ids=[i.id for i in items], count=len(items), extra=extra)


def build_graph(c: Components, m: RunManifest):
    b = m.budgets

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
        targets = ["code_retrieve" if m.condition == "A" else "wiki_search"]
        if wants_pdf(s["question"]):
            targets.append("pdf_retrieve")
        return targets

    async def code_retrieve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        items = await asyncio.to_thread(c.code_retriever.retrieve, s["query"], b.code_top_k)
        return {"repo_candidates": items,
                "stages": [_stage("code_retrieve", t0, items, k=b.code_top_k)]}

    async def wiki_search(s: GraphState) -> dict:
        t0 = time.perf_counter()
        refs = await c.wiki.search(s["query"], b.wiki_search_k)
        st = _stage("wiki_search", t0, k=b.wiki_search_k, refs=[r.ref for r in refs])
        st.count = len(refs)
        return {"wiki_refs": refs, "stages": [st]}

    async def wiki_read(s: GraphState) -> dict:
        t0 = time.perf_counter()
        refs = s.get("wiki_refs", [])[: b.wiki_read_sections]
        sections = await c.wiki.read(refs) if refs else []
        st = _stage("wiki_read", t0, sections=[x.ref.ref for x in sections])
        st.count = len(sections)
        return {"wiki_sections": sections, "stages": [st]}

    async def resolve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        res = c.resolver.resolve(s.get("wiki_sections", []), b.wiki_max_spans)
        unresolved = [u.model_dump() for u in res.unresolved]
        return {
            "repo_candidates": res.items,
            "unresolved_refs": unresolved,
            "stages": [_stage("resolve", t0, res.items, unresolved=len(unresolved),
                              ambiguous=res.ambiguous, max_spans=b.wiki_max_spans)],
        }

    async def pdf_retrieve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        items = await asyncio.to_thread(c.pdf_retriever.retrieve, s["query"], b.pdf_top_k)
        return {"pdf_candidates": items,
                "stages": [_stage("pdf_retrieve", t0, items, k=b.pdf_top_k)]}

    async def join_local(s: GraphState) -> dict:
        t0 = time.perf_counter()
        repo, pdf = s.get("repo_candidates", []), s.get("pdf_candidates", [])
        merged, alias = merge_candidates(repo + pdf)
        return {"merged": merged, "merged_ids": alias,
                "stages": [_stage("join_local", t0, merged, repo_count=len(repo),
                                  pdf_count=len(pdf), merged_away=len(alias))]}

    def route_after_join(s: GraphState) -> str:
        if wants_web(s["question"]):
            return "web_retrieve"
        return "rerank" if s.get("merged") else "empty_context"

    async def web_retrieve(s: GraphState) -> dict:
        t0 = time.perf_counter()
        items = await asyncio.to_thread(
            c.web_store.search, s["query"], max_results=m.web.max_results,
            search_depth=m.web.search_depth, exclude_domains=m.web.exclude_domains,
        )
        return {"web_candidates": items, "web_triggered": True,
                "stages": [_stage("web_retrieve", t0, items, mode=m.web.mode,
                                  **getattr(c.web_store, "last_call", {}))]}

    def route_after_web(s: GraphState) -> str:
        return "rerank" if (s.get("merged") or s.get("web_candidates")) else "empty_context"

    async def rerank(s: GraphState) -> dict:
        pool, alias = merge_candidates(s.get("merged", []) + s.get("web_candidates", []))
        res = await c.reranker.arerank(s["query"], pool)
        return {"reranked": res.items, "merged_ids": {**s.get("merged_ids", {}), **alias},
                "stages": [res.stage]}

    async def assemble(s: GraphState) -> dict:
        t0 = time.perf_counter()
        ctx = assemble_context(s["reranked"], b.context_tokens)
        st = _stage("assemble_context", t0, ctx.items, tokens=ctx.tokens,
                    budget=b.context_tokens, excluded=ctx.excluded_ids)
        st.input_tokens = ctx.tokens
        return {"context": ctx, "stages": [st]}

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
        local_targets = ["code_retrieve"]
    else:
        g.add_node("wiki_search", wiki_search)
        g.add_node("wiki_read", wiki_read)
        g.add_node("resolve", resolve)
        g.add_edge("wiki_search", "wiki_read")
        g.add_edge("wiki_read", "resolve")
        local_targets = ["wiki_search"]
    g.add_node("pdf_retrieve", pdf_retrieve)
    g.add_node("join_local", join_local, defer=True)
    g.add_node("web_retrieve", web_retrieve)
    g.add_node("rerank", rerank)
    g.add_node("assemble_context", assemble)
    g.add_node("generate", generate)
    g.add_node("validate", validate)
    g.add_node("empty_context", empty_context)

    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", route_local, [*local_targets, "pdf_retrieve"])
    g.add_edge("code_retrieve" if m.condition == "A" else "resolve", "join_local")
    g.add_edge("pdf_retrieve", "join_local")
    g.add_conditional_edges("join_local", route_after_join,
                            ["web_retrieve", "rerank", "empty_context"])
    g.add_conditional_edges("web_retrieve", route_after_web, ["rerank", "empty_context"])
    g.add_edge("rerank", "assemble_context")
    g.add_edge("assemble_context", "generate")
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


def trace_from_state(s: GraphState, m: RunManifest) -> TraceRecord:
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
                # text kept for final context (judge) and web passages (KTD15 matching)
                keep = it.id in final_ids or it.source_type == "web"
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
        empty_context=s.get("empty_context", False),
        final_context_ids=ctx.ids if ctx else [],
        aliases=ctx.aliases if ctx else {},
        claims=s.get("claims", []),
        summary=answer.summary if answer else None,
        answer=s.get("rendered"),
        validation=s.get("validation", {}),
        errors=s.get("errors", []),
    )
