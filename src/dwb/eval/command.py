"""``dwb experiment evaluate``: score two saved runs (A and B) and write the paired report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dwb.benchmark.schemas import QuestionRecord, load_records
from dwb.eval.claims_judge import ClaimsJudge
from dwb.eval.matching import MatchRules
from dwb.eval.report import question_row, write_report
from dwb.evidence_text import hydrate
from dwb.log import get_logger
from dwb.manifest import MODEL_NET, check_paired, load_manifest
from dwb.progress import OnProgress, report
from dwb.prompts import PROMPT_DIR
from dwb.trace import TraceRecord, read_traces

LOG = get_logger("eval")


class UnpairedRunsError(ValueError):
    pass


def check_runs(run_a: Path, run_b: Path, *, allow_unpaired: bool = False) -> dict[str, Any]:
    """The two saved runs must be condition A then B under paired manifests (R6). The saved
    manifests are the resolved ones the runs used, so this rechecks what actually ran."""
    ma, mb = load_manifest(run_a / "manifest.yaml"), load_manifest(run_b / "manifest.yaml")
    problems = []
    if (ma.condition, mb.condition) != ("A", "B"):
        problems.append(f"expected runs of condition A then B, got {ma.condition} then "
                        f"{mb.condition} (pass the A run first)")
    pc = check_paired(ma, mb)
    problems += pc.failures
    if problems and not allow_unpaired:
        raise UnpairedRunsError("runs are not a valid A/B pair:\n  " + "\n  ".join(problems))
    for p in problems:
        LOG.warning("Unpaired (allowed): %s", p)
    return {"ok": not problems, "failures": problems, "notes": pc.notes,
            "allowed_unpaired": bool(problems and allow_unpaired)}


def _records_for(run_dir: Path, base_dir: Path) -> dict[str, QuestionRecord]:
    m = load_manifest(run_dir / "manifest.yaml")
    return {r.id: r for r in load_records(base_dir / m.question_set.path)}


def evaluate_runs(
    run_a: Path,
    run_b: Path,
    out: Path,
    *,
    config_path: Path,
    base_dir: Path = Path("."),
    judge_llm=None,
    use_judge: bool = True,
    deepeval: bool | None = None,
    allow_unpaired: bool = False,
    on_progress: OnProgress | None = None,
) -> dict[str, Any]:
    """Score both runs and write the report. ``on_progress`` gets, per condition, an
    ``evaluate`` start (``label`` A or B, ``total`` traces), one step per trace scored or
    skipped, and a done event."""
    paired_check = check_runs(run_a, run_b, allow_unpaired=allow_unpaired)
    cfg = yaml.safe_load(config_path.read_text())
    rules = MatchRules(**cfg.get("matching", {}))
    judge = None
    if use_judge:
        if judge_llm is None:
            from langchain_openai import ChatOpenAI

            judge_llm = ChatOpenAI(model=cfg["judge"]["model"], **MODEL_NET,
                                   temperature=cfg["judge"].get("temperature", 0.0))
        cache = cfg["judge"].get("cache")
        pdir = cfg["judge"].get("prompts_dir")
        judge = ClaimsJudge(judge_llm, (base_dir / pdir) if pdir else PROMPT_DIR,
                            model_id=cfg["judge"]["model"],
                            temperature=cfg["judge"].get("temperature", 0.0),
                            cache_path=(base_dir / cache) if cache else None)
    run_deepeval = cfg.get("deepeval", {}).get("enabled", False) if deepeval is None else deepeval

    rows: dict[str, list[dict]] = {}
    traces: dict[str, list[TraceRecord]] = {}
    diagnostics: list[dict] = []
    LOG.info("Evaluating A=%s vs B=%s (judge: %s)", run_a, run_b,
             cfg["judge"]["model"] if judge else "off, recall and cost only")
    for label, rd in (("A", run_a), ("B", run_b)):
        records = _records_for(rd, base_dir)
        traces[label] = read_traces(rd / "traces.jsonl")
        run_m = load_manifest(rd / "manifest.yaml")
        checkout = (base_dir / run_m.checkout) if run_m.checkout else None
        filled = hydrate(traces[label], checkout)
        rows[label] = []
        LOG.info("Condition %s: %d traces (%d code passages read from %s)", label,
                 len(traces[label]), filled, checkout or "-")
        total = len(traces[label])
        report(on_progress, "evaluate", "start", total=total, label=label)
        for i, t in enumerate(traces[label], start=1):
            rec = records.get(t.question_id)
            if rec is None:
                LOG.warning("  %s not in the question set; skipped", t.question_id)
                report(on_progress, "evaluate", "step", done=i, total=total,
                       label=t.question_id, outcome="skipped")
                continue
            calls0 = judge.stats.calls if judge else 0
            row = question_row(rec, t, judge, rules)
            rows[label].append(row)
            LOG.debug("  [%d/%d] %s: recall %s/%s, correctness %s, support %s, coverage %s%s",
                      i, len(traces[label]), t.question_id, _fmt(row["recall_candidates"]),
                      _fmt(row["recall_final"]), _fmt(row["correctness"]),
                      _fmt(row["citation_support"]), _fmt(row["coverage"]),
                      f" ({judge.stats.calls - calls0} judge calls)" if judge else "")
            if run_deepeval:
                from dwb.eval.deepeval_diag import run_diagnostics

                diagnostics.append({"question_id": t.question_id, "condition": t.condition,
                                    **run_diagnostics(rec, t, cfg["deepeval"]["model"])})
            report(on_progress, "evaluate", "step", done=i, total=total, label=t.question_id,
                   outcome="ok")
        report(on_progress, "evaluate", "done", done=total, total=total, label=label)
    config = {"eval_config": cfg, "match_rules": rules.__dict__,
              "judge_prompt_hashes": ({"claim": judge.claim_hash, "support": judge.support_hash}
                                      if judge else None),
              "runs": {"A": str(run_a), "B": str(run_b)}, "paired_check": paired_check}
    result = write_report(out, rows["A"], rows["B"], traces["A"], traces["B"], config, judge,
                          diagnostics if run_deepeval else None)
    LOG.info("Report written to [bold]%s[/]: %d paired questions, %d judge calls, %d cache hits",
             out, result["paired"], result["judge_calls"], result["judge_cache_hits"])
    if result["missing_from_one_side"]:
        LOG.warning("Not paired (missing from one run): %s", result["missing_from_one_side"])
    return result


def _fmt(x) -> str:
    return "–" if x is None else f"{x:.2f}"
