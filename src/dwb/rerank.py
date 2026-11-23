"""Reranker interface and the LLM listwise backend (KTD3, KTD14).

``LLMListwiseRerank.from_llm`` supplies the library prompt, the ``RankDocuments`` schema,
and the structured-output check. Only the first step of its chain (the ranking call) is
used: the library's ``_parse_ranking`` indexes without bounds checks and
``compress_documents`` cuts to its own ``top_n``. Here the full-pool ranking is parsed
leniently, dropped and duplicated documents are repaired and logged, and only then is the
result cut to ``top_n``. Evidence IDs travel in ``Document.metadata``.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel

from dwb.evidence import EvidenceItem
from dwb.fatal import raise_if_fatal
from dwb.trace import StageRecord
from dwb.usage import approx_tokens, llm_usage


@dataclass
class RerankResult:
    items: list[EvidenceItem]
    stage: StageRecord
    ranking: list[int] = field(default_factory=list)


class Reranker(Protocol):
    name: str

    async def arerank(self, query: str, items: list[EvidenceItem]) -> RerankResult: ...


def interleave_pool(items: list[EvidenceItem], cap: int) -> list[EvidenceItem]:
    """Round-robin across branches by in-branch rank, so no branch wins by position alone."""
    by_branch: dict[str, list[EvidenceItem]] = defaultdict(list)
    for it in items:
        by_branch[str(it.provenance.get("branch", "unknown"))].append(it)
    queues = [
        sorted(v, key=lambda i: (i.provenance.get("rank", 10**9), i.sort_key()))
        for _, v in sorted(by_branch.items())
    ]
    pool: list[EvidenceItem] = []
    depth = 0
    while len(pool) < cap and any(depth < len(q) for q in queues):
        for q in queues:
            if depth < len(q) and len(pool) < cap:
                pool.append(q[depth])
        depth += 1
    return pool


def repair_ranking(raw: list[int], n: int) -> tuple[list[int], dict[str, list[int]]]:
    """Drop out-of-range/negative positions and duplicates; append omitted positions."""
    invalid = [i for i in raw if not (0 <= i < n)]
    seen: set[int] = set()
    order: list[int] = []
    duplicates: list[int] = []
    for i in raw:
        if not (0 <= i < n):
            continue
        if i in seen:
            duplicates.append(i)
            continue
        seen.add(i)
        order.append(i)
    dropped = [i for i in range(n) if i not in seen]
    return order + dropped, {"invalid": invalid, "duplicates": duplicates, "dropped": dropped}


class LLMListwiseReranker:
    name = "llm_listwise"

    def __init__(self, llm: BaseChatModel, *, pool_cap: int = 40, top_n: int = 12,
                 model_id: str | None = None):
        from langchain_classic.retrievers.document_compressors import LLMListwiseRerank

        lib = LLMListwiseRerank.from_llm(llm=llm, top_n=pool_cap)
        # RunnableSequence(assign(ranking=prompt|llm.with_structured_output), _parse_ranking)
        self._ranking_step = lib.reranker.first
        self.pool_cap = pool_cap
        self.top_n = top_n
        self.model_id = model_id or getattr(llm, "model_name", None) or type(llm).__name__

    async def arerank(self, query: str, items: list[EvidenceItem]) -> RerankResult:
        pool = interleave_pool(items, self.pool_cap)
        docs = [it.to_document() for it in pool]
        usage = UsageMetadataCallbackHandler()
        t0 = time.perf_counter()
        error = None
        raw: list[int] = []
        if docs:
            try:
                out = await self._ranking_step.ainvoke(
                    {"documents": docs, "query": query}, config={"callbacks": [usage]}
                )
                raw = list(out["ranking"].ranked_document_ids)
            except Exception as e:  # keep the run going; fall back to pool order
                raise_if_fatal(e)  # unless the account is out of credit (dwb.fatal)
                error = f"{type(e).__name__}: {e}"
        latency = (time.perf_counter() - t0) * 1000
        order, issues = repair_ranking(raw, len(docs))
        ranked = [pool[i] for i in order][: self.top_n]
        from langchain_core.messages import HumanMessage

        prompt_approx = [HumanMessage(query), *(HumanMessage(d.page_content) for d in docs)]
        u = llm_usage(usage, prompt_approx if docs else None,
                      str(raw) if docs else None)
        stage = StageRecord(
            name="rerank",
            latency_ms=latency,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            candidate_ids=[i.id for i in ranked],
            count=len(ranked),
            extra={
                "backend": self.name,
                "model": self.model_id,
                "input_count": len(items),
                "pool_ids": [i.id for i in pool],
                "pool_truncated": max(0, len(items) - len(pool)),
                "raw_ranking": raw,
                "prompt_tokens_estimate": approx_tokens([query, *(d.page_content for d in docs)]),
                **u.extra(),
                "error": error,
                **issues,
            },
        )
        return RerankResult(items=ranked, stage=stage, ranking=order)


class StubReranker:
    """Keeps pool order. Used to test that the interface preserves IDs independent of backend."""

    name = "stub"

    def __init__(self, *, pool_cap: int = 40, top_n: int = 12):
        self.pool_cap, self.top_n = pool_cap, top_n

    async def arerank(self, query: str, items: list[EvidenceItem]) -> RerankResult:
        pool = interleave_pool(items, self.pool_cap)
        ranked = pool[: self.top_n]
        return RerankResult(
            items=ranked,
            stage=StageRecord(name="rerank", candidate_ids=[i.id for i in ranked],
                              count=len(ranked), extra={"backend": self.name}),
            ranking=list(range(len(pool))),
        )

