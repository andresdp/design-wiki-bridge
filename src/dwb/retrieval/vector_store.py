"""Persisted Chroma collections of evidence items (KTD5).

Each index lives in ``<root>/<index_id>/`` with an ``index.json`` manifest. An existing
index is reused and never silently rebuilt. Chroma metadata must be flat, so the locator
is stored as JSON and restored on retrieval.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from dwb.evidence import EvidenceItem, parse_locator

BATCH = 256


def make_index_id(prefix: str, **parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True)
    return f"{prefix}-{hashlib.sha256(blob.encode()).hexdigest()[:12]}"


@dataclass
class IndexInfo:
    index_id: str
    kind: str  # code | pdf
    embedding_model: str
    n_items: int
    parts: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reused: bool = False


def _flat(item: EvidenceItem) -> Document:
    md: dict[str, Any] = {
        "evidence_id": item.id,
        "source_type": item.source_type,
        "locator_json": json.dumps(item.locator.model_dump(), sort_keys=True),
    }
    for k, v in item.provenance.items():
        if isinstance(v, str | int | float | bool):
            md[f"prov_{k}"] = v
    return Document(page_content=item.text, metadata=md, id=item.id)


def _unflat(doc: Document) -> EvidenceItem:
    md = doc.metadata
    prov = {k[5:]: v for k, v in md.items() if k.startswith("prov_")}
    return EvidenceItem(
        id=md["evidence_id"], source_type=md["source_type"], text=doc.page_content,
        locator=parse_locator(json.loads(md["locator_json"])), provenance=prov,
    )


def _chroma(index_dir: Path, index_id: str, embedding: Embeddings):
    import chromadb
    from chromadb.config import Settings
    from langchain_chroma import Chroma

    client = chromadb.PersistentClient(
        path=str(index_dir), settings=Settings(anonymized_telemetry=False)
    )
    return Chroma(collection_name=index_id, embedding_function=embedding, client=client,
                  collection_metadata={"hnsw:space": "cosine"})


class VectorIndex:
    def __init__(self, root: Path, info: IndexInfo, embedding: Embeddings):
        self.root = root
        self.info = info
        self._store = _chroma(root / info.index_id, info.index_id, embedding)

    @classmethod
    def build(
        cls,
        root: str | Path,
        *,
        index_id: str,
        kind: str,
        items: list[EvidenceItem],
        embedding: Embeddings,
        embedding_model: str,
        parts: dict[str, Any],
    ) -> VectorIndex:
        root = Path(root)
        existing = cls.load_info(root, index_id)
        if existing is not None:
            existing.reused = True
            return cls(root, existing, embedding)
        info = IndexInfo(index_id=index_id, kind=kind, embedding_model=embedding_model,
                         n_items=len(items), parts=parts)
        idx = cls(root, info, embedding)
        docs = [_flat(i) for i in items]
        for s in range(0, len(docs), BATCH):
            batch = docs[s : s + BATCH]
            idx._store.add_documents(batch, ids=[d.id for d in batch])
        (root / index_id / "index.json").write_text(json.dumps(asdict(info), indent=2))
        return idx

    @staticmethod
    def load_info(root: str | Path, index_id: str) -> IndexInfo | None:
        p = Path(root) / index_id / "index.json"
        if not p.exists():
            return None
        return IndexInfo(**{**json.loads(p.read_text()), "reused": True})

    @classmethod
    def open(cls, root: str | Path, index_id: str, embedding: Embeddings) -> VectorIndex:
        info = cls.load_info(root, index_id)
        if info is None:
            raise FileNotFoundError(f"index {index_id} not found under {root}")
        return cls(Path(root), info, embedding)

    def search(self, query: str, k: int, *, branch: str) -> list[EvidenceItem]:
        hits = self._store.similarity_search_with_score(query, k=k)
        out = []
        for rank, (doc, distance) in enumerate(hits, start=1):
            item = _unflat(doc)
            item.provenance.update(branch=branch, rank=rank, score=float(distance),
                                   score_kind="cosine_distance")
            out.append(item)
        return out
