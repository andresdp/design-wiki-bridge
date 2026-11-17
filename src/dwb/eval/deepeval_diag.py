"""DeepEval diagnostics (contextual recall, faithfulness) over saved traces (KTD16).

DeepEval runs as a library on the saved context and answer: no tracing, no Confident AI,
no dataset generation. Telemetry is disabled before import.
"""

from __future__ import annotations

import os
from typing import Any

from dwb.benchmark.schemas import QuestionRecord
from dwb.trace import TraceRecord


def build_test_case(record: QuestionRecord, trace: TraceRecord) -> dict[str, Any]:
    """Plain inputs for ``LLMTestCase`` (kept as a dict so it is testable without DeepEval)."""
    expected = record.reference_answer or "\n".join(c.text for c in record.expected_claims)
    return {
        "input": record.question,
        "actual_output": trace.answer or "",
        "expected_output": expected,
        "retrieval_context": [
            trace.evidence[i].get("text", "") for i in trace.final_context_ids
            if i in trace.evidence
        ],
    }


def run_diagnostics(record: QuestionRecord, trace: TraceRecord, model: str) -> dict[str, Any]:
    if not trace.answer or not trace.final_context_ids:
        return {"contextual_recall": None, "faithfulness": None, "skipped": "no answer/context"}
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    from deepeval.metrics import ContextualRecallMetric, FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    case = LLMTestCase(**build_test_case(record, trace))
    out: dict[str, Any] = {}
    for name, metric in (("contextual_recall", ContextualRecallMetric(model=model)),
                         ("faithfulness", FaithfulnessMetric(model=model))):
        try:
            metric.measure(case)
            out[name] = metric.score
        except Exception as e:  # diagnostics never block the report
            out[name] = None
            out[f"{name}_error"] = f"{type(e).__name__}: {e}"
    return out
