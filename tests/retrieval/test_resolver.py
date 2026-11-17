import json
from pathlib import Path

import pytest

from dwb.repo import RepoReader
from dwb.retrieval.openwiki_client import parse_read, parse_search
from dwb.retrieval.resolver import WikiResolver, attribute_claims
from dwb.retrieval.wiki_types import Section, SectionRef

ROOT = Path(__file__).resolve().parents[2]
TOY = ROOT / "tests" / "fixtures" / "toy_repo"
FX = ROOT / "tests" / "fixtures"
FLASK = ROOT / "artifacts" / "checkouts" / "flask-3.1.2"
FLASK_SHA = "2c1b30d0503cfb064f1cb252e6614a06915a362a"


def section(text="", claims=(), page_sections=None, idx=0, rank=1, anchor="s"):
    ref = SectionRef(page="openwiki/arch/page.md", anchor=anchor, rank=rank)
    return Section(ref=ref, text=text, raw={
        "page_claims": list(claims),
        "page_sections": page_sections if page_sections is not None else [text],
        "section_index": idx})


def resolver(**kw):
    return WikiResolver(RepoReader.from_dir(TOY, sha="toysha"), **kw)


def claim(cid, res, statement="the app runs handlers"):
    return {"id": cid, "statement": statement, "evidence": res}


def test_claim_yields_span_at_pinned_sha():
    sec = section("The app runs handlers.", [claim("c1", ["repo://src/toy/app.py#L10-L20"])])
    res = resolver().resolve([sec], 20)
    [item] = res.items
    loc = item.locator
    assert (loc.path, loc.start_line, loc.end_line, loc.commit) == ("src/toy/app.py", 10, 20,
                                                                    "toysha")
    assert item.provenance["resolution_method"] == "claim_section"
    assert item.provenance["claim_id"] == "c1" and item.provenance["branch"] == "wiki"


@pytest.mark.parametrize("res", ["repo://src/toy/missing.py#L1-L2",
                                 "repo://src/toy/app.py#L30-L400"])
def test_bad_claim_logged_unresolved(res):
    out = resolver().resolve([section("The app runs handlers.", [claim("c1", [res])])], 20)
    assert out.items == []
    assert out.unresolved and out.unresolved[0].raw == res


def test_whole_file_claim_resource_resolves_to_file():
    sec = section("The app runs handlers.", [claim("c1", ["repo://src/toy/wsgi.py"])])
    [item] = resolver().resolve([sec], 20).items
    n = RepoReader.from_dir(TOY).line_count("src/toy/wsgi.py")
    assert (item.locator.start_line, item.locator.end_line) == (1, n)
    assert item.provenance["whole_file"] is True


def test_inline_links_first_and_relative_to_page():
    text = "Run is at [`app.py`](../../src/toy/app.py#L23-L25). The app runs handlers."
    sec = section(text, [claim("c1", ["repo://src/toy/app.py#L1-L5"])])
    out = resolver().resolve([sec], 20)
    assert [i.provenance["resolution_method"] for i in out.items] == ["inline_link",
                                                                      "claim_section"]
    assert out.items[0].locator.start_line == 23


def test_claims_attributed_to_matching_section_or_page():
    pages = ["## Routing\nroute decorator registers handlers in table",
             "## Running\nrun method logs the port and returns"]
    claims = [claim("route", ["repo://src/toy/app.py#L16-L21"],
                    "route decorator registers handlers table"),
              claim("run", ["repo://src/toy/app.py#L23-L25"], "run logs port returns"),
              claim("orphan", ["repo://src/toy/wsgi.py#L6-L9"], "wsgi callable start_response")]
    assert attribute_claims(claims, pages) == {"route": {0}, "run": {1}, "orphan": set()}
    out = resolver().resolve([section(pages[1], claims, pages, idx=1)], 20)
    got = [(i.provenance["resolution_method"], i.provenance["claim_id"]) for i in out.items]
    assert got == [("claim_section", "run"), ("claim_page", "orphan")]


def test_ast_fallback_dotted_symbol():
    out = resolver().resolve([section("Starts via `toy.app.App.run`.")], 20)
    [item] = out.items
    assert (item.locator.start_line, item.locator.end_line) == (23, 25)
    assert item.provenance["resolution_method"] == "ast"
    assert item.provenance["symbol"] == "toy.app.App.run"


def test_path_only_resolves_whole_file():
    out = resolver().resolve([section("See `src/toy/wsgi.py`.")], 20)
    [item] = out.items
    n = RepoReader.from_dir(TOY).line_count("src/toy/wsgi.py")
    assert (item.locator.start_line, item.locator.end_line) == (1, n)
    assert item.provenance["resolution_method"] == "path"


def test_ambiguous_symbol_emits_both_and_records_it():
    out = resolver().resolve([section("Both have a `run` method.")], 20)
    assert {i.provenance["symbol"] for i in out.items} == {"toy.app.App.run",
                                                          "toy.wsgi.Router.run"}
    assert out.ambiguous and out.ambiguous[0]["symbol"] == "run"


def test_ast_not_used_when_claims_resolved():
    sec = section("The app runs handlers via `toy.app.helper`.",
                  [claim("c1", ["repo://src/toy/app.py#L10-L12"])])
    out = resolver().resolve([sec], 20)
    assert {i.provenance["resolution_method"] for i in out.items} == {"claim_section"}


def test_nothing_recognizable_gives_zero_spans_one_unresolved():
    out = resolver().resolve([section("Plain prose without any references at all.")], 20)
    assert out.items == [] and len(out.unresolved) == 1


def test_same_span_from_two_sections_emitted_once_and_cap_applied():
    c = [claim("c1", ["repo://src/toy/app.py#L10-L20"])]
    a = section("The app runs handlers.", c, rank=1, anchor="a")
    b = section("The app runs handlers.", c, rank=2, anchor="b")
    out = resolver().resolve([b, a], 20)
    assert len(out.items) == 1 and out.items[0].provenance["wiki_ref"].endswith("#a")
    many = section("x", [claim(f"c{i}", [f"repo://src/toy/app.py#L{i}-L{i}"], "x")
                         for i in range(1, 30)], ["x"])
    assert len(resolver().resolve([many], 20).items) == 20


def test_oversized_span_split_with_shared_helper():
    sec = section("The app runs handlers.", [claim("c1", ["repo://src/toy/app.py#L1-L33"])])
    out = resolver(max_span_tokens=40).resolve([sec], 50)
    assert len(out.items) > 1
    starts = [i.locator.start_line for i in out.items]
    assert starts[0] == 1 and out.items[-1].locator.end_line == 33


@pytest.mark.skipif(not FLASK.exists(), reason="pinned Flask checkout not present")
def test_traced_question_resolves_to_spike_span():
    """U1 trace: q1's top section resolves to app.py#L879-L920 (spike doc §6)."""
    refs = parse_search(json.loads((FX / "mcp_responses/q1_wsgi_dispatch_search.json")
                                   .read_text()))
    secs = parse_read(json.loads((FX / "mcp_responses/q1_wsgi_dispatch_read.json").read_text()),
                      refs[:1], FX / "openwiki")
    out = WikiResolver(RepoReader.from_git(FLASK, FLASK_SHA)).resolve(secs, 20)
    spans = [(i.locator.path, i.locator.start_line, i.locator.end_line) for i in out.items]
    assert ("src/flask/app.py", 879, 920) in spans
    assert all(i.locator.commit == FLASK_SHA for i in out.items)
    assert out.unresolved == []


def test_claims_pointing_into_the_wiki_are_not_evidence():
    sec = section("The app runs handlers.", [claim("c1", ["repo://openwiki/arch/page.md#L1-L3",
                                                           "repo://src/toy/app.py#L1-L2"])])
    res = resolver().resolve([sec], 20)
    assert [i.locator.path for i in res.items] == ["src/toy/app.py"]
    assert any(u.reason == "points into the wiki itself" for u in res.unresolved)


def test_resolved_spans_get_unique_ranks_in_resolver_order():
    sec = section("The app runs handlers.", [claim("c1", ["repo://src/toy/app.py#L10-L12",
                                                           "repo://src/toy/__init__.py#L1-L1",
                                                           "repo://src/toy/app.py#L1-L2"])])
    res = resolver().resolve([sec], 20)
    assert [i.provenance["rank"] for i in res.items] == [1, 2, 3]
    assert all(i.provenance["section_rank"] == 1 for i in res.items)
    from dwb.rerank import interleave_pool

    # the reranker pool keeps that order instead of re-sorting by path
    assert [i.id for i in interleave_pool(res.items, 40)] == [i.id for i in res.items]
