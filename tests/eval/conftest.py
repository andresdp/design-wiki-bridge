import pytest

from dwb.benchmark.schemas import QuestionRecord
from dwb.trace import AnswerClaim, StageRecord, TraceRecord


def ev(path, s, e, text="code text"):
    return {"source_type": "code", "text": text,
            "locator": {"kind": "code", "commit": "x", "path": path, "start_line": s,
                        "end_line": e}}


@pytest.fixture
def record():
    return QuestionRecord(
        id="q1", kind="custom", repo="flask", repo_sha="x", question="How?",
        allowed_sources=["code", "pdf"],
        expected_claims=[{"id": "c1", "text": "A calls B"}, {"id": "c2", "text": "B returns C"}],
        evidence=[
            {"id": "e1", "alternatives": [
                {"source_type": "code", "path": "a.py", "start_line": 1, "end_line": 5},
                {"source_type": "code", "path": "alt.py", "start_line": 1, "end_line": 5}]},
            {"id": "e2", "alternatives": [{"source_type": "pdf", "doc_id": "d", "page": 2}]},
        ],
        provenance={"author": "x"},
    )


def make_trace(condition="A", qid="q1", final=("i1",), cands=("i1", "i2"), claims=None,
               errors=None):
    evidence = {
        "i1": ev("a.py", 3, 9, "def a(): return b()"),
        "i2": {"source_type": "pdf", "locator": {"doc_id": "d", "page": 2}, "text": "pdf p2"},
        "i3": ev("alt.py", 2, 3, "alt"),
    }
    return TraceRecord(
        question_id=qid, run_id=f"run-{condition}", condition=condition,
        stages=[StageRecord(name="assemble_context", input_tokens=500, latency_ms=2),
                StageRecord(name="generate", input_tokens=600, output_tokens=50, latency_ms=10)],
        candidates={"repo": [c for c in cands if c != "i2"],
                    "pdf": [c for c in cands if c == "i2"]},
        evidence=evidence, final_context_ids=list(final),
        claims=claims if claims is not None else [
            AnswerClaim(text="A calls B", cited_aliases=["E1"], evidence_ids=["i1"]),
            AnswerClaim(text="B returns C", cited_aliases=["E1", "E2"],
                        evidence_ids=["i1", "i2"]),
            AnswerClaim(text="uncited claim")],
        answer="...", errors=errors or [],
    )
