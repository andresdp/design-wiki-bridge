import subprocess
import sys
from pathlib import Path

import yaml
from conftest import make_trace
from test_claims_judge import make_judge

from dwb.benchmark.schemas import save_records
from dwb.eval.command import evaluate_runs
from dwb.eval.matching import MatchRules
from dwb.eval.report import pair, paired_summary, question_row
from dwb.manifest import load_manifest
from dwb.trace import RunDir

ROOT = Path(__file__).resolve().parents[2]


def test_pairing_reports_and_excludes_missing(record):
    j = make_judge()
    a = [question_row(record, make_trace("A", final=("i1",)), j, MatchRules())]
    b = [question_row(record, make_trace("B", final=("i1", "i2")), j, MatchRules()),
         question_row(record.model_copy(update={"id": "q2"}), make_trace("B", qid="q2"), j,
                      MatchRules())]
    paired, missing = pair(a, b)
    assert missing == ["q2"] and len(paired) == 1
    assert paired[0]["recall_final_diff"] == 0.5
    s = {(r["subset"], r["metric"]): r for r in paired_summary(paired)}
    assert s[("custom", "recall_final")]["improved"] == 1
    assert s[("public", "recall_final")]["n_paired"] == 0
    assert s[("custom", "correctness")]["tied"] == 1


def test_evaluate_command_writes_all_tables(tmp_path, record):
    save_records([record], tmp_path / "data" / "dev.jsonl")
    base = yaml.safe_load((ROOT / "configs" / "manifests" / "flask-A.yaml").read_text())
    for cond, final in (("A", ("i1",)), ("B", ("i1", "i2"))):
        m = load_manifest(ROOT / "configs" / "manifests" / f"flask-{cond}.yaml")
        m = m.model_copy(update={"question_set": m.question_set.model_copy(
            update={"path": "data/dev.jsonl"})})
        rd = RunDir.start(m, tmp_path / "runs")
        with rd.writer() as w:
            w.append(make_trace(cond, final=final).model_copy(update={"run_id": m.run_id}))
    assert base  # example manifest still parses
    (tmp_path / "prompts").mkdir()
    for p in ("judge_claim_v1.md", "judge_support_v1.md"):
        (tmp_path / "prompts" / p).write_text((ROOT / "prompts" / p).read_text())
    cfg = tmp_path / "eval.yaml"
    cfg.write_text(yaml.safe_dump({"judge": {"model": "fake", "prompts_dir": "prompts",
                                             "cache": "cache.jsonl"},
                                   "matching": {"min_overlap_lines": 1}}))
    from test_claims_judge import judge_fn

    from fakes import FakeStructuredChat

    out = tmp_path / "report"
    res = evaluate_runs(tmp_path / "runs" / "flask-A-dev-001",
                        tmp_path / "runs" / "flask-B-dev-001",
                        out, config_path=cfg, base_dir=tmp_path,
                        judge_llm=FakeStructuredChat(judge_fn))
    for f in ("per_question.csv", "paired.csv", "paired_summary.csv", "cost.csv",
              "evaluator_config.json", "judge_log.jsonl", "report_summary.json"):
        assert (out / f).exists() and (out / f).stat().st_size > 0, f
    assert res["paired"] == 1 and res["judge_calls"] > 0
    again = evaluate_runs(tmp_path / "runs" / "flask-A-dev-001",
                          tmp_path / "runs" / "flask-B-dev-001", tmp_path / "report2",
                          config_path=cfg, base_dir=tmp_path,
                          judge_llm=FakeStructuredChat(judge_fn))
    assert again["judge_calls"] == 0 and again["judge_cache_hits"] > 0


def test_evaluator_never_imports_graph_or_generator():
    code = ("import sys, dwb.eval.command, dwb.eval.deepeval_diag, dwb.eval.report;"
            "bad=[m for m in sys.modules if m in ('dwb.graph','dwb.generate','dwb.runner',"
            "'dwb.rerank') or m.startswith('dwb.retrieval')];"
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""})
    assert r.returncode == 0, r.stdout + r.stderr
