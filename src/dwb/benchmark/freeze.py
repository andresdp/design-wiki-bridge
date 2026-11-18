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


def sidecar_path(path: str | Path) -> Path:
    """Where a frozen record file's hash lives: ``<path>.sha256``."""
    return Path(f"{path}.sha256")


def freeze(path: str | Path) -> str:
    """Write ``<path>.sha256`` next to the record file and return the hash."""
    digest = file_hash(path)
    sidecar_path(path).write_text(digest + "\n")
    return digest


class FrozenSetMismatch(ValueError):
    pass


def verify(path: str | Path, expected: str) -> None:
    actual = file_hash(path)
    if actual != expected:
        raise FrozenSetMismatch(f"question set {path} hash {actual} != manifest {expected}")


def find_overlaps(dev: list[QuestionRecord], test: list[QuestionRecord]) -> list[str]:
    """Shared IDs, question texts, and SWE-QA upstream rows between dev and test (empty = OK)."""
    out = []
    ids = sorted({r.id for r in dev} & {r.id for r in test})
    if ids:
        out.append(f"shared ids: {', '.join(ids)}")
    texts = {r.question for r in dev} & {r.question for r in test}
    if texts:
        shared = sorted(r.id for r in test if r.question in texts)
        out.append(f"shared question text: {', '.join(shared)}")

    def rows(rs: list[QuestionRecord]) -> set[str]:
        return {r.provenance.upstream_row_id for r in rs if r.provenance.upstream_row_id}

    upstream = sorted(rows(dev) & rows(test))
    if upstream:
        out.append(f"shared SWE-QA rows: {', '.join(upstream)}")
    return out


def check_disjoint(dev: list[QuestionRecord], test: list[QuestionRecord]) -> None:
    overlaps = find_overlaps(dev, test)
    if overlaps:
        raise ValueError(f"dev/test overlap: {'; '.join(overlaps)}")
