"""Question records (R12) and their validation (R8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dwb.repo import RepoReader

SourceName = Literal["code", "pdf", "web"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnnotatedSpan(_Strict):
    """One acceptable piece of evidence. Fields used depend on ``source_type``."""

    source_type: SourceName
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    doc_id: str | None = None
    page: int | None = None
    url: str | None = None
    passage: str | None = None

    @model_validator(mode="after")
    def _fields_for_type(self) -> AnnotatedSpan:
        if self.source_type == "code":
            if not self.path or self.start_line is None or self.end_line is None:
                raise ValueError("code span needs path, start_line, end_line")
            if self.start_line < 1 or self.end_line < self.start_line:
                raise ValueError(f"bad code range {self.start_line}-{self.end_line}")
        elif self.source_type == "pdf":
            if not self.doc_id or self.page is None or self.page < 1:
                raise ValueError("pdf span needs doc_id and page >= 1")
        elif not self.url:
            raise ValueError("web span needs url")
        return self


class EvidenceRequirement(_Strict):
    """A piece of evidence the answer needs; any one alternative satisfies it (KTD15)."""

    id: str
    alternatives: list[AnnotatedSpan] = Field(min_length=1)
    supports_claims: list[str] = Field(default_factory=list)


class ExpectedClaim(_Strict):
    id: str
    text: str
    material: bool = True
    weight: float = 1.0


class Provenance(_Strict):
    upstream_dataset: str | None = None
    upstream_revision: str | None = None
    upstream_split: str | None = None
    upstream_row_id: str | None = None
    author: str | None = None
    edits: list[str] = Field(default_factory=list)


class QuestionRecord(_Strict):
    id: str = Field(min_length=1)
    # public: SWE-QA; custom: authored, answerable; control: authored, unanswerable from the
    # corpus (the expected outcome is a decline)
    kind: Literal["public", "custom", "control"]
    repo: str
    repo_sha: str
    question: str
    reference_answer: str | None = None
    expected_claims: list[ExpectedClaim] = Field(default_factory=list)
    evidence: list[EvidenceRequirement] = Field(default_factory=list)
    allowed_sources: list[SourceName] = Field(default_factory=lambda: ["code"])
    partial_credit: str = "supported=1, partial=0.5, missing=0"
    provenance: Provenance = Field(default_factory=Provenance)
    notes: str = ""

    @model_validator(mode="after")
    def _kind_rules(self) -> QuestionRecord:
        if "code" not in self.allowed_sources:
            raise ValueError("allowed_sources must include 'code'")
        if self.kind == "public":
            if "web" in self.allowed_sources:
                raise ValueError("public items may not allow 'web' (R8)")
            if not (self.provenance.upstream_split and self.provenance.upstream_row_id):
                raise ValueError("public items need upstream_split and upstream_row_id")
        elif self.kind == "custom":
            if not self.expected_claims:
                raise ValueError("custom items need expected_claims")
            if not self.evidence:
                raise ValueError("custom items need at least one evidence span")
            if not self.provenance.author:
                raise ValueError("custom items need provenance.author")
        else:  # control: nothing in the corpus answers it, and the web may not either
            if self.evidence or self.expected_claims:
                raise ValueError("control items have no evidence and no expected_claims")
            if "web" in self.allowed_sources:
                raise ValueError("control items may not allow 'web'")
            if not self.provenance.author:
                raise ValueError("control items need provenance.author")
        claim_ids = {c.id for c in self.expected_claims}
        for req in self.evidence:
            missing = set(req.supports_claims) - claim_ids
            if missing:
                raise ValueError(f"evidence {req.id} supports unknown claims {sorted(missing)}")
        return self

    @property
    def grounding(self) -> str:
        """The annotated evidence's source types, e.g. ``code``, ``pdf``, ``code+web``;
        ``none`` without annotated evidence (controls, unannotated public items)."""
        types = {a.source_type for req in self.evidence for a in req.alternatives}
        return "+".join(t for t in ("code", "pdf", "web") if t in types) or "none"

    def code_spans(self) -> list[AnnotatedSpan]:
        return [a for r in self.evidence for a in r.alternatives if a.source_type == "code"]


def validate_against_checkout(record: QuestionRecord, reader: RepoReader) -> list[str]:
    """Return problems with the record's code spans at the pinned revision (empty = OK)."""
    problems = []
    if record.repo_sha != reader.sha and reader.git_dir is not None:
        problems.append(f"{record.id}: repo_sha {record.repo_sha} != checkout {reader.sha}")
    for span in record.code_spans():
        n = reader.line_count(span.path)
        if n is None:
            problems.append(f"{record.id}: {span.path} does not exist at {reader.sha}")
        elif span.end_line > n:
            problems.append(f"{record.id}: {span.path}:{span.end_line} past end of file ({n})")
    return problems


def load_records(path: str | Path) -> list[QuestionRecord]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            out.append(QuestionRecord.model_validate_json(line))
    return out


def save_records(records: list[QuestionRecord], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w") as fh:
        for r in records:
            fh.write(json.dumps(r.model_dump(mode="json"), sort_keys=True) + "\n")
