"""Evidence items with deterministic IDs (R1, R2, KTD1).

An evidence item is ID + source type + text + locator. The ID is a short hash of the
source type plus the canonical locator string, so the same span carries the same ID
across conditions and runs.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

SourceType = Literal["code", "pdf", "web"]
SOURCE_ORDER: dict[str, int] = {"code": 0, "pdf": 1, "web": 2}


class _Locator(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CodeLocator(_Locator):
    kind: Literal["code"] = "code"
    commit: str = Field(min_length=1)
    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_range(self) -> CodeLocator:
        if self.start_line > self.end_line:
            raise ValueError(f"start_line {self.start_line} > end_line {self.end_line}")
        if self.path.startswith("/"):
            raise ValueError("code path must be repo-relative")
        return self

    def canonical(self) -> str:
        return f"code:{self.commit}:{self.path}#L{self.start_line}-L{self.end_line}"

    def sort_key(self) -> tuple:
        return (SOURCE_ORDER["code"], self.commit, self.path, self.start_line, self.end_line)


class PdfLocator(_Locator):
    kind: Literal["pdf"] = "pdf"
    doc_id: str = Field(min_length=1)
    doc_version: str = Field(min_length=1)
    page: int = Field(ge=1)

    def canonical(self) -> str:
        return f"pdf:{self.doc_id}@{self.doc_version}#p{self.page}"

    def sort_key(self) -> tuple:
        return (SOURCE_ORDER["pdf"], self.doc_id, self.doc_version, self.page, 0)


class WebLocator(_Locator):
    kind: Literal["web"] = "web"
    url: str = Field(min_length=1)
    capture_id: str = Field(min_length=1)
    passage_index: int = Field(ge=0)

    def canonical(self) -> str:
        return f"web:{self.url}#{self.capture_id}:{self.passage_index}"

    def sort_key(self) -> tuple:
        return (SOURCE_ORDER["web"], self.url, self.capture_id, self.passage_index, 0)


Locator = Annotated[CodeLocator | PdfLocator | WebLocator, Field(discriminator="kind")]
_LOCATOR_ADAPTER: TypeAdapter = TypeAdapter(Locator)


def parse_locator(data: dict[str, Any]) -> CodeLocator | PdfLocator | WebLocator:
    return _LOCATOR_ADAPTER.validate_python(data)


def evidence_id(source_type: str, locator: CodeLocator | PdfLocator | WebLocator) -> str:
    """Stable evidence ID: ``ev_`` + 16 hex chars of sha256(source_type | canonical)."""
    digest = hashlib.sha256(f"{source_type}|{locator.canonical()}".encode()).hexdigest()
    return f"ev_{digest[:16]}"


class EvidenceItem(BaseModel):
    """One unit of citable original evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    source_type: SourceType
    text: str
    locator: Locator
    # branch, rank, score, resolution_method, merged_ids, ... Never wiki text (R3).
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_and_check_id(self) -> EvidenceItem:
        if self.locator.kind != self.source_type:
            raise ValueError(
                f"source_type {self.source_type!r} does not match locator kind "
                f"{self.locator.kind!r}"
            )
        expected = evidence_id(self.source_type, self.locator)
        if not self.id:
            self.id = expected
        elif self.id != expected:
            raise ValueError(f"evidence id {self.id!r} does not match locator ({expected!r})")
        return self

    @property
    def canonical_locator(self) -> str:
        return self.locator.canonical()

    def sort_key(self) -> tuple:
        return self.locator.sort_key()

    def to_document(self) -> Document:
        return Document(
            page_content=self.text,
            id=self.id,
            metadata={
                "evidence_id": self.id,
                "source_type": self.source_type,
                "locator": self.locator.model_dump(),
                "provenance": dict(self.provenance),
            },
        )

    @classmethod
    def from_document(cls, doc: Document) -> EvidenceItem:
        md = doc.metadata
        return cls(
            id=md["evidence_id"],
            source_type=md["source_type"],
            text=doc.page_content,
            locator=parse_locator(md["locator"]),
            provenance=dict(md.get("provenance", {})),
        )


def code_item(
    commit: str,
    path: str,
    start_line: int,
    end_line: int,
    text: str,
    **provenance: Any,
) -> EvidenceItem:
    return EvidenceItem(
        source_type="code",
        text=text,
        locator=CodeLocator(commit=commit, path=path, start_line=start_line, end_line=end_line),
        provenance=provenance,
    )
