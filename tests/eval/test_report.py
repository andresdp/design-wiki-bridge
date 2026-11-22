import subprocess
import sys
from pathlib import Path

import yaml
from test_claims_judge import make_judge

from conftest import make_trace
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
    assert s[("code+pdf", "recall_final")]["improved"] == 1
    assert s[("primary", "recall_final")]["improved"] == 1  # code+pdf needs code
    assert ("public", "recall_final") not in s  # only subsets present are reported
    assert s[("code+pdf", "correctness")]["tied"] == 1


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
        (tmp_path / "prompts" / p).write_text((ROOT / "src/dwb/prompt_files" / p).read_text())
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


def test_abstention_reported_separately(record):
    from dwb.trace import AnswerClaim

    gap = make_trace(claims=[AnswerClaim(text="Not in the evidence.", kind="gap")])
    row = question_row(record, gap, make_judge(), MatchRules())
    assert row["abstained"] == 1.0 and row["gap_claims"] == 1.0
    assert row["coverage"] is None and row["citation_support"] is None  # nothing to judge
    normal = question_row(record, make_trace(), make_judge(), MatchRules())
    assert normal["abstained"] == 0.0 and normal["context_items"] == 1.0


def test_failed_trace_scores_zero_correctness_and_no_cost(record):
    j = make_judge()
    failed = make_trace("B", claims=[], errors=["OpenWikiError: server died"])
    row = question_row(record, failed, j, MatchRules())
    assert row["failed"] == 1.0 and row["correctness"] == 0.0
    assert row["latency_ms"] is None and row["total_tokens"] is None
    assert j.stats.calls == 0  # no answer: every required claim is "missing" without a call


def test_failed_question_does_not_drop_out_of_the_paired_means(record):
    j = make_judge()
    q2 = record.model_copy(update={"id": "q2"})
    a = [question_row(record, make_trace("A"), j, MatchRules()),
         question_row(q2, make_trace("A", qid="q2"), j, MatchRules())]
    b = [question_row(record, make_trace("B"), j, MatchRules()),
         question_row(q2, make_trace("B", qid="q2", claims=[], errors=["boom"]), j,
                      MatchRules())]
    s = {(r["subset"], r["metric"]): r for r in paired_summary(pair(a, b)[0])}
    corr = s[("all", "correctness")]
    assert corr["n_paired"] == 2 and corr["regressed"] == 1  # the failure counts against B
    assert corr["mean_B"] < corr["mean_A"]
    fail = s[("all", "failed")]
    assert fail["mean_A"] == 0 and fail["mean_B"] == 0.5 and fail["regressed"] == 1
    # cost: the failed question is excluded from both sides, not counted as free
    lat = s[("all", "latency_ms")]
    assert lat["n_paired"] == 1 and lat["n_excluded"] == 1
    assert lat["mean_A"] == lat["mean_B"]


def test_degraded_metric(record):
    t = make_trace("B").model_copy(update={"warnings": ["rerank: ValueError: x"]})
    row = question_row(record, t, make_judge(), MatchRules())
    assert row["degraded"] == 1.0 and "rerank" in row["warnings"] and row["failed"] == 0.0


def _two_runs(tmp_path, record, conds=("A", "B"), mutate=None):
    save_records([record], tmp_path / "data" / "dev.jsonl")
    dirs = []
    for i, cond in enumerate(conds):
        m = load_manifest(ROOT / "configs" / "manifests" / f"flask-{cond}.yaml")
        m = m.model_copy(update={"question_set": m.question_set.model_copy(
            update={"path": "data/dev.jsonl"}), "run_id": f"r{i}-{cond}"})
        if mutate and i == 1:
            m = mutate(m)
        rd = RunDir.start(m, tmp_path / "runs")
        with rd.writer() as w:
            w.append(make_trace(cond).model_copy(update={"run_id": m.run_id}))
        dirs.append(rd.root)
    cfg = tmp_path / "eval.yaml"
    cfg.write_text(yaml.safe_dump({"judge": {"model": "fake"}}))
    return dirs, cfg


def test_evaluate_rejects_swapped_runs(tmp_path, record):
    import pytest

    from dwb.eval.command import UnpairedRunsError

    (a, b), cfg = _two_runs(tmp_path, record)
    with pytest.raises(UnpairedRunsError, match="condition A then B"):
        evaluate_runs(b, a, tmp_path / "out", config_path=cfg, base_dir=tmp_path,
                      use_judge=False)


def test_evaluate_rejects_unpaired_settings_unless_allowed(tmp_path, record):
    import json

    import pytest

    from dwb.eval.command import UnpairedRunsError

    def other_generator(m):
        return m.model_copy(update={"models": m.models.model_copy(update={"generator": "x"})})

    (a, b), cfg = _two_runs(tmp_path, record, mutate=other_generator)
    with pytest.raises(UnpairedRunsError, match="models"):
        evaluate_runs(a, b, tmp_path / "out", config_path=cfg, base_dir=tmp_path,
                      use_judge=False)
    evaluate_runs(a, b, tmp_path / "out", config_path=cfg, base_dir=tmp_path, use_judge=False,
                  allow_unpaired=True)
    cfg_out = json.loads((tmp_path / "out" / "evaluator_config.json").read_text())
    assert cfg_out["paired_check"]["allowed_unpaired"] is True
    assert any("models" in f for f in cfg_out["paired_check"]["failures"])


def test_evaluate_reports_each_question_and_the_results_do_not_change(tmp_path, record):
    (a, b), cfg = _two_runs(tmp_path, record)
    plain = evaluate_runs(a, b, tmp_path / "plain", config_path=cfg, base_dir=tmp_path,
                          use_judge=False)
    events = []
    with_cb = evaluate_runs(a, b, tmp_path / "cb", config_path=cfg, base_dir=tmp_path,
                            use_judge=False, on_progress=events.append)
    assert with_cb == plain
    for f in ("per_question.csv", "paired.csv", "paired_summary.csv", "cost.csv"):
        assert (tmp_path / "plain" / f).read_text() == (tmp_path / "cb" / f).read_text(), f
    assert [(e.task, e.phase, e.label) for e in events] == [
        ("evaluate", "start", "A"), ("evaluate", "step", record.id), ("evaluate", "done", "A"),
        ("evaluate", "start", "B"), ("evaluate", "step", record.id), ("evaluate", "done", "B")]
    assert all(e.total == 1 for e in events) and events[1].done == 1


def test_cli_evaluate_prints_one_line_per_question_without_a_terminal(tmp_path, record,
                                                                      monkeypatch):
    from typer.testing import CliRunner

    import dwb.cli as cli

    (a, b), cfg = _two_runs(tmp_path, record)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli.app, ["experiment", "evaluate", str(a), str(b), "--out",
                                     str(tmp_path / "rep"), "--config", str(cfg), "--no-judge"])
    assert r.exit_code == 0, r.output
    assert "Ctrl-C stops" in r.stderr
    lines = r.stderr.splitlines()
    assert "Evaluating condition A: 1 traces" in lines and f"  [1/1] {record.id}" in lines
    assert "  ✓ Condition B: 1 traces scored" in lines
    assert "\r" not in r.stderr and "\x1b[" not in r.stderr


# --- the web gate (conditional web search) ------------------------------------------------


def _gated(trace, sufficient, *stages):
    from dwb.trace import StageRecord

    check = None if sufficient == "off" else {
        "sufficient": sufficient, "reason": "r", "judged": True, "seen_ids": ["i1"]}
    extra = [StageRecord(name=n, input_tokens=t, output_tokens=0, latency_ms=lat, extra=x)
             for n, t, lat, x in stages]
    return trace.model_copy(update={"web_check": check, "stages": trace.stages + extra})


def test_web_check_label_and_gate_cost(record):
    from dwb.eval.report import cost_rows

    t = _gated(make_trace(), True,
               ("check_local", 300, 5, {}),
               ("web_retrieve", 0, 40, {"used": False, "source": "live"}))
    row = question_row(record, t, None, MatchRules())
    assert row["web_check"] == "sufficient"
    assert row["total_tokens"] == 650 + 300  # generate + the judge
    assert row["latency_ms"] == 12 + 5  # the capture-only web call is not counted
    web = [r for r in cost_rows([t]) if r["stage"] == "web_retrieve"]
    assert web == []  # capture-only calls are not web cost
    assert question_row(record, _gated(make_trace(), False), None,
                        MatchRules())["web_check"] == "insufficient"
    assert question_row(record, _gated(make_trace(), None), None,
                        MatchRules())["web_check"] == "judge_failed"
    assert question_row(record, make_trace(), None, MatchRules())["web_check"] == ""


def test_web_gate_summary_splits_by_verdict_agreement(record):
    from dwb.eval.report import web_gate_summary

    d = record.model_dump()
    d["allowed_sources"] = ["code", "pdf", "web"]
    d["evidence"].append({"id": "e3", "alternatives": [
        {"source_type": "web", "url": "https://x.org/a"}]})
    web_ref = type(record).model_validate(d)
    verdicts = {"q1": (True, True), "q2": (False, False), "q3": (True, False)}
    rows_a, rows_b = [], []
    for qid, (va, vb) in verdicts.items():
        rec = (web_ref if qid == "q3" else record).model_copy(update={"id": qid})
        rows_a.append(question_row(rec, _gated(make_trace("A", qid=qid), va), None, MatchRules()))
        rows_b.append(question_row(rec, _gated(make_trace("B", qid=qid), vb), None, MatchRules()))
    rows_a.append(question_row(record.model_copy(update={"id": "q4"}),
                               make_trace("A", qid="q4"), None, MatchRules()))  # no gate
    s = web_gate_summary(rows_a, rows_b)
    assert (s["both_sufficient"], s["both_insufficient"], s["discordant"]) == (1, 1, 1)
    assert s["n_gated"] == 3
    assert s["escalation_rate_A"] == round(1 / 3, 6) and s["escalation_rate_B"] == round(2 / 3, 6)
    # the success-criterion check: web-needing items the judge called sufficient
    assert s["web_reference_judged_sufficient_A"] == 1
    assert s["web_reference_judged_sufficient_B"] == 0


def control_record():
    from dwb.benchmark.schemas import QuestionRecord

    return QuestionRecord(id="ctl", kind="control", repo="flask", repo_sha="x",
                          question="What is Flask's market share?", allowed_sources=["code"],
                          reference_answer="Not answerable from the corpus.",
                          provenance={"author": "x"})


def test_control_that_declines_scores_one_without_judge_calls():
    from dwb.trace import AnswerClaim

    j = make_judge()
    t = make_trace(qid="ctl", claims=[AnswerClaim(text="Not in the evidence.", kind="gap")])
    row = question_row(control_record(), t, j, MatchRules())
    assert row["subset"] == "control" and row["control_correct"] == 1.0
    assert row["correctness"] is None and row["recall_final"] is None
    assert row["citation_support"] is None and j.stats.calls == 0


def test_control_with_an_answer_claim_scores_zero():
    j = make_judge()
    row = question_row(control_record(), make_trace(qid="ctl"), j, MatchRules())
    assert row["control_correct"] == 0.0 and j.stats.calls == 0


def test_failed_control_scores_zero_and_is_paired():
    j = make_judge()
    failed = make_trace("B", qid="ctl", claims=[], errors=["boom"])
    a = [question_row(control_record(), make_trace("A", qid="ctl", claims=[
        __import__("dwb.trace", fromlist=["AnswerClaim"]).AnswerClaim(text="n/a", kind="gap")]),
        j, MatchRules())]
    b = [question_row(control_record(), failed, j, MatchRules())]
    assert b[0]["control_correct"] == 0.0 and b[0]["latency_ms"] is None and j.stats.calls == 0
    s = {(r["subset"], r["metric"]): r for r in paired_summary(pair(a, b)[0])}
    cc = s[("control", "control_correct")]
    assert cc["n_paired"] == 1 and cc["regressed"] == 1
    assert s[("primary", "control_correct")]["n_paired"] == 0  # controls are not primary


def test_summary_has_one_block_per_subset_plus_primary_and_all(record):
    j = make_judge()
    public = record.model_copy(update={
        "id": "p", "kind": "public", "expected_claims": [], "reference_answer": "ref",
        "provenance": record.provenance.model_copy(update={"upstream_row_id": "1"})})
    code = record.model_copy(update={"id": "c", "evidence": record.evidence[:1]})
    recs = [record, public, code]
    rows = {c: [question_row(r, make_trace(c, qid=r.id), j, MatchRules()) for r in recs]
            for c in "AB"}
    assert [r["subset"] for r in rows["A"]] == ["code+pdf", "public", "code"]
    s = paired_summary(pair(rows["A"], rows["B"])[0])
    subsets = list(dict.fromkeys(r["subset"] for r in s))
    assert subsets == ["primary", "public", "code", "code+pdf", "all"]
    n = {r["subset"]: r["n_paired"] for r in s if r["metric"] == "recall_final"}
    assert n["primary"] == 3 and n["all"] == 3
