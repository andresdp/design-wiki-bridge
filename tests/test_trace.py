from pathlib import Path

import pytest

from dwb.manifest import load_manifest
from dwb.trace import AnswerClaim, RunDir, StageRecord, TraceRecord, read_traces

EXAMPLES = Path(__file__).resolve().parents[1] / "configs" / "manifests"


def _rec(i: int, **kw) -> TraceRecord:
    return TraceRecord(
        question_id=f"q{i}",
        run_id="r",
        condition="A",
        stages=[StageRecord(name="prepare", latency_ms=1.5, count=0)],
        claims=[AnswerClaim(text=f"claim {i}", cited_aliases=["E1"], evidence_ids=["ev_x"])],
        answer=f"answer {i}",
        **kw,
    )


def test_append_three_and_reload_in_order(tmp_path):
    rd = RunDir.start(load_manifest(EXAMPLES / "flask-A-dev.yaml"), tmp_path)
    recs = [_rec(i) for i in range(3)]
    with rd.writer() as w:
        for r in recs:
            w.append(r)
    assert read_traces(rd.traces_path) == recs
    assert load_manifest(rd.manifest_path).run_id == "flask-A-dev-002"


def test_error_trace_without_answer_round_trips(tmp_path):
    rec = TraceRecord(question_id="q", run_id="r", condition="B", errors=["ReplayMiss: key"])
    p = tmp_path / "t.jsonl"
    from dwb.trace import TraceWriter

    with TraceWriter(p) as w:
        w.append(rec)
    [back] = read_traces(p)
    assert back.answer is None and back.errors == ["ReplayMiss: key"]


def test_run_dir_refuses_to_clobber(tmp_path):
    m = load_manifest(EXAMPLES / "flask-A-dev.yaml")
    rd = RunDir.start(m, tmp_path)
    rd.write_summary({"questions": 0})
    with pytest.raises(FileExistsError):
        RunDir.start(m, tmp_path)


def test_uncited_claim_flag():
    assert AnswerClaim(text="x").uncited
    assert not AnswerClaim(text="x", cited_aliases=["E1"]).uncited
