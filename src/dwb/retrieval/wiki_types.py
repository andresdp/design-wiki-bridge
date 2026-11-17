"""Types shared by the OpenWiki client (U7), the resolver (U8), and the graph (U11).

Wiki text never becomes an ``EvidenceItem`` (R3). It only guides discovery and stays in
the trace as section references.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from dwb.evidence import EvidenceItem


class SectionRef(BaseModel):
    page: str  # e.g. openwiki/architecture/request-handling.md
    anchor: str  # heading anchor
    rank: int
    score: float | None = None
    title: str | None = None
    wiki: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.page}#{self.anchor}"


class Section(BaseModel):
    ref: SectionRef
    text: str
    claim_evidence: list[str] = Field(default_factory=list)  # repo://path#Lx-Ly
    sources: list[str] = Field(default_factory=list)  # frontmatter sources
    raw: dict[str, Any] = Field(default_factory=dict)


class UnresolvedRef(BaseModel):
    section: str
    raw: str
    reason: str


class ResolveResult(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)
    unresolved: list[UnresolvedRef] = Field(default_factory=list)
    ambiguous: list[dict[str, Any]] = Field(default_factory=list)


class WikiClient(Protocol):
    async def search(self, query: str, k: int) -> list[SectionRef]: ...
    async def read(self, refs: list[SectionRef]) -> list[Section]: ...


class Resolver(Protocol):
    def resolve(self, sections: list[Section], max_spans: int) -> ResolveResult: ...
