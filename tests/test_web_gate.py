"""The check_local web gate: web runs only when local evidence is judged insufficient."""

import asyncio

import pytest

from dwb.graph import build_graph, trace_from_state
from dwb.trace import TraceRecord
from dwb.web.tavily_store import ReplayMiss, TavilyStore
from graph_fakes import FakeRetriever, components, judge
from test_graph import FIXTURE_STORE, custom_q, manifest, names, public_q

WEB_Q = "flask redirect status code http semantics"  # recorded in the fixture store


def fixture_store():
    return TavilyStore(FIXTURE_STORE, "fixture-store", mode="replay")


def m_fixture(**web):
    # the fixture was recorded with max_results=3 and no exclusions
    return manifest("A", max_results=3, exclude_domains=[], **web)


def run(m, c, q, **kw):
    state = asyncio.run(build_graph(c, m, **kw).ainvoke({"question": q}))
    return trace_from_state(state, m)


class StubStore:
    """A web store answering from a list (or raising), recording its calls."""

    def __init__(self, items=(), error=None):
        self.items, self.error, self.calls = list(items), error, 0
        self.last_call = {}

    def search(self, query, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.items)


def test_sufficient_local_evidence_skips_the_web():
    j = judge(True, "the code shows it")
    t = run(m_fixture(), components("A", web_store=fixture_store(), judge_=j), custom_q(WEB_Q))
    assert "check_local" in names(t) and "web_retrieve" not in names(t)
    assert not t.web_triggered and not t.candidates["web"]
    assert t.web_check["sufficient"] is True and t.web_check["reason"] == "the code shows it"
    assert t.web_check["judged"] and t.web_check["seen_ids"]
    assert t.claims  # answered from local evidence


def test_insufficient_local_evidence_searches_the_web():
    t = run(m_fixture(), components("A", web_store=fixture_store(), judge_=judge(False)),
            custom_q(WEB_Q))
    assert names(t).index("check_local") < names(t).index("web_retrieve")
    assert t.web_triggered and t.candidates["web"]
    assert any(t.evidence[i]["source_type"] == "web" for i in t.candidates["reranked"])
    assert t.web_check["sufficient"] is False


def counting_judge(sufficient):
    calls = []

    def respond(messages):
        calls.append(messages)
        return {"sufficient": sufficient, "reason": "counted"}

    return judge(respond), calls


def test_flag_off_searches_the_web_without_a_judge_call():
    j, calls = counting_judge(True)
    t = run(m_fixture(conditional_web_search=False),
            components("A", web_store=fixture_store(), judge_=j), custom_q(WEB_Q))
    assert "check_local" not in names(t) and t.web_triggered
    assert t.web_check is None and calls == []


def test_public_items_never_reach_the_judge_or_the_web():
    store = StubStore()
    t = run(manifest("A"), components("A", web_store=store, judge_=judge(False)), public_q())
    assert "check_local" not in names(t) and "web_retrieve" not in names(t)
    assert store.calls == 0 and t.web_check is None


def test_no_local_evidence_goes_to_the_web_without_a_judge_call():
    j, calls = counting_judge(True)
    c = components("A", pdf=False, web_store=fixture_store(), judge_=j)
    c.code_retriever = FakeRetriever([])
    t = run(m_fixture(), c, custom_q(WEB_Q))
    assert t.web_check == {"sufficient": False, "reason": "no local evidence", "judged": False,
                           "seen_ids": []}
    assert t.web_triggered and not t.warnings and calls == []


def test_no_evidence_anywhere_ends_at_empty_context():
    c = components("A", pdf=False, web_store=StubStore([]))
    c.code_retriever = FakeRetriever([])
    t = run(manifest("A"), c, custom_q())
    assert t.empty_context and names(t)[-1] == "empty_context"


def test_a_failed_judge_searches_the_web_and_degrades():
    def boom(_messages):
        raise RuntimeError("judge down")

    t = run(m_fixture(), components("A", web_store=fixture_store(), judge_=judge(boom)),
            custom_q(WEB_Q))
    assert t.web_triggered and t.web_check["sufficient"] is None
    assert t.degraded and any(w.startswith("check_local") for w in t.warnings)
    assert not t.errors


def test_record_run_captures_web_even_when_local_evidence_is_sufficient(tmp_path):
    from web.test_tavily_store import StubClient

    stub = StubClient()
    store = TavilyStore(tmp_path, "s", mode="record", client=stub)
    t = run(manifest("A", mode="record"), components("A", web_store=store, judge_=judge(True)),
            custom_q(), capture_all_web=True)
    assert len(stub.calls) == 1 and list((tmp_path / "s").glob("*.json"))
    web = [s for s in t.stages if s.name == "web_retrieve"]
    assert web and web[0].extra["used"] is False
    assert not t.web_triggered and not t.candidates["web"]
    assert all(t.evidence[i]["source_type"] != "web" for i in t.final_context_ids)
    # the capture replays for the other condition, whatever its verdict
    replay = TavilyStore(tmp_path, "s", mode="replay")
    tb = run(manifest("A", mode="replay"), components("A", web_store=replay,
                                                      judge_=judge(False)), custom_q())
    assert tb.web_triggered


def test_capture_only_replay_miss_still_raises(tmp_path):
    with pytest.raises(ReplayMiss):
        run(manifest("A"), components("A", web_store=TavilyStore(tmp_path, "e", mode="replay"),
                                      judge_=judge(True)), custom_q(), capture_all_web=True)


def test_a_failed_capture_does_not_degrade_the_question():
    store = StubStore(error=RuntimeError("tavily down"))
    t = run(manifest("A"), components("A", web_store=store, judge_=judge(True)), custom_q(),
            capture_all_web=True)
    (web,) = [s for s in t.stages if s.name == "web_retrieve"]
    assert "tavily down" in web.extra["error"] and web.extra["used"] is False
    assert not t.warnings and not t.web_triggered


def test_gate_without_a_judge_is_a_build_error():
    c = components("A", web_store=StubStore())
    c.judge = None
    with pytest.raises(ValueError, match="judge"):
        build_graph(c, manifest("A"))
    build_graph(c, manifest("A", conditional_web_search=False))  # no gate, no judge needed


def test_both_conditions_use_the_same_judge_configuration():
    q = custom_q(WEB_Q)
    ta = run(m_fixture(), components("A", web_store=fixture_store()), q)
    mb = manifest("B", max_results=3, exclude_domains=[])
    tb = run(mb, components("B", web_store=fixture_store()), q)
    ca = next(s for s in ta.stages if s.name == "check_local").extra
    cb = next(s for s in tb.stages if s.name == "check_local").extra
    assert (ca["model"], ca["prompt_hash"]) == (cb["model"], cb["prompt_hash"])


def test_web_check_survives_the_trace_round_trip():
    t = run(m_fixture(), components("A", web_store=fixture_store(), judge_=judge(True)),
            custom_q(WEB_Q))
    again = TraceRecord.model_validate_json(t.model_dump_json())
    assert again.web_check == t.web_check
