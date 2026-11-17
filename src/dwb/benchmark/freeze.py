"""Test-set freeze by content hash (R12)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dwb.benchmark.schemas import QuestionRecord, load_records


def records_hash(records: list[QuestionRecord]) -> str:
    """sha256 over canonical JSON of the records sorted by ID (order-independent)."""
    h = hashlib.sha256()
    for r in sorted(records, key=lambda r: r.id):
        h.update(json.dumps(r.model_dump(mode="json"), sort_keys=True).encode())
        h.update(b"\n")
    return h.hexdigest()


def file_hash(path: str | Path) -> str:
    return records_hash(load_records(path))


def freeze(path: str | Path) -> str:
    """Write ``<path>.sha256`` next to the record file and return the hash."""
    digest = file_hash(path)
    Path(f"{path}.sha256").write_text(digest + "\n")
    return digest


class FrozenSetMismatch(ValueError):
    pass


def verify(path: str | Path, expected: str) -> None:
    actual = file_hash(path)
    if actual != expected:
        raise FrozenSetMismatch(f"question set {path} hash {actual} != manifest {expected}")


def check_disjoint(dev: list[QuestionRecord], test: list[QuestionRecord]) -> None:
    overlap = {r.id for r in dev} & {r.id for r in test}
    q_overlap = {r.question for r in dev} & {r.question for r in test}
    if overlap or q_overlap:
        raise ValueError(
            f"dev/test overlap: ids={sorted(overlap)} questions={len(q_overlap)}"
        )
