from pathlib import Path

import pymupdf
from langchain_core.embeddings import DeterministicFakeEmbedding

from dwb.retrieval.pdf_index import PdfCorpus, PdfRetriever, build_pdf_index


def _pdf(path: Path):
    doc = pymupdf.open()
    for text in ("The application object is a callable.", "start_response sends headers."):
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)


def test_two_page_pdf_yields_two_pages_with_version(tmp_path):
    _pdf(tmp_path / "pep3333.pdf")
    corpus = PdfCorpus(corpus_id="wsgi", documents=[
        {"doc_id": "pep3333", "version": "2010-09-26", "path": "pep3333.pdf"}])
    emb = DeterministicFakeEmbedding(size=32)
    idx = build_pdf_index(tmp_path / "idx", corpus, tmp_path, emb, "fake-32")
    assert idx.info.n_items == 2
    items = PdfRetriever(idx).retrieve("headers", 5)
    assert sorted(i.locator.page for i in items) == [1, 2]
    assert {i.locator.doc_version for i in items} == {"2010-09-26"}
    assert all(i.provenance["branch"] == "pdf" for i in items)
    again = build_pdf_index(tmp_path / "idx", corpus, tmp_path, emb, "fake-32")
    assert again.info.index_id == idx.info.index_id and again.info.reused
