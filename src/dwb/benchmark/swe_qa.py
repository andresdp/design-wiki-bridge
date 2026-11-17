"""SWE-QA loader: pinned raw split files -> draft public question records.

SWE-QA rows carry only ``question`` and ``answer``. The row ID is
``<split>:<0-based line index>`` in the pinned file (see the U1 spike doc).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dwb.benchmark.schemas import Provenance, QuestionRecord

DATASET = "swe-qa/SWE-QA-Benchmark"


class SweQaRow(BaseModel):
    row_id: str
    split: str
    index: int
    question: str
    answer: str


class SelectionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int
    reason: str = ""


class Selection(BaseModel):
    """Human-authored selection file: which upstream rows enter a question set, and why."""

    model_config = ConfigDict(extra="forbid")
    split: str
    dataset_revision: str
    file_sha256: str | None = None
    repo: str
    repo_sha: str
    id_prefix: str
    criteria: str = ""
    include: list[SelectionEntry] = Field(default_factory=list)
    exclude: list[SelectionEntry] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Selection:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


class Exclusion(BaseModel):
    row_id: str
    reason: str


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_split(path: str | Path, split: str, expected_sha256: str | None = None) -> list[SweQaRow]:
    path = Path(path)
    if expected_sha256 and file_sha256(path) != expected_sha256:
        raise ValueError(f"{path} sha256 does not match the pinned value")
    rows = []
    for i, line in enumerate(path.read_text().splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        rows.append(
            SweQaRow(row_id=f"{split}:{i}", split=split, index=i,
                     question=d["question"], answer=d["answer"])
        )
    return rows


def fetch_split(split_file: str, revision: str, local_dir: str | Path) -> Path:
    """Download one split file at a pinned dataset revision (network)."""
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(DATASET, split_file, repo_type="dataset", revision=revision,
                        local_dir=str(local_dir))
    )


def build_drafts(
    selection: Selection, rows: list[SweQaRow]
) -> tuple[list[QuestionRecord], list[Exclusion]]:
    """Draft public records for selected rows. ``allowed_sources`` is always ``[code]`` (R8).

    Expected claims and evidence spans are left empty for human annotation.
    """
    by_index = {r.index: r for r in rows}
    included = {e.row for e in selection.include}
    clash = included & {e.row for e in selection.exclude}
    if clash:
        raise ValueError(f"rows both included and excluded: {sorted(clash)}")
    records, exclusions = [], []
    for e in selection.include:
        if e.row not in by_index:
            raise ValueError(f"selection row {e.row} not in split {selection.split}")
        r = by_index[e.row]
        records.append(
            QuestionRecord(
                id=f"{selection.id_prefix}-{r.index:03d}",
                kind="public",
                repo=selection.repo,
                repo_sha=selection.repo_sha,
                question=r.question,
                reference_answer=r.answer,
                allowed_sources=["code"],
                provenance=Provenance(
                    upstream_dataset=DATASET,
                    upstream_revision=selection.dataset_revision,
                    upstream_split=r.split,
                    upstream_row_id=r.row_id,
                ),
                notes=e.reason,
            )
        )
    for e in selection.exclude:
        exclusions.append(Exclusion(row_id=f"{selection.split}:{e.row}", reason=e.reason))
    return records, exclusions
