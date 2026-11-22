import asyncio
from pathlib import Path

import pytest

from dwb.benchmark.schemas import QuestionRecord
from dwb.graph import build_graph, trace_from_state
from dwb.manifest import load_manifest
from dwb.web.tavily_store import ReplayMiss, TavilyStore
from graph_fakes import SHA, FakeResolver, FakeWiki, components

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_STORE = ROOT / "tests" / "fixtures" / "tavily"
SHARED_DOWNSTREAM = ["join_local", "rerank", "assemble_context", "generate", "validate"]


def manifest(cond, **web):
    m = load_manifest(ROOT / "configs" / "manifests" / f"flask-{cond}-dev.yaml")
    if web:
        m = m.model_copy(update={"web": m.web.model_copy(update=web)})
    return m


def custom_q(question="How does flask redirect status code http semantics work?",
             sources=("code", "pdf", "web")):
    return QuestionRecord(
        id="flask-cus-001", kind="custom", repo="flask", repo_sha=SHA, question=question,
        allowed_sources=list(sources),
        expected_claims=[{"id": "c1", "text": "x"}],
        evidence=[{"id": "e1", "alternatives": [
            {"source_type": "code", "path": "src/flask/app.py", "start_line": 1, "end_line": 2},
            {"source_type": "pdf", "doc_id": "pep3333", "page": 1}]}],
        provenance={"author": "a"},
    )


def public_q():
    return QuestionRecord(id="flask-pub-000", kind="public", repo="flask", repo_sha=SHA,
                          question="What does ensure_ascii control?",
                          provenance={"upstream_split": "flask", "upstream_row_id": "flask:0"})


def run(m, c, q):
    state = asyncio.run(build_graph(c, m).ainvoke({"question": q}))
    return trace_from_state(state, m)


def names(trace):
    return [s.name for s in trace.stages]


def test_condition_a_stages():
    t = run(manifest("A"), components("A", pdf=False), public_q())
    assert names(t) == ["prepare", "code_retrieve", *SHARED_DOWNSTREAM]
    assert not any(n.startswith("wiki") for n in names(t))
    assert t.final_context_ids and t.validation["n_unresolved_ids"] == 0
    assert t.claims[0].evidence_ids == [t.aliases["E1"], t.aliases["E2"]]


def test_condition_b_stages_and_identical_downstream_config():
    m_a, m_b = manifest("A"), manifest("B")
    q = custom_q(sources=("code", "pdf"))
    ta = run(m_a, components("A"), q)
    tb = run(m_b, components("B"), q)
    nb = names(tb)
    assert {"wiki_search", "wiki_read", "resolve"} <= set(nb)
    assert "code_retrieve" not in nb
    shared = {s.name: s.extra for s in ta.stages if s.name in ("rerank", "generate")}
    for s in tb.stages:
        if s.name in shared:
            for key in ("backend", "model", "prompt_hash"):
                assert s.extra.get(key) == shared[s.name].get(key)


def test_condition_b_with_pdf_runs_each_shared_stage_once():
    t = run(manifest("B"), components("B"), custom_q(sources=("code", "pdf")))
    for n in ("join_local", "rerank", "generate"):
        assert names(t).count(n) == 1


def test_condition_b_empty_wiki_still_uses_pdf():
    c = components("B", wiki=FakeWiki([], []), resolver=FakeResolver([]))
    t = run(manifest("B"), c, custom_q(sources=("code", "pdf")))
    assert not t.empty_context
    assert t.final_context_ids
    assert all(t.evidence[i]["source_type"] == "pdf" for i in t.final_context_ids)


def test_public_item_empty_wiki_is_explicit_empty_context_without_vector_fallback():
    c = components("B", wiki=FakeWiki([], []), resolver=FakeResolver([]))
    c.code_retriever = pytest.fail  # would blow up if anything fell back to vector retrieval
    t = run(manifest("B"), c, public_q())
    assert t.empty_context and t.final_context_ids == [] and t.claims == []
    assert names(t)[-1] == "empty_context"
    assert "generate" not in names(t)


def test_public_item_never_triggers_web():
    c = components("A", web_store=TavilyStore(FIXTURE_STORE, "fixture-store", mode="replay"))
    t = run(manifest("A"), c, public_q())
    assert not t.web_triggered and "web_retrieve" not in names(t)


def test_custom_item_allowing_web_triggers_replay():
    c = components("A", web_store=TavilyStore(FIXTURE_STORE, "fixture-store", mode="replay"))
    m = manifest("A", max_results=3, exclude_domains=[])  # the fixture was recorded without
    t = run(m, c, custom_q("flask redirect status code http semantics"))
    assert t.web_triggered and "web_retrieve" in names(t)
    assert t.candidates["web"]
    assert any(t.evidence[i]["source_type"] == "web" for i in t.candidates["reranked"])


def test_manifest_exclude_domains_reach_tavily_and_the_replay_key(tmp_path):
    from test_tavily_store import StubClient

    stub = StubClient()
    store = TavilyStore(tmp_path, "s", mode="record", client=stub)
    m = manifest("A")
    assert "flask.palletsprojects.com" in m.web.exclude_domains
    run(m, components("A", web_store=store), custom_q())
    assert stub.calls[0][2]["exclude_domains"] == sorted(m.web.exclude_domains)
    # a replay without the exclusions does not match the recorded request
    unfiltered = manifest("A", exclude_domains=[])
    with pytest.raises(ReplayMiss):
        run(unfiltered, components("A", web_store=TavilyStore(tmp_path, "s", mode="replay")),
            custom_q())


def test_replay_miss_raises_for_runner_to_record(tmp_path):
    c = components("A", web_store=TavilyStore(tmp_path, "empty", mode="replay"))
    with pytest.raises(ReplayMiss):
        run(manifest("A"), c, custom_q())


def test_wiki_refs_in_trace_but_never_in_context_or_claims():
    t = run(manifest("B"), components("B"), custom_q(sources=("code", "pdf")))
    assert t.wiki_section_refs == ["openwiki/request.md#dispatch"]
    assert t.unresolved_refs and t.unresolved_refs[0]["raw"] == "mystery()"
    for i in t.final_context_ids:
        assert t.evidence[i]["source_type"] in ("code", "pdf")
        assert "openwiki/" not in str(t.evidence[i]["locator"])
        assert "dispatch_request" not in t.evidence[i].get("text", "")
