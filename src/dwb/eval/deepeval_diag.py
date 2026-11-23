"""DeepEval diagnostics (contextual recall, faithfulness) over saved traces (KTD16, KTD-B7).

DeepEval runs as a library on the saved context and answer: no tracing, no Confident AI,
no dataset generation. Telemetry is disabled before import. The diagnostics are secondary:
DeepEval's metric prompts are not temperature-pinned, so they never replace the claim and
citation metrics.

Per question:
- answerable (public and custom): Contextual Recall and Faithfulness;
- control: Faithfulness only (the reference is a decline, so Contextual Recall does not apply;
  Faithfulness catches answers made up for an unanswerable question);
- failed trace: no diagnostics at all (correctness is already 0);
- no answer or no final context: skipped with a reason.

With a cache (``deepeval.cache`` in configs/eval.yaml), a row is reused when the DeepEval
version, model, question kind and test case are identical, so scoring the same run in a
second pairwise report (A/B, then A/C) costs nothing. Rows with an error are not cached.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from statistics import mean
from typing import Any

from dwb.benchmark.schemas import QuestionRecord
from dwb.eval.claims_judge import JudgeCache
from dwb.trace import TraceRecord

METRIC_NAMES = ("contextual_recall", "faithfulness")
# name -> metric instance; injectable so the contract is testable without DeepEval calls
MetricFactory = Callable[[str, str], Any]


def deepeval_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("deepeval")
    except Exception:  # pragma: no cover - not installed
        return None


def _default_metric(name: str, model: str) -> Any:
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    from deepeval.metrics import ContextualRecallMetric, FaithfulnessMetric

    cls = {"contextual_recall": ContextualRecallMetric, "faithfulness": FaithfulnessMetric}[name]
    return cls(model=model)


def metrics_for(record: QuestionRecord) -> tuple[str, ...]:
    return ("faithfulness",) if record.kind == "control" else METRIC_NAMES


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


def cache_key(record: QuestionRecord, trace: TraceRecord, model: str) -> str:
    return hashlib.sha256(json.dumps(
        [deepeval_version(), model, record.kind, build_test_case(record, trace)],
        sort_keys=True).encode()).hexdigest()


def run_diagnostics(record: QuestionRecord, trace: TraceRecord, model: str, *,
                    metric_factory: MetricFactory | None = None,
                    cache: JudgeCache | None = None) -> dict[str, Any] | None:
    """One diagnostics row, or ``None`` for a failed trace (it gets no diagnostics)."""
    if trace.errors:
        return None
    out: dict[str, Any] = {name: None for name in METRIC_NAMES}
    if not trace.answer or not trace.final_context_ids:
        out["skipped"] = "no answer" if not trace.answer else "empty final context"
        return out
    key = cache_key(record, trace, model) if cache is not None else None
    if key is not None and (hit := cache.get(key)) is not None:
        return {**hit, "cached": True}
    out["skipped"] = ""
    if record.kind == "control":
        out["contextual_recall_skipped"] = "control: reference is a decline"
    factory = metric_factory or _default_metric
    case = None
    for name in metrics_for(record):
        try:
            if case is None:
                case = _test_case(build_test_case(record, trace), metric_factory is None)
            metric = factory(name, model)
            metric.measure(case)
            out[name] = metric.score
        except Exception as e:  # diagnostics never block the report
            out[f"{name}_error"] = f"{type(e).__name__}: {e}"
            metric = None
        cost = getattr(metric, "evaluation_cost", None)
        out[f"{name}_cost"] = cost
    if key is not None and not any(k.endswith("_error") for k in out):
        cache.put(key, out)
    return {**out, "cached": False}


def _test_case(fields: dict[str, Any], real: bool) -> Any:
    if not real:
        return fields
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(**fields)


def deepeval_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per condition, subset (plus ``all``) and metric: scored count, mean, skips, errors,
    the summed evaluation cost and how many rows came from the cache. ``rows`` carry
    ``condition`` and ``subset``."""
    out = []
    for cond in sorted({r["condition"] for r in rows}):
        cond_rows = [r for r in rows if r["condition"] == cond]
        subsets = list(dict.fromkeys(r["subset"] for r in cond_rows)) + ["all"]
        for subset in subsets:
            sub = [r for r in cond_rows if subset == "all" or r["subset"] == subset]
            for m in METRIC_NAMES:
                scores = [r[m] for r in sub if r.get(m) is not None]
                costs = [r[f"{m}_cost"] for r in sub if r.get(f"{m}_cost") is not None]
                out.append({
                    "condition": cond, "subset": subset, "metric": m,
                    "n_questions": len(sub), "n_scored": len(scores),
                    "mean": round(mean(scores), 6) if scores else None,
                    "n_skipped": sum(1 for r in sub if r.get("skipped")
                                     or r.get(f"{m}_skipped")),
                    "n_errors": sum(1 for r in sub if r.get(f"{m}_error")),
                    # cost of computing these scores, also when an earlier report did (cached)
                    "evaluation_cost": round(sum(costs), 6) if costs else None,
                    "n_cached": sum(1 for r in sub if r.get("cached")),
                })
    return out
