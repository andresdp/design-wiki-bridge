"""Per-question trace records and the file-based run store (R10, KTD13)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dwb.manifest import RunManifest


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    candidate_ids: list[str] = Field(default_factory=list)
    count: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


class AnswerClaim(BaseModel):
    """One generated claim after validation (KTD9)."""

    text: str
    kind: str = "answer"  # "answer" | "gap" (the evidence does not establish this part)
    cited_aliases: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)  # resolved stable IDs
    unresolved_aliases: list[str] = Field(default_factory=list)

    @property
    def uncited(self) -> bool:
        return not self.cited_aliases

    @property
    def is_gap(self) -> bool:
        return self.kind == "gap"


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    run_id: str
    condition: str
    stages: list[StageRecord] = Field(default_factory=list)
    candidates: dict[str, list[str]] = Field(default_factory=dict)  # stage -> evidence IDs
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)  # id -> EvidenceItem dump
    wiki_section_refs: list[str] = Field(default_factory=list)
    unresolved_refs: list[dict[str, Any]] = Field(default_factory=list)
    merged_ids: dict[str, str] = Field(default_factory=dict)  # dropped id -> surviving id
    web_triggered: bool = False  # web evidence entered the candidate pool
    # the check_local web gate: sufficient (None = judge failed), reason, judged, seen_ids;
    # None when the gate did not run (web not allowed, or conditional_web_search off)
    web_check: dict[str, Any] | None = None
    empty_context: bool = False
    final_context_ids: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)  # E1 -> stable id
    claims: list[AnswerClaim] = Field(default_factory=list)
    summary: str | None = None
    answer: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    # degraded but answered: a source or the reranker failed and the question went on
    # without it (KTD-R5: the other branches still contribute)
    warnings: list[str] = Field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return bool(self.warnings)


class TraceWriter:
    """Appends one JSON line per question, flushed and fsynced so a crash keeps completed ones."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = path.open("a", encoding="utf-8")

    def append(self, record: TraceRecord) -> None:
        self._fh.write(record.model_dump_json() + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_traces(path: Path) -> list[TraceRecord]:
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(TraceRecord.model_validate_json(line))
    return out


class RunDir:
    """runs/<run_id>/{manifest.yaml, traces.jsonl, run_summary.json}."""

    def __init__(self, root: Path):
        self.root = root
        self.manifest_path = root / "manifest.yaml"
        self.traces_path = root / "traces.jsonl"
        self.summary_path = root / "run_summary.json"

    @classmethod
    def start(cls, manifest: RunManifest, runs_dir: Path, *, overwrite: bool = False) -> RunDir:
        root = runs_dir / manifest.run_id
        if root.exists() and any(root.iterdir()) and not overwrite:
            raise FileExistsError(f"run directory {root} already exists")
        root.mkdir(parents=True, exist_ok=True)
        rd = cls(root)
        rd.manifest_path.write_text(manifest.to_yaml())
        if overwrite and rd.traces_path.exists():
            rd.traces_path.unlink()
        return rd

    def writer(self) -> TraceWriter:
        return TraceWriter(self.traces_path)

    def write_summary(self, summary: dict[str, Any]) -> None:
        summary = {"written_at": datetime.now(UTC).isoformat(), **summary}
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
