"""``dwb evaluate``: score two saved runs (A and B) and write the paired report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dwb.benchmark.schemas import QuestionRecord, load_records
from dwb.eval.claims_judge import ClaimsJudge
from dwb.eval.matching import MatchRules
from dwb.eval.report import question_row, write_report
from dwb.manifest import load_manifest
from dwb.trace import TraceRecord, read_traces


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
) -> dict[str, Any]:
    cfg = yaml.safe_load(config_path.read_text())
    rules = MatchRules(**cfg.get("matching", {}))
    judge = None
    if use_judge:
        if judge_llm is None:
            from langchain_openai import ChatOpenAI

            judge_llm = ChatOpenAI(model=cfg["judge"]["model"],
                                   temperature=cfg["judge"].get("temperature", 0.0))
        cache = cfg["judge"].get("cache")
        judge = ClaimsJudge(judge_llm, base_dir / cfg["judge"].get("prompts_dir", "prompts"),
                            model_id=cfg["judge"]["model"],
                            cache_path=(base_dir / cache) if cache else None)
    run_deepeval = cfg.get("deepeval", {}).get("enabled", False) if deepeval is None else deepeval

    rows: dict[str, list[dict]] = {}
    traces: dict[str, list[TraceRecord]] = {}
    diagnostics: list[dict] = []
    for label, rd in (("A", run_a), ("B", run_b)):
        records = _records_for(rd, base_dir)
        traces[label] = read_traces(rd / "traces.jsonl")
        rows[label] = []
        for t in traces[label]:
            rec = records.get(t.question_id)
            if rec is None:
                continue
            rows[label].append(question_row(rec, t, judge, rules))
            if run_deepeval:
                from dwb.eval.deepeval_diag import run_diagnostics

                diagnostics.append({"question_id": t.question_id, "condition": t.condition,
                                    **run_diagnostics(rec, t, cfg["deepeval"]["model"])})
    config = {"eval_config": cfg, "match_rules": rules.__dict__,
              "judge_prompt_hashes": ({"claim": judge.claim_hash, "support": judge.support_hash}
                                      if judge else None),
              "runs": {"A": str(run_a), "B": str(run_b)}}
    return write_report(out, rows["A"], rows["B"], traces["A"], traces["B"], config, judge,
                        diagnostics if run_deepeval else None)
