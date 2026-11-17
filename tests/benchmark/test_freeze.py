import pytest

from dwb.benchmark.freeze import (
    FrozenSetMismatch,
    check_disjoint,
    freeze,
    records_hash,
    verify,
)
from dwb.benchmark.schemas import QuestionRecord, save_records


def rec(i, q=None):
    return QuestionRecord(
        id=f"p-{i}", kind="public", repo="toy", repo_sha="s", question=q or f"q{i}",
        provenance={"upstream_split": "flask", "upstream_row_id": f"flask:{i}"},
    )


def test_hash_order_independent_but_content_sensitive():
    a = [rec(1), rec(2), rec(3)]
    assert records_hash(a) == records_hash(list(reversed(a)))
    changed = [rec(1), rec(2, q="edited"), rec(3)]
    assert records_hash(a) != records_hash(changed)


def test_freeze_and_verify(tmp_path):
    p = tmp_path / "test.jsonl"
    save_records([rec(1), rec(2)], p)
    digest = freeze(p)
    assert (tmp_path / "test.jsonl.sha256").read_text().strip() == digest
    verify(p, digest)
    save_records([rec(1), rec(2, q="edited")], p)
    with pytest.raises(FrozenSetMismatch):
        verify(p, digest)


def test_dev_test_overlap_rejected():
    check_disjoint([rec(1)], [rec(2)])
    with pytest.raises(ValueError, match="overlap"):
        check_disjoint([rec(1)], [rec(1, q="other")])
    with pytest.raises(ValueError, match="overlap"):
        check_disjoint([rec(5, q="same")], [rec(6, q="same")])
