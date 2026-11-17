"""Fake retrievers, wiki client, and resolver for graph and runner tests."""

from __future__ import annotations

from pathlib import Path

from dwb.evidence import EvidenceItem, PdfLocator, code_item
from dwb.generate import Generator
from dwb.graph import Components
from dwb.rerank import LLMListwiseReranker
from dwb.retrieval.wiki_types import ResolveResult, Section, SectionRef, UnresolvedRef
from fakes import FakeStructuredChat

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "src" / "dwb" / "prompt_files" / "generate_v1.md"
SHA = "2c1b30d0503cfb064f1cb252e6614a06915a362a"


class FakeRetriever:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    def retrieve(self, query, k):
        self.calls += 1
        return [i.model_copy(deep=True) for i in self.items[:k]]


class FakeWiki:
    def __init__(self, refs=None, sections=None):
        self.refs = refs or []
        self.sections = sections or []

    async def search(self, query, k):
        return self.refs[:k]

    async def read(self, refs):
        wanted = {r.ref for r in refs}
        return [s for s in self.sections if s.ref.ref in wanted]


class FakeResolver:
    def __init__(self, items):
        self.items = items

    def resolve(self, sections, max_spans):
        if not sections:
            return ResolveResult(unresolved=[])
        return ResolveResult(items=self.items[:max_spans], unresolved=[
            UnresolvedRef(section=sections[0].ref.ref, raw="mystery()", reason="no match")])


def code_items(n=3, branch="vector"):
    return [code_item(SHA, f"src/flask/m{i}.py", 1, 10, f"def f{i}(): pass", branch=branch,
                      rank=i + 1) for i in range(n)]


def pdf_items(n=2):
    return [EvidenceItem(source_type="pdf", text=f"pep page {p}",
                         locator=PdfLocator(doc_id="pep3333", doc_version="1.0.1", page=p),
                         provenance={"branch": "pdf", "rank": p}) for p in range(1, n + 1)]


def wiki_refs():
    return [SectionRef(page="openwiki/request.md", anchor="dispatch", rank=1, score=3.2)]


def wiki_sections():
    return [Section(ref=wiki_refs()[0], text="Dispatch goes through `Flask.dispatch_request`.",
                    claim_evidence=["repo://src/flask/app.py#L10-L20"])]


def cite_all(messages):
    """Fake generator: one claim citing E1 (and E2 when present)."""
    human = messages[-1].content
    ids = [a for a in ("E1", "E2") if f"[{a}]" in human]
    return {"claims": [{"text": "It works this way.", "evidence_ids": ids}], "summary": None}


def components(condition, *, wiki=None, resolver=None, pdf=True, web_store=None):
    llm_rank = FakeStructuredChat(lambda m: {"ranked_document_ids": list(range(60))})
    c = Components(
        reranker=LLMListwiseReranker(llm_rank, pool_cap=40, top_n=12, model_id="fake"),
        generator=Generator(FakeStructuredChat(cite_all), PROMPT, model_id="fake"),
        pdf_retriever=FakeRetriever(pdf_items()) if pdf else None,
        web_store=web_store,
    )
    if condition == "A":
        c.code_retriever = FakeRetriever(code_items())
    else:
        c.wiki = wiki if wiki is not None else FakeWiki(wiki_refs(), wiki_sections())
        c.resolver = resolver or FakeResolver(code_items(2, branch="wiki"))
    return c
