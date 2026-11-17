import asyncio
import os

import pytest

from dwb.evidence import code_item
from dwb.rerank import LLMListwiseReranker, StubReranker, interleave_pool, repair_ranking
from fakes import FakeStructuredChat


def items(n, branch="vector"):
    return [code_item("s", f"f{i}.py", 1, 2, f"text {i}", branch=branch, rank=i + 1)
            for i in range(n)]


def run(rr, query, its):
    return asyncio.run(rr.arerank(query, its))


def ranker(ranking, **kw):
    return LLMListwiseReranker(FakeStructuredChat({"ranked_document_ids": ranking}), **kw)


def test_order_follows_ranking_and_ids_survive():
    its = items(3)
    res = run(ranker([2, 0, 1]), "q", its)
    assert [i.id for i in res.items] == [its[2].id, its[0].id, its[1].id]
    for out in res.items:
        assert out.to_document().metadata["evidence_id"] == out.id


def test_omitted_index_appended_and_logged():
    its = items(3)
    res = run(ranker([2, 0]), "q", its)
    assert [i.id for i in res.items] == [its[2].id, its[0].id, its[1].id]
    assert res.stage.extra["dropped"] == [1]


def test_duplicate_index_returned_once_and_logged():
    its = items(3)
    res = run(ranker([1, 1, 0, 2]), "q", its)
    assert [i.id for i in res.items] == [its[1].id, its[0].id, its[2].id]
    assert res.stage.extra["duplicates"] == [1]


def test_full_pool_of_40_returns_12_with_zero_drops():
    its = items(40)
    res = run(ranker(list(reversed(range(40))), pool_cap=40, top_n=12), "q", its)
    assert len(res.items) == 12
    assert res.stage.extra["dropped"] == [] and res.stage.extra["invalid"] == []
    assert res.items[0].id == its[39].id


def test_invalid_positions_logged_and_repaired():
    its = items(3)
    res = run(ranker([45, -1, 0]), "q", its)
    assert res.stage.extra["invalid"] == [45, -1]
    assert [i.id for i in res.items] == [its[0].id, its[1].id, its[2].id]


def test_pool_over_cap_truncated_by_interleaved_rank_before_call():
    a, b = items(30, "vector"), [code_item("s", f"p{i}.py", 1, 2, "p", branch="pdf", rank=i + 1)
                                 for i in range(30)]
    fake = FakeStructuredChat({"ranked_document_ids": list(range(40))})
    rr = LLMListwiseReranker(fake, pool_cap=40, top_n=40)
    res = run(rr, "q", a + b)
    assert res.stage.extra["pool_truncated"] == 20
    pool = res.stage.extra["pool_ids"]
    assert len(pool) == 40
    assert pool[:4] == [b[0].id, a[0].id, b[1].id, a[1].id]  # branches alternate by rank
    prompt = fake.calls[0][0].content
    assert "Document ID: 39" in prompt and "Document ID: 40" not in prompt


def test_usage_and_latency_recorded():
    res = run(ranker([0, 1]), "q", items(2))
    assert res.stage.input_tokens == 100 and res.stage.output_tokens == 10
    assert res.stage.latency_ms > 0
    assert res.stage.extra["backend"] == "llm_listwise"


def test_model_failure_falls_back_to_pool_order():
    def boom(_m):
        raise RuntimeError("provider down")

    its = items(3)
    res = run(LLMListwiseReranker(FakeStructuredChat(boom)), "q", its)
    assert [i.id for i in res.items] == [i.id for i in its]
    assert "provider down" in res.stage.extra["error"]


def test_stub_backend_preserves_ids_through_interface():
    its = items(5)
    res = run(StubReranker(top_n=3), "q", its)
    assert [i.id for i in res.items] == [i.id for i in its[:3]]


def test_repair_and_interleave_units():
    assert repair_ranking([], 2) == ([0, 1], {"invalid": [], "duplicates": [], "dropped": [0, 1]})
    assert interleave_pool([], 5) == []


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_live_determinism_recorded():
    from langchain_openai import ChatOpenAI

    model = os.environ.get("DWB_LIVE_MODEL", "gpt-5.5")
    rr = LLMListwiseReranker(ChatOpenAI(model=model, temperature=0), top_n=5)
    texts = ["Flask routes map URLs to view functions.", "Bananas are yellow.",
             "The WSGI app is a callable.", "Blueprints group routes.", "Rain is wet."]
    its = [code_item("s", f"d{i}.py", 1, 1, t, branch="vector", rank=i + 1)
           for i, t in enumerate(texts)]
    r1 = run(rr, "How are URL routes registered in Flask?", its)
    r2 = run(rr, "How are URL routes registered in Flask?", its)
    print("rankings", r1.ranking, r2.ranking, "equal:", r1.ranking == r2.ranking)
    assert r1.stage.extra["error"] is None
