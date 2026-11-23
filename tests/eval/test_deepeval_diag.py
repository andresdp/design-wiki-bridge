import pytest

from conftest import make_trace
from dwb.eval.deepeval_diag import build_test_case, run_diagnostics


def test_test_case_uses_saved_context_only(record):
    case = build_test_case(record, make_trace(final=("i1", "i2")))
    assert case["retrieval_context"] == ["def a(): return b()", "pdf p2"]
    assert "A calls B" in case["expected_output"]


def test_skipped_without_answer(record):
    t = make_trace().model_copy(update={"answer": None})
    assert run_diagnostics(record, t, "unused")["skipped"]


class StubMetric:
    def __init__(self, name, calls, cost=0.01):
        self.name, self.calls, self.evaluation_cost = name, calls, cost
        self.score = None

    def measure(self, case):
        self.calls.append(self.name)
        self.score = 0.8


def stub_factory(calls, cost=0.01):
    return lambda name, model: StubMetric(name, calls, cost)


def control():
    from dwb.benchmark.schemas import QuestionRecord

    return QuestionRecord(id="ctl", kind="control", repo="flask", repo_sha="x",
                          question="Unanswerable?", allowed_sources=["code"],
                          reference_answer="Not answerable.", provenance={"author": "x"})


def test_control_runs_faithfulness_only():
    calls = []
    out = run_diagnostics(control(), make_trace(qid="ctl"), "m",
                          metric_factory=stub_factory(calls))
    assert calls == ["faithfulness"]
    assert out["contextual_recall"] is None and out["contextual_recall_skipped"]
    assert out["faithfulness"] == 0.8 and out["faithfulness_cost"] == 0.01


def test_answerable_runs_both_and_records_cost(record):
    calls = []
    out = run_diagnostics(record, make_trace(), "m", metric_factory=stub_factory(calls, 0.02))
    assert calls == ["contextual_recall", "faithfulness"]
    assert out["contextual_recall_cost"] == 0.02 and out["skipped"] == ""


def test_failed_trace_gets_no_row_and_empty_context_is_skipped(record):
    calls = []
    f = stub_factory(calls)
    assert run_diagnostics(record, make_trace(errors=["boom"]), "m", metric_factory=f) is None
    out = run_diagnostics(record, make_trace(final=()), "m", metric_factory=f)
    assert out["skipped"] == "empty final context" and calls == []


def test_metric_error_is_recorded_not_raised(record):
    class Boom(StubMetric):
        def measure(self, case):
            raise RuntimeError("judge down")

    out = run_diagnostics(record, make_trace(), "m",
                          metric_factory=lambda n, m: Boom(n, []))
    assert out["faithfulness"] is None and "judge down" in out["faithfulness_error"]


def test_summary_per_condition_and_subset(record):
    from dwb.eval.deepeval_diag import deepeval_summary

    calls = []
    rows = []
    for cond in "AB":
        for rec, subset in ((record, "code+pdf"), (control(), "control")):
            d = run_diagnostics(rec, make_trace(cond, qid=rec.id), "m",
                                metric_factory=stub_factory(calls))
            rows.append({"question_id": rec.id, "condition": cond, "subset": subset, **d})
    s = {(r["condition"], r["subset"], r["metric"]): r for r in deepeval_summary(rows)}
    assert s[("A", "control", "contextual_recall")]["n_scored"] == 0
    assert s[("A", "control", "contextual_recall")]["n_skipped"] == 1
    assert s[("B", "all", "faithfulness")]["n_scored"] == 2
    assert s[("A", "all", "faithfulness")]["evaluation_cost"] == 0.02


def test_cache_reuses_a_row_and_skips_metric_calls(record, tmp_path):
    from dwb.eval.claims_judge import JudgeCache

    cache = JudgeCache(tmp_path / "de.jsonl")
    calls = []
    first = run_diagnostics(record, make_trace(), "m", metric_factory=stub_factory(calls),
                            cache=cache)
    again = run_diagnostics(record, make_trace(), "m", metric_factory=stub_factory(calls),
                            cache=JudgeCache(tmp_path / "de.jsonl"))
    assert calls == ["contextual_recall", "faithfulness"]  # second call measured nothing
    assert first["cached"] is False and again["cached"] is True
    assert again["faithfulness"] == first["faithfulness"]
    other = run_diagnostics(record, make_trace(final=("i1", "i2")), "m",
                            metric_factory=stub_factory(calls), cache=cache)
    assert other["cached"] is False  # a different context is a different test case


def test_rows_with_errors_are_not_cached(record, tmp_path):
    from dwb.eval.claims_judge import JudgeCache

    class Boom(StubMetric):
        def measure(self, case):
            raise RuntimeError("rate limit")

    cache = JudgeCache(tmp_path / "de.jsonl")
    run_diagnostics(record, make_trace(), "m", metric_factory=lambda n, m: Boom(n, []),
                    cache=cache)
    assert cache.data == {}


def test_deepeval_stops_on_a_fatal_error(record):
    from dwb.fatal import FatalAPIError
    from test_fatal import quota_error

    class Broke:
        evaluation_cost = None

        def measure(self, case):
            raise quota_error()

    with pytest.raises(FatalAPIError):
        run_diagnostics(record, make_trace(), "m", metric_factory=lambda n, m: Broke())
