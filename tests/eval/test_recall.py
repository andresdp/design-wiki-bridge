from conftest import make_trace

from dwb.eval.recall import recall


def test_recall_at_candidates_and_final_from_same_trace(record):
    t = make_trace(final=("i1",), cands=("i1", "i2"))
    r = recall(record, t)
    assert r.candidates == 1.0 and r.final == 0.5
    assert r.satisfied_final == ["e1"]


def test_alternative_counts(record):
    t = make_trace(final=("i3",), cands=("i3",))
    assert recall(record, t).final == 0.5


def test_unannotated_record_gives_none(record):
    rec = record.model_copy(update={"evidence": []})
    assert recall(rec, make_trace()).final is None
