import os
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from dwb.repo import RepoReader
from dwb.retrieval.code_index import CodeRetriever, build_code_index

TOY = Path(__file__).resolve().parents[1] / "fixtures" / "toy_repo"


def _build(root, emb=None, model="fake-64"):
    return build_code_index(root, RepoReader.from_dir(TOY, sha="toysha"), "toy",
                            emb or DeterministicFakeEmbedding(size=64), model)


def test_build_twice_reuses_collection(tmp_path):
    a = _build(tmp_path)
    b = _build(tmp_path)
    assert a.info.index_id == b.info.index_id
    assert not a.info.reused and b.info.reused
    assert a.info.n_items == b.info.n_items > 0
    assert _build(tmp_path, model="other").info.index_id != a.info.index_id


def test_retrieve_k3_valid_locators_and_rank(tmp_path):
    items = CodeRetriever(_build(tmp_path)).retrieve("how does the app run", 3)
    assert len(items) == 3
    assert [i.provenance["rank"] for i in items] == [1, 2, 3]
    scores = [i.provenance["score"] for i in items]
    assert scores == sorted(scores)  # cosine distance ascending == relevance descending
    for i in items:
        assert i.source_type == "code" and i.locator.commit == "toysha"
        assert i.provenance["branch"] == "vector"
        assert i.text == RepoReader.from_dir(TOY).span_text(
            i.locator.path, i.locator.start_line, i.locator.end_line)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_live_openai_embeddings(tmp_path):
    from langchain_openai import OpenAIEmbeddings

    model = "text-embedding-3-small"
    idx = _build(tmp_path, OpenAIEmbeddings(model=model), model)
    hits = CodeRetriever(idx).retrieve("register a route handler", 3)
    assert hits and any("route" in h.text for h in hits)
