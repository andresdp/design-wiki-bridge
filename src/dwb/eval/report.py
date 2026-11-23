"""Per-question metrics, pairing of two conditions (A/B, A/C, B/C), and report tables (R14).

Paired columns carry the conditions' letters (``mean_A``, ``mean_C``, ``mean_diff_C_minus_A``),
so an A/B report keeps its original layout."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dwb.benchmark.schemas import QuestionRecord
from dwb.eval.claims_judge import ClaimsJudge
from dwb.eval.matching import DEFAULT_RULES, MatchRules, matches
from dwb.eval.recall import recall
from dwb.manifest import USES_WIKI
from dwb.trace import TraceRecord

METRICS = ("recall_candidates", "recall_final", "recall_candidates_jaccard",
           "recall_final_jaccard", "correctness", "citation_support", "coverage",
           "control_correct", "abstained", "gap_claims", "failed", "degraded", "context_items",
           "context_tokens", "total_tokens", "latency_ms")
# abstaining / gap claims are not errors, but fewer of them means more of the question answered
LOWER_IS_BETTER = {"abstained", "gap_claims", "failed", "degraded", "context_tokens",
                   "total_tokens", "latency_ms"}
COST_METRICS = ("context_tokens", "total_tokens", "latency_ms")
TIE_EPS = 1e-9
# LLM stages whose tokens make up a question's cost; check_local is the web gate's judge
LLM_STAGES = ("check_local", "rerank", "generate")
# report subsets (KTD-B10): "primary" is the condition-sensitive A/B comparison, public
# questions plus authored answerable ones that need code; the rest are per-type breakdowns
PRIMARY = "primary"
SUBSET_ORDER = ("public", "code", "code+pdf", "code+web", "code+pdf+web", "pdf", "web",
                "pdf+web", "control")


def subset_of(record: QuestionRecord) -> str:
    """``public`` or ``control`` by kind; an authored answerable question by its grounding."""
    return record.kind if record.kind in ("public", "control") else record.grounding


def in_primary(subset: str) -> bool:
    return subset == "public" or subset.startswith("code")


def declined(trace: TraceRecord) -> bool:
    """The answer declines: it has claims and every one is a "not established" gap claim."""
    return bool(trace.claims) and all(c.kind == "gap" for c in trace.claims)


# a repository path with at least one directory, as SWE-QA reference answers name them
REF_PATH = re.compile(r"(?<![\w/.-])(?:\./)?((?:[\w.-]+/)+[\w-]+\.(?:py|pyi|rst|md|toml|cfg|txt|"
                      r"ya?ml))\b")


def gold_files(record: QuestionRecord) -> list[set[str]]:
    """For a public question (no annotated spans), the repository paths its reference
    answer names, one needed file each."""
    if record.kind != "public" or not record.reference_answer:
        return []
    return [{p} for p in sorted(set(REF_PATH.findall(record.reference_answer)))]


def _wiki_items(trace: TraceRecord) -> list[dict]:
    """Code items the wiki route resolved (before reranking), including spans merged into a
    vector-search survivor (condition C)."""
    return [e for e in trace.evidence.values() if e.get("source_type") == "code"
            and (e.get("provenance", {}).get("branch") == "wiki"
                 or "wiki" in e.get("provenance", {}).get("also_from", []))]


def wiki_files(trace: TraceRecord) -> set[str]:
    return {e["locator"]["path"] for e in _wiki_items(trace)}


def wiki_gold_coverage(record: QuestionRecord, trace: TraceRecord,
                       rules: MatchRules | None = None) -> tuple[float | None, str | None]:
    """Share of the needed code the wiki route reached, before ranking. 0 means a wiki
    coverage gap: ranking and generation could not recover it. ``None`` for condition A or
    when nothing names the needed code.

    - ``evidence_spans``: code requirements met by a wiki-resolved span (the evaluator's span
      matching; any alternative counts);
    - ``reference_answer_files`` (public questions): repository paths named in the reference
      answer that the wiki route reached at file level. Coarse: reaching a large file is not
      reaching the function, so only a 0 is conclusive."""
    if trace.condition not in USES_WIKI:
        return None, None
    items = _wiki_items(trace)
    code_reqs = [[a for a in e.alternatives if a.source_type == "code"] for e in record.evidence]
    code_reqs = [r for r in code_reqs if r]
    if code_reqs:
        hit = sum(1 for alts in code_reqs
                  if any(matches(e, a, rules or DEFAULT_RULES) for a in alts for e in items))
        return round(hit / len(code_reqs), 6), "evidence_spans"
    files = gold_files(record)
    if files:
        reached = {e["locator"]["path"] for e in items}
        return round(sum(1 for f in files if f & reached) / len(files), 6), \
            "reference_answer_files"
    return None, None


def wiki_coverage_summary(rows: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in ("evidence_spans", "reference_answer_files"):
        scored = [r["wiki_gold_coverage"] for r in rows if r.get("wiki_gold_source") == source]
        out[source] = {"n": len(scored), "mean": _mean(scored),
                       "n_none_reached": sum(1 for v in scored if v == 0)}
    return out


def _counted(stage) -> bool:
    """A capture-only web call (record runs store results the gate did not use) is not part
    of answering the question, so it is left out of latency and cost."""
    return stage.extra.get("used") is not False


def question_row(record: QuestionRecord, trace: TraceRecord, judge: ClaimsJudge | None,
                 rules: MatchRules) -> dict[str, Any]:
    r = recall(record, trace, rules)
    row: dict[str, Any] = {
        "question_id": record.id, "kind": record.kind, "subset": subset_of(record),
        "condition": trace.condition,
        "run_id": trace.run_id,
        "recall_candidates": r.candidates, "recall_final": r.final,
        "recall_candidates_jaccard": r.candidates_jaccard,
        "recall_final_jaccard": r.final_jaccard,
        "empty_context": trace.empty_context, "web_triggered": trace.web_triggered,
        "web_check": trace.web_verdict,
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
        "abstained": float(declined(trace)),
        "gap_claims": float(sum(1 for c in trace.claims if c.kind == "gap")),
        "context_items": float(len(trace.final_context_ids)),
        "context_tokens": next((s.input_tokens for s in trace.stages
                                if s.name == "assemble_context"), 0),
        "total_tokens": sum(s.input_tokens + s.output_tokens for s in trace.stages
                            if s.name in LLM_STAGES),
        "latency_ms": sum(s.latency_ms for s in trace.stages if _counted(s)),
        "correctness": None, "citation_support": None, "coverage": None,
        "control_correct": None,
    }
    row["wiki_gold_coverage"], row["wiki_gold_source"] = wiki_gold_coverage(record, trace,
                                                                         rules)
    if record.kind == "control":
        # unanswerable (R6): correct only when the answer declines; a failed control has no
        # answer and scores 0. The claim judge is never called, recall does not apply.
        row["control_correct"] = float(not trace.errors and declined(trace))
        if trace.errors:
            for m in COST_METRICS:
                row[m] = None
        return row
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


def pair(rows_a: list[dict], rows_b: list[dict], x: str = "A",
         y: str = "B") -> tuple[list[dict], list[str]]:
    a = {r["question_id"]: r for r in rows_a}
    b = {r["question_id"]: r for r in rows_b}
    missing = sorted(set(a) ^ set(b))
    paired = []
    for qid in sorted(set(a) & set(b)):
        row = {"question_id": qid, "kind": a[qid]["kind"], "subset": a[qid]["subset"]}
        for m in METRICS:
            va, vb = a[qid].get(m), b[qid].get(m)
            row[f"{m}_{x}"], row[f"{m}_{y}"] = va, vb
            row[f"{m}_diff"] = (vb - va) if va is not None and vb is not None else None
        paired.append(row)
    return paired, missing


def summary_subsets(paired: list[dict]) -> list[str]:
    """``primary``, then each subset present in a fixed order (unknown ones last), then
    ``all``."""
    present = {p["subset"] for p in paired}
    ordered = [s for s in SUBSET_ORDER if s in present] + sorted(present - set(SUBSET_ORDER))
    return [PRIMARY, *ordered, "all"]


def paired_summary(paired: list[dict], x: str = "A", y: str = "B") -> list[dict]:
    out = []
    for subset in summary_subsets(paired):
        rows = [p for p in paired if subset == "all" or p["subset"] == subset
                or (subset == PRIMARY and in_primary(p["subset"]))]
        for m in METRICS:
            both = [p for p in rows if p[f"{m}_diff"] is not None]  # same questions on both sides
            diffs = [p[f"{m}_diff"] for p in both]
            sign = -1 if m in LOWER_IS_BETTER else 1
            out.append({
                "subset": subset, "metric": m, "n_paired": len(diffs),
                "n_excluded": len(rows) - len(both),
                f"mean_{x}": _mean([p[f"{m}_{x}"] for p in both]),
                f"mean_{y}": _mean([p[f"{m}_{y}"] for p in both]),
                f"mean_diff_{y}_minus_{x}": _mean(diffs),
                "improved": sum(1 for d in diffs if sign * d > TIE_EPS),
                "tied": sum(1 for d in diffs if abs(d) <= TIE_EPS),
                "regressed": sum(1 for d in diffs if sign * d < -TIE_EPS),
            })
    return out


def web_gate_summary(rows_a: list[dict], rows_b: list[dict], x: str = "A",
                     y: str = "B") -> dict[str, Any]:
    """The web gate over questions it ran on in both conditions. The two judge their own
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
        f"escalation_rate_{x}": rate(up_a), f"escalation_rate_{y}": rate(up_b),
        f"web_reference_judged_sufficient_{x}": kept_local(a),
        f"web_reference_judged_sufficient_{y}": kept_local(b),
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
                 diagnostics: list[dict] | None = None,
                 labels: tuple[str, str] = ("A", "B")) -> dict[str, Any]:
    x, y = labels
    out.mkdir(parents=True, exist_ok=True)
    paired, missing = pair(rows_a, rows_b, x, y)
    summary = paired_summary(paired, x, y)
    write_csv(out / "per_question.csv", rows_a + rows_b)
    write_csv(out / "paired.csv", paired)
    write_csv(out / "paired_summary.csv", summary)
    write_csv(out / "cost.csv", cost_rows(traces_a) + cost_rows(traces_b))
    if diagnostics is not None:
        from dwb.eval.deepeval_diag import deepeval_summary

        write_csv(out / "deepeval_diagnostics.csv", diagnostics)
        write_csv(out / "deepeval_summary.csv", deepeval_summary(diagnostics))
    (out / "evaluator_config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
    if judge is not None:
        with (out / "judge_log.jsonl").open("w") as fh:
            for entry in judge.stats.log:
                fh.write(json.dumps(entry) + "\n")
    gate = web_gate_summary(rows_a, rows_b, x, y)
    write_csv(out / "web_gate.csv", [gate])
    result = {"paired": len(paired), "missing_from_one_side": missing,
              "judge_calls": judge.stats.calls if judge else 0,
              "judge_cache_hits": judge.stats.cache_hits if judge else 0,
              "web_gate": gate,
              "wiki_coverage": {c: wiki_coverage_summary(r)
                                for c, r in ((x, rows_a), (y, rows_b)) if c in USES_WIKI}}
    (out / "report_summary.json").write_text(json.dumps(result, indent=2))
    return result
