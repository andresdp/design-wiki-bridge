"""Per-question metrics, A/B pairing, and report tables (R14)."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dwb.benchmark.schemas import QuestionRecord
from dwb.eval.claims_judge import ClaimsJudge
from dwb.eval.matching import MatchRules
from dwb.eval.recall import recall
from dwb.trace import TraceRecord

METRICS = ("recall_candidates", "recall_final", "recall_candidates_jaccard",
           "recall_final_jaccard", "correctness", "citation_support", "coverage",
           "abstained", "gap_claims", "failed", "degraded", "context_items", "context_tokens",
           "total_tokens", "latency_ms")
# abstaining / gap claims are not errors, but fewer of them means more of the question answered
LOWER_IS_BETTER = {"abstained", "gap_claims", "failed", "degraded", "context_tokens",
                   "total_tokens", "latency_ms"}
COST_METRICS = ("context_tokens", "total_tokens", "latency_ms")
TIE_EPS = 1e-9
# LLM stages whose tokens make up a question's cost; check_local is the web gate's judge
LLM_STAGES = ("check_local", "rerank", "generate")


def _counted(stage) -> bool:
    """A capture-only web call (record runs store results the gate did not use) is not part
    of answering the question, so it is left out of latency and cost."""
    return stage.extra.get("used") is not False


def web_check_label(trace: TraceRecord) -> str:
    """``sufficient`` / ``insufficient`` / ``judge_failed``, or empty when no gate ran."""
    wc = trace.web_check
    if wc is None:
        return ""
    return {True: "sufficient", False: "insufficient"}.get(wc.get("sufficient"), "judge_failed")


def question_row(record: QuestionRecord, trace: TraceRecord, judge: ClaimsJudge | None,
                 rules: MatchRules) -> dict[str, Any]:
    r = recall(record, trace, rules)
    row: dict[str, Any] = {
        "question_id": record.id, "kind": record.kind, "condition": trace.condition,
        "run_id": trace.run_id,
        "recall_candidates": r.candidates, "recall_final": r.final,
        "recall_candidates_jaccard": r.candidates_jaccard,
        "recall_final_jaccard": r.final_jaccard,
        "empty_context": trace.empty_context, "web_triggered": trace.web_triggered,
        "web_check": web_check_label(trace),
        # the reference evidence includes a web source (the gate should not call it sufficient)
        "web_reference": any(a.source_type == "web" for e in record.evidence
                             for a in e.alternatives),
        # a failed question has no answer: it scores correctness 0 rather than dropping out of
        # the paired means (survivorship), and has no cost (its stages never ran)
        "failed": float(bool(trace.errors)),
        "degraded": float(trace.degraded),
        "warnings": "; ".join(trace.warnings),
        "unresolved_citation_ids": trace.validation.get("n_unresolved_ids", 0),
        # whole-answer abstention (every claim is a gap) and partial gaps, separate from errors
        "abstained": float(bool(trace.claims) and all(c.kind == "gap" for c in trace.claims)),
        "gap_claims": float(sum(1 for c in trace.claims if c.kind == "gap")),
        "context_items": float(len(trace.final_context_ids)),
        "context_tokens": next((s.input_tokens for s in trace.stages
                                if s.name == "assemble_context"), 0),
        "total_tokens": sum(s.input_tokens + s.output_tokens for s in trace.stages
                            if s.name in LLM_STAGES),
        "latency_ms": sum(s.latency_ms for s in trace.stages if _counted(s)),
        "correctness": None, "citation_support": None, "coverage": None,
    }
    if trace.errors:
        for m in COST_METRICS:
            row[m] = None
        if judge is not None:  # no claims: every required claim is "missing" (score 0, no calls)
            row["correctness"] = judge.correctness(record, trace)["score"]
        return row
    if judge is not None:
        row["correctness"] = judge.correctness(record, trace)["score"]
        sup = judge.support(trace)
        row["citation_support"], row["coverage"] = sup["citation_support"], sup["coverage"]
    return row


def cost_rows(traces: list[TraceRecord]) -> list[dict[str, Any]]:
    by_stage: dict[str, list] = defaultdict(list)
    for t in traces:
        for s in t.stages:
            if _counted(s):
                by_stage[s.name].append(s)
    rows = []
    for name, stages in by_stage.items():
        rows.append({
            "condition": traces[0].condition if traces else "",
            "stage": name, "n": len(stages),
            "mean_latency_ms": round(mean(s.latency_ms for s in stages), 3),
            "sum_input_tokens": sum(s.input_tokens for s in stages),
            "sum_output_tokens": sum(s.output_tokens for s in stages),
            "web_live_calls": sum(1 for s in stages if s.extra.get("source") == "live"),
        })
    return rows


def pair(rows_a: list[dict], rows_b: list[dict]) -> tuple[list[dict], list[str]]:
    a = {r["question_id"]: r for r in rows_a}
    b = {r["question_id"]: r for r in rows_b}
    missing = sorted(set(a) ^ set(b))
    paired = []
    for qid in sorted(set(a) & set(b)):
        row = {"question_id": qid, "kind": a[qid]["kind"]}
        for m in METRICS:
            va, vb = a[qid].get(m), b[qid].get(m)
            row[f"{m}_A"], row[f"{m}_B"] = va, vb
            row[f"{m}_diff"] = (vb - va) if va is not None and vb is not None else None
        paired.append(row)
    return paired, missing


def paired_summary(paired: list[dict]) -> list[dict]:
    out = []
    for kind in ("public", "custom", "all"):
        rows = [p for p in paired if kind == "all" or p["kind"] == kind]
        for m in METRICS:
            both = [p for p in rows if p[f"{m}_diff"] is not None]  # same questions on both sides
            diffs = [p[f"{m}_diff"] for p in both]
            sign = -1 if m in LOWER_IS_BETTER else 1
            out.append({
                "subset": kind, "metric": m, "n_paired": len(diffs),
                "n_excluded": len(rows) - len(both),
                "mean_A": _mean([p[f"{m}_A"] for p in both]),
                "mean_B": _mean([p[f"{m}_B"] for p in both]),
                "mean_diff_B_minus_A": _mean(diffs),
                "improved": sum(1 for d in diffs if sign * d > TIE_EPS),
                "tied": sum(1 for d in diffs if abs(d) <= TIE_EPS),
                "regressed": sum(1 for d in diffs if sign * d < -TIE_EPS),
            })
    return out


def web_gate_summary(rows_a: list[dict], rows_b: list[dict]) -> dict[str, Any]:
    """The web gate over questions it ran on in both conditions. A and B judge their own
    local evidence, so their verdicts can differ; on discordant questions one condition
    answered with web and the other without, and score differences there mix retrieval with
    web access. Escalation = the gate sent the question to the web (insufficient or
    judge_failed). ``web_reference_judged_sufficient``: questions whose reference evidence
    includes web but the judge kept local (the success-criterion check)."""
    a = {r["question_id"]: r for r in rows_a if r.get("web_check")}
    b = {r["question_id"]: r for r in rows_b if r.get("web_check")}
    both = sorted(set(a) & set(b))
    up_a = {q: a[q]["web_check"] != "sufficient" for q in both}
    up_b = {q: b[q]["web_check"] != "sufficient" for q in both}
    n = len(both)

    def rate(up):
        return round(sum(up.values()) / n, 6) if n else None

    def kept_local(rows):
        return sum(1 for q in both if rows[q]["web_reference"]
                   and rows[q]["web_check"] == "sufficient")

    return {
        "n_gated": n,
        "both_sufficient": sum(1 for q in both if not up_a[q] and not up_b[q]),
        "both_insufficient": sum(1 for q in both if up_a[q] and up_b[q]),
        "discordant": sum(1 for q in both if up_a[q] != up_b[q]),
        "escalation_rate_A": rate(up_a), "escalation_rate_B": rate(up_b),
        "web_reference_judged_sufficient_A": kept_local(a),
        "web_reference_judged_sufficient_B": kept_local(b),
    }


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(mean(xs), 6) if xs else None


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def write_report(out: Path, rows_a: list[dict], rows_b: list[dict], traces_a: list[TraceRecord],
                 traces_b: list[TraceRecord], config: dict, judge: ClaimsJudge | None,
                 diagnostics: list[dict] | None = None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    paired, missing = pair(rows_a, rows_b)
    summary = paired_summary(paired)
    write_csv(out / "per_question.csv", rows_a + rows_b)
    write_csv(out / "paired.csv", paired)
    write_csv(out / "paired_summary.csv", summary)
    write_csv(out / "cost.csv", cost_rows(traces_a) + cost_rows(traces_b))
    if diagnostics is not None:
        write_csv(out / "deepeval_diagnostics.csv", diagnostics)
    (out / "evaluator_config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
    if judge is not None:
        with (out / "judge_log.jsonl").open("w") as fh:
            for entry in judge.stats.log:
                fh.write(json.dumps(entry) + "\n")
    gate = web_gate_summary(rows_a, rows_b)
    write_csv(out / "web_gate.csv", [gate])
    result = {"paired": len(paired), "missing_from_one_side": missing,
              "judge_calls": judge.stats.calls if judge else 0,
              "judge_cache_hits": judge.stats.cache_hits if judge else 0,
              "web_gate": gate}
    (out / "report_summary.json").write_text(json.dumps(result, indent=2))
    return result
