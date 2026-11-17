"""Condition B end to end: real OpenWiki MCP server + resolver on the pinned Flask snapshot,
fake reranker/generator models (no network). Skipped when the local artifacts are absent."""

import asyncio
import shutil
from pathlib import Path

import pytest

from dwb.repo import RepoReader
from dwb.retrieval.openwiki_client import OpenWikiClient, restore_snapshot
from dwb.retrieval.resolver import WikiResolver
from dwb.runner import run_questions
from dwb.trace import RunDir, read_traces
from graph_fakes import components
from test_graph import manifest, public_q

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT / "artifacts" / "checkouts" / "flask-3.1.2"
SNAP = ROOT / "artifacts" / "wikis" / "wiki-flask-52e1119f2d9c"
SHA = "2c1b30d0503cfb064f1cb252e6614a06915a362a"

pytestmark = pytest.mark.skipif(
    not (CHECKOUT.exists() and SNAP.exists() and shutil.which("openwiki")),
    reason="needs the pinned Flask checkout, its wiki snapshot, and openwiki 0.9.0",
)


def test_condition_b_real_wiki_to_cited_answer(tmp_path):
    m = manifest("B")
    restore_snapshot(SNAP, CHECKOUT)
    c = components("B")
    c.wiki = OpenWikiClient(CHECKOUT)
    c.resolver = WikiResolver(RepoReader.from_git(CHECKOUT, SHA))
    q = public_q().model_copy(update={
        "question": "How does Flask's WSGI entry point create the request context and dispatch "
                    "the request to the view function?"})
    rd = RunDir.start(m, tmp_path)
    summary = asyncio.run(run_questions(m, c, [q], rd))
    [t] = read_traces(rd.traces_path)
    names = [s.name for s in t.stages]
    assert ["wiki_search", "wiki_read", "resolve"] == [n for n in names if n.startswith(
        ("wiki", "resolve"))]
    assert t.wiki_section_refs[0].startswith("openwiki/")
    assert t.final_context_ids and not t.errors and not t.empty_context
    for i in t.final_context_ids:
        ev = t.evidence[i]
        assert ev["source_type"] == "code" and ev["locator"]["commit"] == SHA
        assert not ev["locator"]["path"].startswith("openwiki/")  # wiki text never cited (R3)
    assert ("src/flask/app.py", 879, 920) in {
        (t.evidence[i]["locator"]["path"], t.evidence[i]["locator"]["start_line"],
         t.evidence[i]["locator"]["end_line"]) for i in t.candidates["repo"]}
    assert summary["unresolved_citation_ids"] == 0
    assert summary["run_stages"][0]["name"] == "wiki_server_start"
