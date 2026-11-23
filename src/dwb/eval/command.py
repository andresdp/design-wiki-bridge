"""``dwb experiment evaluate``: score two saved runs of different conditions (A/B, A/C or B/C,
in that order) and write the paired report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dwb.benchmark.schemas import QuestionRecord, load_records
from dwb.eval.claims_judge import ClaimsJudge, JudgeCache
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


def run_conditions(run_a: Path, run_b: Path) -> tuple[str, str]:
    return (load_manifest(run_a / "manifest.yaml").condition,
            load_manifest(run_b / "manifest.yaml").condition)


def check_runs(run_a: Path, run_b: Path, *, allow_unpaired: bool = False) -> dict[str, Any]:
    """The two saved runs must be two different conditions in letter order (A then B, A then
    C, B then C) under paired manifests (R6). The saved manifests are the resolved ones the
    runs used, so this rechecks what actually ran."""
    ma, mb = load_manifest(run_a / "manifest.yaml"), load_manifest(run_b / "manifest.yaml")
    problems = []
    if not ma.condition < mb.condition:
        problems.append(f"expected two conditions in letter order (A B, A C or B C), got "
                        f"{ma.condition} then {mb.condition}")
    pc = check_paired(ma, mb)
    problems += pc.failures
    if problems and not allow_unpaired:
        raise UnpairedRunsError(f"runs are not a valid {ma.condition}/{mb.condition} pair:\n  "
                                + "\n  ".join(problems))
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
    x, y = run_conditions(run_a, run_b)
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
    de_cache_path = cfg.get("deepeval", {}).get("cache")
    de_cache = JudgeCache(base_dir / de_cache_path) if run_deepeval and de_cache_path else None

    rows: dict[str, list[dict]] = {}
    traces: dict[str, list[TraceRecord]] = {}
    diagnostics: list[dict] = []
    LOG.info("Evaluating %s=%s vs %s=%s (judge: %s)", x, run_a, y, run_b,
             cfg["judge"]["model"] if judge else "off, recall and cost only")
    for label, rd in ((x, run_a), (y, run_b)):
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
                from dwb.eval.deepeval_diag import default_metric_factory, run_diagnostics

                diag = run_diagnostics(rec, t, cfg["deepeval"]["model"], cache=de_cache,
                                       metric_factory=default_metric_factory(
                                           cfg["deepeval"].get("price_per_million")))
                if diag is not None:  # a failed trace gets no diagnostics (KTD-B7)
                    diagnostics.append({"question_id": t.question_id, "condition": t.condition,
                                        "subset": row["subset"], **diag})
            report(on_progress, "evaluate", "step", done=i, total=total, label=t.question_id,
                   outcome="ok")
        report(on_progress, "evaluate", "done", done=total, total=total, label=label)
    config = {"eval_config": cfg, "match_rules": rules.__dict__,
              "judge_prompt_hashes": ({"claim": judge.claim_hash, "support": judge.support_hash}
                                      if judge else None),
              "runs": {x: str(run_a), y: str(run_b)}, "paired_check": paired_check}
    if run_deepeval:
        from dwb.eval.deepeval_diag import deepeval_version

        config["deepeval_version"] = deepeval_version()
    result = write_report(out, rows[x], rows[y], traces[x], traces[y], config, judge,
                          diagnostics if run_deepeval else None, labels=(x, y))
    LOG.info("Report written to [bold]%s[/]: %d paired questions, %d judge calls, %d cache hits",
             out, result["paired"], result["judge_calls"], result["judge_cache_hits"])
    if result["missing_from_one_side"]:
        LOG.warning("Not paired (missing from one run): %s", result["missing_from_one_side"])
    g = result["web_gate"]
    if g["n_gated"]:
        LOG.info("Web gate on %d questions: escalated %s %.0f%%, %s %.0f%%; %d discordant "
                 "(answered with web in one condition only)", g["n_gated"],
                 x, 100 * g[f"escalation_rate_{x}"], y, 100 * g[f"escalation_rate_{y}"],
                 g["discordant"])
        kx, ky = (g[f"web_reference_judged_sufficient_{c}"] for c in (x, y))
        if kx + ky:
            LOG.warning("The judge kept %d web-needing questions local (%s %d, %s %d); see "
                        "web_gate.csv", kx + ky, x, kx, y, ky)
    for c, w in result["wiki_coverage"].items():
        for source, label in (("evidence_spans", "annotated code"),
                              ("reference_answer_files", "files the reference answer names")):
            if w[source]["n"]:
                LOG.info("Wiki route (%s) reached none of the %s on %d of %d questions", c,
                         label, w[source]["n_none_reached"], w[source]["n"])
    return result


def _fmt(x) -> str:
    return "–" if x is None else f"{x:.2f}"
