import re
from pathlib import Path

import pytest

from dwb.web.tavily_store import (
    ReplayMiss,
    TavilyStore,
    normalize_search,
    request_key,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tavily"


class StubClient:
    def __init__(self):
        self.calls = []

    def search(self, query, **kw):
        self.calls.append(("search", query, kw))
        return {"query": query, "results": [
            {"url": "https://peps.python.org/pep-3333/", "title": "PEP 3333",
             "content": "The application object is simply a callable object.", "score": 0.9},
            {"url": "https://example.org/wsgi", "title": "WSGI notes",
             "content": "start_response must be called before the body.", "score": 0.5},
        ]}

    def extract(self, urls, **kw):
        self.calls.append(("extract", urls, kw))
        return {"results": [{"url": u, "raw_content": f"text of {u}"} for u in urls]}


def test_record_persists_once_and_replay_is_identical(tmp_path):
    stub = StubClient()
    rec = TavilyStore(tmp_path, "s1", mode="record", client=stub)
    items = rec.search("wsgi application callable", max_results=2)
    assert len(list((tmp_path / "s1").glob("*.json"))) == 1
    assert stub.calls[0][2]["include_answer"] is False
    again = rec.search("wsgi application callable", max_results=2)  # recorded -> no new call
    assert len(stub.calls) == 1 and again == items
    replay = TavilyStore(tmp_path, "s1", mode="replay").search(
        "wsgi application callable", max_results=2)
    assert replay == items
    assert [i.id for i in replay] == [i.id for i in items]  # IDs match record time
    assert items[0].locator.passage_index == 0 and items[1].locator.passage_index == 1
    assert items[0].locator.capture_id.startswith("cap_")


def test_replay_miss_names_the_key(tmp_path):
    store = TavilyStore(tmp_path, "empty", mode="replay")
    with pytest.raises(ReplayMiss) as e:
        store.search("never recorded")
    assert e.value.key == request_key(normalize_search("never recorded"))
    assert e.value.key in str(e.value)


def test_parameter_order_and_whitespace_share_a_key():
    a = normalize_search("  wsgi   spec ", include_domains=["b.org", "A.org"], max_results=5)
    b = normalize_search("wsgi spec", max_results=5, include_domains=["a.org", "b.org"])
    assert request_key(a) == request_key(b)
    assert request_key(a) != request_key(normalize_search("wsgi spec", max_results=6))


def test_live_mode_persists_nothing(tmp_path):
    store = TavilyStore(tmp_path, "live", mode="live", client=StubClient())
    assert store.extract(["https://a.org"])[0].text == "text of https://a.org"
    assert not (tmp_path / "live").exists()
    assert store.last_call["source"] == "live"


def test_fixture_store_replays_offline():
    items = TavilyStore(FIXTURES, "fixture-store", mode="replay").search(
        "flask redirect status code http semantics", max_results=3)
    assert items and all(i.source_type == "web" for i in items)


def test_no_research_endpoint_in_source():
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    assert not re.search(r"\.research\s*\(", src)


def test_excluded_domains_are_filtered_locally_too(tmp_path):
    from dwb.web.tavily_store import is_excluded

    assert is_excluded("https://flask.palletsprojects.com/en/latest/", ["palletsprojects.com"])
    assert is_excluded("https://PalletsProjects.com/x", ["palletsprojects.com"])
    assert not is_excluded("https://notpalletsprojects.com/x", ["palletsprojects.com"])
    assert not is_excluded("https://peps.python.org/", [])

    class Leaky(StubClient):
        def search(self, query, **kw):
            out = super().search(query, **kw)
            out["results"].insert(0, {"url": "https://docs.example.org/api", "title": "own",
                                      "content": "the project's own docs", "score": 1.0})
            return out

    store = TavilyStore(tmp_path, "s", mode="record", client=Leaky())
    items = store.search("wsgi", max_results=3, exclude_domains=["example.org"])
    assert [i.locator.url for i in items] == ["https://peps.python.org/pep-3333/"]
    assert items[0].locator.passage_index == 1  # capture position kept: IDs stay stable
    assert store.last_call["excluded_urls"] == ["https://docs.example.org/api",
                                                "https://example.org/wsgi"]


def test_record_write_is_atomic_and_corrupt_records_fail_clearly(tmp_path):
    store = TavilyStore(tmp_path, "s", mode="record", client=StubClient())
    store.search("wsgi", max_results=2)
    files = list((tmp_path / "s").iterdir())
    assert len(files) == 1 and files[0].suffix == ".json"  # no .tmp left behind
    files[0].write_text('{"key": "trunc')
    with pytest.raises(ValueError, match="corrupt Tavily record"):
        TavilyStore(tmp_path, "s", mode="replay").search("wsgi", max_results=2)
