"""Condition A code index: AST chunks of the pinned repository in Chroma (KTD5)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from langchain_core.embeddings import Embeddings

from dwb.evidence import EvidenceItem
from dwb.log import get_logger
from dwb.repo import RepoReader
from dwb.retrieval.chunking import ChunkingConfig, chunk_repo
from dwb.retrieval.vector_store import VectorIndex, make_index_id

LOG = get_logger("index")


def code_index_id(repo: str, sha: str, cfg: ChunkingConfig, embedding_model: str) -> str:
    return make_index_id(f"code-{repo}", sha=sha, chunking=cfg.config_id(),
                         embedding=embedding_model)


def build_code_index(
    root: str | Path,
    reader: RepoReader,
    repo: str,
    embedding: Embeddings,
    embedding_model: str,
    cfg: ChunkingConfig | None = None,
) -> VectorIndex:
    cfg = cfg or ChunkingConfig()
    index_id = code_index_id(repo, reader.sha, cfg, embedding_model)
    if VectorIndex.load_info(root, index_id) is not None:
        LOG.info("Index [bold]%s[/] already exists; reusing it, not rebuilding", index_id)
        return VectorIndex.open(root, index_id, embedding)
    LOG.info("Chunking %s at %s (AST, max %d tokens per span)", repo, reader.sha[:12],
             cfg.max_span_tokens)
    items = chunk_repo(reader, cfg)
    LOG.info("Chunked into %d spans", len(items))
    return VectorIndex.build(
        root, index_id=index_id, kind="code", items=items, embedding=embedding,
        embedding_model=embedding_model,
        parts={"repo": repo, "sha": reader.sha, "chunking": asdict(cfg)},
    )


class CodeRetriever:
    def __init__(self, index: VectorIndex):
        self.index = index

    def retrieve(self, query: str, k: int) -> list[EvidenceItem]:
        return self.index.search(query, k, branch="vector")
