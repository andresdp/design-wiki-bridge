"""A failing source or reranker degrades the question; it does not fail it or go unrecorded."""

import asyncio

import pytest

from dwb.graph import build_graph, trace_from_state
from dwb.rerank import LLMListwiseReranker
from dwb.runner import run_manifest
from dwb.trace import read_traces
from fakes import FakeStructuredChat
from graph_fakes import FakeRetriever, FakeWiki, code_items, components, wiki_refs, wiki_sections
from test_graph import custom_q, manifest, names, run
from test_runner import setup


class Boom:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc or RuntimeError("server died")

    def retrieve(self, query, k):
        raise self.exc

    async def search(self, query, k):
        raise self.exc

    async def read(self, refs):
        raise self.exc

    def resolve(self, sections, max_spans):
        raise self.exc


def failing_rank(messages):
    raise ValueError("rate limited")


def test_wiki_failure_keeps_pdf_evidence_and_answers():
    c = components("B", wiki=Boom())
    t = run(manifest("B"), c, custom_q(sources=("code", "pdf")))
    assert not t.errors and t.answer
    assert t.warnings == ["wiki_search: RuntimeError: server died"]
    assert t.candidates["pdf"] and not t.candidates["repo"]
    assert all(t.evidence[i]["source_type"] == "pdf" for i in t.final_context_ids)
    st = next(s for s in t.stages if s.name == "wiki_search")
    assert st.extra["error"] == "RuntimeError: server died"


class ReadFails(FakeWiki):
    async def read(self, refs):
        raise RuntimeError("read failed")


def test_wiki_read_and_resolve_failures_are_degraded():
    t = run(manifest("B"), components("B", wiki=ReadFails(wiki_refs(), wiki_sections())),
            custom_q(sources=("code", "pdf")))
    assert t.warnings == ["wiki_read: RuntimeError: read failed"] and t.answer
    assert t.wiki_section_refs  # the search hits stay in the trace
    t = run(manifest("B"), components("B", resolver=Boom()), custom_q(sources=("code", "pdf")))
    assert t.warnings == ["resolve: RuntimeError: server died"] and t.answer


def test_pdf_and_code_failures_are_degraded():
    c = components("A")
    c.pdf_retriever = Boom()
    t = run(manifest("A"), c, custom_q(sources=("code", "pdf")))
    assert t.warnings == ["pdf_retrieve: RuntimeError: server died"] and t.answer
    c = components("A")
    c.code_retriever = Boom()
    t = run(manifest("A"), c, custom_q(sources=("code", "pdf")))
    assert t.warnings[0].startswith("code_retrieve:") and t.candidates["pdf"]


def test_every_source_failing_ends_in_empty_context():
    c = components("A")
    c.code_retriever, c.pdf_retriever = Boom(), Boom()
    t = run(manifest("A"), c, custom_q(sources=("code", "pdf")))
    assert t.empty_context and len(t.warnings) == 2 and "generate" not in names(t)


def test_rerank_failure_is_recorded_as_degraded():
    c = components("A", pdf=False)
    c.reranker = LLMListwiseReranker(FakeStructuredChat(failing_rank), pool_cap=40, top_n=12,
                                     model_id="fake")
    t = run(manifest("A"), c, custom_q(sources=("code",)))
    assert not t.errors and t.answer
    assert t.degraded and t.warnings[0].startswith("rerank: ValueError: rate limited")
    assert "fell back to pool order" in t.warnings[0]


def test_nothing_fits_the_budget_ends_in_empty_context():
    c = components("A", pdf=False)
    c.code_retriever = FakeRetriever(code_items(1))
    m = manifest("A")
    m = m.model_copy(update={"budgets": m.budgets.model_copy(update={"context_tokens": 5})})
    state = asyncio.run(build_graph(c, m).ainvoke({"question": custom_q(sources=("code",))}))
    t = trace_from_state(state, m)
    assert t.empty_context and "generate" not in names(t) and not t.claims


def test_run_summary_counts_degraded_questions(tmp_path):
    q = custom_q(sources=("code", "pdf"))
    m = setup(tmp_path, [q])
    c = components("A")
    c.pdf_retriever = Boom()
    s = run_manifest(m, c, base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert s["failed"] == 0 and s["degraded"] == 1
    (t,) = read_traces(tmp_path / "runs" / m.run_id / "traces.jsonl")
    assert t.warnings


def test_replay_miss_still_fails_the_question(tmp_path):
    from dwb.web.tavily_store import ReplayMiss, TavilyStore

    c = components("A", web_store=TavilyStore(tmp_path, "empty", mode="replay"))
    with pytest.raises(ReplayMiss):
        run(manifest("A"), c, custom_q())
