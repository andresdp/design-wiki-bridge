from conftest import make_trace

from dwb.eval.deepeval_diag import build_test_case, run_diagnostics


def test_test_case_uses_saved_context_only(record):
    case = build_test_case(record, make_trace(final=("i1", "i2")))
    assert case["retrieval_context"] == ["def a(): return b()", "pdf p2"]
    assert "A calls B" in case["expected_output"]


def test_skipped_without_answer(record):
    t = make_trace().model_copy(update={"answer": None})
    assert run_diagnostics(record, t, "unused")["skipped"]
