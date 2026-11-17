"""Shared PDF index: one evidence item per page (KTD5)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, ConfigDict

from dwb.evidence import EvidenceItem, PdfLocator
from dwb.log import get_logger
from dwb.retrieval.vector_store import VectorIndex, make_index_id

LOG = get_logger("index")


class PdfDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    version: str
    path: str
    title: str = ""


class PdfCorpus(BaseModel):
    """``configs/pdfs/<corpus>.yaml``: the user-chosen documents for one repository."""

    model_config = ConfigDict(extra="forbid")
    corpus_id: str
    documents: list[PdfDoc]

    @classmethod
    def load(cls, path: str | Path) -> PdfCorpus:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def fingerprint(self, base_dir: Path) -> list[dict]:
        return [
            {"doc_id": d.doc_id, "version": d.version,
             "sha256": hashlib.sha256((base_dir / d.path).read_bytes()).hexdigest()}
            for d in sorted(self.documents, key=lambda d: d.doc_id)
        ]


def pdf_pages(doc: PdfDoc, base_dir: Path) -> list[EvidenceItem]:
    import pymupdf

    items = []
    with pymupdf.open(base_dir / doc.path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = page.get_text().strip()
            if not text:
                continue
            items.append(EvidenceItem(
                source_type="pdf", text=text,
                locator=PdfLocator(doc_id=doc.doc_id, doc_version=doc.version, page=i),
                provenance={"title": doc.title} if doc.title else {},
            ))
    return items


def pdf_index_id(corpus: PdfCorpus, base_dir: Path, embedding_model: str) -> str:
    return make_index_id(f"pdf-{corpus.corpus_id}", docs=corpus.fingerprint(base_dir),
                         embedding=embedding_model)


def build_pdf_index(
    root: str | Path,
    corpus: PdfCorpus,
    base_dir: Path,
    embedding: Embeddings,
    embedding_model: str,
) -> VectorIndex:
    index_id = pdf_index_id(corpus, base_dir, embedding_model)
    if VectorIndex.load_info(root, index_id) is not None:
        LOG.info("Index [bold]%s[/] already exists; reusing it, not rebuilding", index_id)
        return VectorIndex.open(root, index_id, embedding)
    items = []
    for d in corpus.documents:
        pages = pdf_pages(d, base_dir)
        LOG.info("Read %s (%s): %d pages with text", d.doc_id, d.version, len(pages))
        items.extend(pages)
    return VectorIndex.build(
        root, index_id=index_id, kind="pdf", items=items, embedding=embedding,
        embedding_model=embedding_model,
        parts={"corpus_id": corpus.corpus_id, "docs": corpus.fingerprint(base_dir)},
    )


class PdfRetriever:
    def __init__(self, index: VectorIndex):
        self.index = index

    def retrieve(self, query: str, k: int) -> list[EvidenceItem]:
        return self.index.search(query, k, branch="pdf")
