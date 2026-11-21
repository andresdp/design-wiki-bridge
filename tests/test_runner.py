import json
import re
import shutil
from pathlib import Path

import pytest

from dwb.benchmark.freeze import freeze
from dwb.benchmark.schemas import save_records
from dwb.manifest import QuestionSetRef
from dwb.runner import QuestionSetError, run_manifest
from dwb.trace import read_traces
from dwb.web.tavily_store import TavilyStore
from graph_fakes import SHA, components
from test_graph import custom_q, manifest, public_q

ROOT = Path(__file__).resolve().parents[1]


def setup(tmp_path, records, *, frozen=True, rel="data/questions/test/flask.jsonl"):
    shutil.copytree(ROOT / "src/dwb/prompt_files", tmp_path / "src/dwb/prompt_files")
    path = tmp_path / rel
    save_records(records, path)
    digest = freeze(path) if frozen else None
    m = manifest("A")
    return m.model_copy(update={"question_set": QuestionSetRef(id="t", path=rel, hash=digest)})


def test_run_dir_contents_and_error_isolation(tmp_path):
    q_ok = public_q()
    q_miss = custom_q().model_copy(update={"id": "flask-cus-002"})
    m = setup(tmp_path, [q_miss, q_ok])
    c = components("A", web_store=TavilyStore(tmp_path / "tav", "empty", mode="replay"))
    summary = run_manifest(m, c, base_dir=tmp_path, runs_dir=tmp_path / "runs")
    rd = tmp_path / "runs" / m.run_id
    assert (rd / "manifest.yaml").exists()
    traces = read_traces(rd / "traces.jsonl")
    assert [t.question_id for t in traces] == ["flask-cus-002", "flask-pub-000"]
    assert "ReplayMiss" in traces[0].errors[0]
    assert traces[1].answer and not traces[1].errors  # run continued
    saved = json.loads((rd / "run_summary.json").read_text())
    assert saved["failed"] == 1 and saved["questions"] == 2 and summary["failed"] == 1
    frozen_manifest = (rd / "manifest.yaml").read_text()
    assert "sha256" in frozen_manifest  # prompt hashes frozen into the run copy


def test_hash_mismatch_aborts_before_any_question(tmp_path):
    m = setup(tmp_path, [public_q()])
    m = m.model_copy(update={"question_set": m.question_set.model_copy(update={"hash": "0" * 64})})
    with pytest.raises(QuestionSetError, match="hash"):
        run_manifest(m, components("A"), base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_unfrozen_test_set_refused(tmp_path):
    m = setup(tmp_path, [public_q()], frozen=False)
    with pytest.raises(QuestionSetError, match="not frozen"):
        run_manifest(m, components("A"), base_dir=tmp_path, runs_dir=tmp_path / "runs")


def test_unfrozen_dev_set_allowed(tmp_path):
    m = setup(tmp_path, [public_q()], frozen=False, rel="data/questions/dev/flask.jsonl")
    s = run_manifest(m, components("A"), base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert s["questions"] == 1 and s["failed"] == 0


def test_records_with_other_sha_rejected(tmp_path):
    q = public_q().model_copy(update={"repo_sha": "deadbeef"})
    m = setup(tmp_path, [q])
    with pytest.raises(QuestionSetError, match="different SHA"):
        run_manifest(m, components("A"), base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert SHA != "deadbeef"


def test_code_index_must_match_the_manifest_chunking_and_sha():
    from dwb.retrieval.vector_store import IndexInfo
    from dwb.runner import IndexMismatchError, check_code_index

    m = manifest("A")

    def info(**parts):
        return IndexInfo(index_id="code-x", kind="code", embedding_model="e", n_items=1,
                         parts={"sha": m.repo_sha, **parts})

    from dwb.retrieval.chunking import ChunkingConfig

    v = ChunkingConfig.chunker_version
    check_code_index(info(chunking={"max_span_tokens": m.budgets.max_span_tokens,
                                    "chunker_version": v}), m)
    with pytest.raises(IndexMismatchError, match="--max-span-tokens"):
        check_code_index(info(chunking={"max_span_tokens": 800, "chunker_version": v}), m)
    with pytest.raises(IndexMismatchError, match="chunker version 1"):
        check_code_index(info(chunking={"max_span_tokens": m.budgets.max_span_tokens}), m)
    with pytest.raises(IndexMismatchError, match="manifest pins"):
        check_code_index(info(sha="0" * 40), m)


def test_condition_b_needs_a_checkout(tmp_path, monkeypatch):
    from dwb.runner import build_components

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    m = manifest("B").model_copy(update={"checkout": None, "pdf_enabled": False})
    m = m.model_copy(update={"web": m.web.model_copy(update={"enabled": False})})
    with pytest.raises(ValueError, match="needs manifest.checkout"):
        build_components(m, base_dir=ROOT, index_root=tmp_path, tavily_root=tmp_path,
                         wiki_root=tmp_path)


def test_wiki_session_is_entered_once_and_its_startup_is_recorded(tmp_path):
    from dwb.trace import StageRecord
    from graph_fakes import FakeWiki

    class SessionWiki(FakeWiki):
        entered = exited = 0
        startup_stage = StageRecord(name="wiki_server_start", latency_ms=12.0)

        async def __aenter__(self):
            SessionWiki.entered += 1
            return self

        async def __aexit__(self, *exc):
            SessionWiki.exited += 1

    from graph_fakes import wiki_refs, wiki_sections

    q1, q2 = public_q(), public_q().model_copy(update={"id": "flask-pub-001"})
    m = setup(tmp_path, [q1, q2])
    m = m.model_copy(update={"condition": "B", "run_id": "flask-B-t",
                             "indexes": manifest("B").indexes})
    c = components("B", wiki=SessionWiki(wiki_refs(), wiki_sections()))
    s = run_manifest(m, c, base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert SessionWiki.entered == 1 and SessionWiki.exited == 1  # one session for the run
    assert [st["name"] for st in s["run_stages"]] == ["wiki_server_start"]


def test_experiment_run_checks_questions_before_touching_the_checkout(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import dwb.cli as cli
    import dwb.runner as runner

    m = setup(tmp_path, [public_q()], frozen=False)
    path = tmp_path / "m.yaml"
    path.write_text(m.model_copy(update={"question_set": m.question_set.model_copy(
        update={"hash": "sha256:wrong"})}).to_yaml())
    called = []
    monkeypatch.setattr(runner, "build_components", lambda *a, **k: called.append(1))
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli.app, ["experiment", "run", str(path)])
    assert r.exit_code != 0 and "hash" in r.output and not called


def _cli_run(tmp_path, monkeypatch, *flags):
    from typer.testing import CliRunner

    import dwb.cli as cli
    import dwb.runner as runner

    m = setup(tmp_path, [public_q()])
    path = tmp_path / "m.yaml"
    path.write_text(m.to_yaml())
    monkeypatch.setattr(runner, "build_components", lambda *a, **k: components("A"))
    monkeypatch.chdir(tmp_path)
    return m, CliRunner().invoke(cli.app, [*flags, "experiment", "run", str(path), "--overwrite"])


def test_cli_experiment_run_piped_prints_the_summary_json(tmp_path, monkeypatch):
    m, r = _cli_run(tmp_path, monkeypatch)
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert r.stdout == json.dumps(out, indent=2) + "\n"  # json.dumps(summary, indent=2)
    assert list(out) == ["run_id", "condition", "questions", "failed", "degraded",
                         "empty_context", "web_triggered", "web_capture_failed",
                         "unresolved_citation_ids",
                         "elapsed_s", "run_stages"]
    saved = json.loads((tmp_path / "runs" / m.run_id / "run_summary.json").read_text())
    assert all(saved[k] == v for k, v in out.items())


def test_cli_experiment_run_at_a_terminal(tmp_path, monkeypatch, tty):
    m, r = _cli_run(tmp_path, monkeypatch)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith(f"{m.repo} · research mode · {m.repo_sha[:12]} · run {m.run_id}")
    assert re.search(r"^questions\s+1$", r.stdout, re.M)
    assert re.search(r"^failed\s+0$", r.stdout, re.M) and "{" not in r.stdout
    assert (f"✓ Run {m.run_id} finished → next: dwb experiment evaluate runs/{m.run_id} "
            "runs/<B> --out reports/<name>") in r.stderr


def test_cli_experiment_check_piped_and_at_a_terminal(monkeypatch):
    from typer.testing import CliRunner

    import dwb.cli as cli
    from dwb import ui
    from dwb.manifest import check_paired, load_manifest

    monkeypatch.chdir(ROOT)
    pair = ["configs/manifests/flask-A.yaml", "configs/manifests/flask-B.yaml"]
    a, b = (load_manifest(p) for p in pair)
    r = CliRunner().invoke(cli.app, ["experiment", "check", *pair])
    assert r.exit_code == 0, r.output
    assert r.stdout == json.dumps(check_paired(a, b).model_dump(), indent=2) + "\n"
    monkeypatch.setenv("NO_COLOR", "1")
    ui.force_terminal(True)
    try:
        r = CliRunner().invoke(cli.app, ["experiment", "check", *pair])
        rj = CliRunner().invoke(cli.app, ["--json", "experiment", "check", *pair])
    finally:
        ui.force_terminal(None)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith(f"flask · research mode · {a.repo_sha[:12]} · "
                               f"{a.run_id} vs {b.run_id}\n")
    assert "✓ Paired check passed" in r.stderr and f"next: dwb experiment run {pair[0]}" \
        in r.stderr
    assert json.loads(rj.stdout)["ok"] is True


def test_cli_experiment_evaluate_piped_and_at_a_terminal(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import dwb.cli as cli
    import dwb.eval.command as command
    from dwb import ui

    res = {"paired": 1, "judge_calls": 0, "judge_cache_hits": 0, "missing_from_one_side": []}
    monkeypatch.setattr(command, "evaluate_runs", lambda *a, **k: res)
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    cmd = ["experiment", "evaluate", str(tmp_path / "A"), str(tmp_path / "B"), "--out",
           str(tmp_path / "rep"), "--no-judge"]
    r = CliRunner().invoke(cli.app, cmd)
    assert r.exit_code == 0 and r.stdout == json.dumps(res, indent=2) + "\n"
    monkeypatch.setenv("NO_COLOR", "1")
    ui.force_terminal(True)
    try:
        r = CliRunner().invoke(cli.app, cmd)
    finally:
        ui.force_terminal(None)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith("A vs B · research mode\n")
    assert re.search(r"^paired\s+1$", r.stdout, re.M)
    assert f"✓ Report written to {tmp_path / 'rep'}" in r.stderr


# ------------------------------------------------------------------------------ progress


def _mixed_run(tmp_path, runs, on_progress=None):
    """Three questions: one fails (Tavily replay miss), one is degraded (the PDF retriever
    raises), one is ok (code only)."""
    from test_degraded import Boom

    qs = [custom_q().model_copy(update={"id": "q-failed"}),
          custom_q(sources=("code", "pdf")).model_copy(update={"id": "q-degraded"}),
          custom_q(sources=("code",)).model_copy(update={"id": "q-ok"})]
    m = setup(tmp_path, qs)
    c = components("A", web_store=TavilyStore(tmp_path / "tav", "empty", mode="replay"))
    c.pdf_retriever = Boom()
    return m, run_manifest(m, c, base_dir=tmp_path, runs_dir=runs, on_progress=on_progress)


def test_run_reports_each_question_outcome_and_counts_match_the_summary(tmp_path):
    events = []
    m, summary = _mixed_run(tmp_path, tmp_path / "runs", events.append)
    assert [(e.task, e.phase) for e in events] == [("run", "start")] + [("run", "step")] * 3 \
        + [("run", "done")]
    start, *steps, done = events
    assert start.total == 3 and start.data["traces"] == tmp_path / "runs" / m.run_id / \
        "traces.jsonl"
    assert [(e.label, e.outcome, e.done) for e in steps] == [
        ("q-failed", "failed", 1), ("q-degraded", "degraded", 2), ("q-ok", "ok", 3)]
    assert all(e.elapsed_s > 0 and e.data["trace"].question_id == e.label for e in steps)
    saved = json.loads((tmp_path / "runs" / m.run_id / "run_summary.json").read_text())
    last = steps[-1].data
    assert (last["failed"], last["degraded"], last["empty_context"]) == \
        (saved["failed"], saved["degraded"], saved["empty_context"]) == (1, 1, 0)
    assert done.data == summary


def test_outcome_names_empty_context():
    from dwb.runner import outcome
    from dwb.trace import TraceRecord

    def rec(**kw):
        return TraceRecord(question_id="q", run_id="r", condition="A", **kw)

    assert outcome(rec()) == "ok"
    assert outcome(rec(errors=["x"], empty_context=True)) == "failed"
    assert outcome(rec(empty_context=True, warnings=["pdf: boom"])) == "empty"
    assert outcome(rec(warnings=["pdf: boom"])) == "degraded"


_VOLATILE = {"elapsed_s", "latency_ms", "written_at", "created_at", "started_at",
             "finished_at", "timestamp"}


def _stable(obj):
    """A trace or summary without its wall-clock fields (they differ between any two runs)."""
    if isinstance(obj, dict):
        return {k: _stable(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_stable(v) for v in obj]
    return obj


def test_traces_and_summary_are_the_same_with_and_without_progress(tmp_path):
    m, _ = _mixed_run(tmp_path / "plain", tmp_path / "plain" / "runs")
    m2, _ = _mixed_run(tmp_path / "cb", tmp_path / "cb" / "runs", lambda e: None)
    a, b = tmp_path / "plain/runs" / m.run_id, tmp_path / "cb/runs" / m2.run_id
    for name in ("traces.jsonl", "run_summary.json"):
        ta, tb = a.joinpath(name).read_text(), b.joinpath(name).read_text()
        ja = [json.loads(ln) for ln in ta.splitlines()] if name.endswith("l") else \
            json.loads(ta)
        jb = [json.loads(ln) for ln in tb.splitlines()] if name.endswith("l") else \
            json.loads(tb)
        assert _stable(ja) == _stable(jb), name
        assert list(ja if isinstance(ja, dict) else ja[0]) == \
            list(jb if isinstance(jb, dict) else jb[0])  # same fields, same order


def _cli_run_two(tmp_path, monkeypatch, *flags, stop_at=None):
    from typer.testing import CliRunner

    import dwb.cli as cli
    import dwb.runner as runner

    qs = [public_q(), public_q().model_copy(update={"id": "flask-pub-001"})]
    m = setup(tmp_path, qs)
    path = tmp_path / "m.yaml"
    path.write_text(m.to_yaml())
    monkeypatch.setattr(runner, "build_components", lambda *a, **k: components("A"))
    if stop_at is not None:
        real, calls = runner.trace_from_state, []

        def interrupt(state, m):
            calls.append(1)
            if len(calls) == stop_at:
                raise KeyboardInterrupt
            return real(state, m)

        monkeypatch.setattr(runner, "trace_from_state", interrupt)
    monkeypatch.chdir(tmp_path)
    return m, CliRunner().invoke(cli.app, [*flags, "experiment", "run", str(path),
                                           "--overwrite"])


def test_cli_run_progress_without_a_terminal_is_one_plain_line_per_question(tmp_path,
                                                                           monkeypatch):
    m, r = _cli_run_two(tmp_path, monkeypatch)
    assert r.exit_code == 0, r.output
    json.loads(r.stdout)  # stdout is still only the summary JSON
    assert "Ctrl-C stops; traces already written are kept." in r.stderr
    assert "Running 2 questions" in r.stderr
    assert re.search(r"^  ✓ flask-pub-000  \d+\.\d s$", r.stderr, re.M)
    assert re.search(r"^  ✓ flask-pub-001  \d+\.\d s$", r.stderr, re.M)
    assert "\r" not in r.stderr and "\x1b[" not in r.stderr
    assert "[1/2]" not in r.stderr  # the per-question log lines are debug records now


def test_cli_run_progress_at_a_no_color_terminal_is_plain_lines(tmp_path, monkeypatch, tty):
    m, r = _cli_run_two(tmp_path, monkeypatch)
    assert r.exit_code == 0, r.output
    assert re.search(r"^  ✓ flask-pub-001  \d+\.\d s$", r.stderr, re.M)
    assert "\r" not in r.stderr and "\x1b[" not in r.stderr


def test_cli_run_quiet_shows_no_progress(tmp_path, monkeypatch):
    m, r = _cli_run_two(tmp_path, monkeypatch, "-q")
    assert r.exit_code == 0, r.output
    assert "flask-pub-000" not in r.stderr and "Ctrl-C" not in r.stderr


def test_cli_run_progress_animates_on_a_color_terminal(tmp_path, monkeypatch):
    from dwb import ui

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    prev = ui.force_terminal(True)
    try:
        m, r = _cli_run_two(tmp_path, monkeypatch)
    finally:
        ui.force_terminal(prev)
    assert r.exit_code == 0, r.output
    assert "\x1b[" in r.stderr  # a live display was drawn
    assert "flask-pub-000" in r.stderr and "flask-pub-001" in r.stderr
    assert "Running 2 questions" not in r.stderr  # the bar replaces the plain lines


def test_cli_run_interrupted_after_one_question_reports_what_was_kept(tmp_path, monkeypatch):
    m, r = _cli_run_two(tmp_path, monkeypatch, stop_at=2)
    assert r.exit_code == 130, r.output
    assert "! Stopped" in r.stderr and "Traceback" not in r.output
    traces = Path("runs") / m.run_id / "traces.jsonl"
    assert f"  • kept: 1 trace in {traces}" in r.stderr.splitlines()
    assert ("Run incomplete (1 of 2 questions, no run_summary.json): it cannot be evaluated. "
            "Rerun with --overwrite to restart it.") in r.stderr
    assert len(read_traces(tmp_path / traces)) == 1
    assert not (tmp_path / "runs" / m.run_id / "run_summary.json").exists()


def test_record_runs_capture_web_even_when_the_gate_says_sufficient(tmp_path):
    """The runner turns on capture-all for record runs; replay runs do not."""
    from graph_fakes import judge
    from web.test_tavily_store import StubClient

    m = setup(tmp_path, [custom_q()])
    for mode, expect_calls in (("record", 1), ("replay", 0)):
        stub = StubClient()
        store = TavilyStore(tmp_path / mode, "s", mode=mode, client=stub)
        mm = m.model_copy(update={"web": m.web.model_copy(update={"mode": mode}),
                                  "run_id": f"r-{mode}"})
        if mode == "replay":  # replay needs the recording: copy it over
            shutil.copytree(tmp_path / "record" / "s", tmp_path / "replay" / "s")
        c = components("A", web_store=store, judge_=judge(True))
        run_manifest(mm, c, base_dir=tmp_path, runs_dir=tmp_path / "runs")
        (t,) = read_traces(tmp_path / "runs" / mm.run_id / "traces.jsonl")
        assert len(stub.calls) == expect_calls and not t.web_triggered
        assert ("web_retrieve" in [s.name for s in t.stages]) is (mode == "record")


def test_gated_manifests_get_the_same_judge_in_both_conditions(tmp_path, monkeypatch):
    """build_components wires the sufficiency judge from models.generator and
    prompts.sufficiency when the gate is on, and leaves it out when the gate is off."""
    from dwb import runner
    from dwb.prompts import SUFFICIENCY_PROMPT, load_prompt
    from dwb.sufficiency import SufficiencyJudge

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(runner, "check_code_index", lambda *a, **k: None)

    class FakeIndex:
        info = None

        @classmethod
        def open(cls, *a, **k):
            return cls()

    import dwb.retrieval.vector_store as vs
    monkeypatch.setattr(vs, "VectorIndex", FakeIndex)
    m = manifest("A").model_copy(update={"pdf_enabled": False})
    c = runner.build_components(m, base_dir=ROOT, index_root=tmp_path, tavily_root=tmp_path,
                                wiki_root=tmp_path)
    assert isinstance(c.judge, SufficiencyJudge)
    assert c.judge.model_id == m.models.generator
    assert c.judge.prompt_hash == load_prompt(SUFFICIENCY_PROMPT)[1]
    off = m.model_copy(update={"web": m.web.model_copy(update={"conditional_web_search": False})})
    c = runner.build_components(off, base_dir=ROOT, index_root=tmp_path, tavily_root=tmp_path,
                                wiki_root=tmp_path)
    assert c.judge is None


def test_a_gated_run_without_its_judge_prompt_stops_before_writing(tmp_path):
    from dwb.manifest import ManifestError

    m = setup(tmp_path, [custom_q()])
    m = m.model_copy(update={"prompts": {"generate": m.prompts["generate"]}})
    with pytest.raises(ManifestError, match="prompts.sufficiency"):
        run_manifest(m, components("A"), base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_experiment_check_fails_when_a_gated_manifest_lacks_its_judge_prompt(tmp_path):
    import yaml
    from typer.testing import CliRunner

    from dwb.cli import app

    for cond in ("A", "B"):
        d = yaml.safe_load((ROOT / "configs" / "manifests" / f"flask-{cond}.yaml").read_text())
        d["prompts"].pop("sufficiency")
        (tmp_path / f"{cond}.yaml").write_text(yaml.safe_dump(d))
    res = CliRunner().invoke(app, ["experiment", "check", str(tmp_path / "A.yaml"),
                                   str(tmp_path / "B.yaml")])
    assert res.exit_code != 0
    assert "prompts.sufficiency" in res.output


def test_failed_web_captures_are_counted_in_the_run_summary(tmp_path):
    from graph_fakes import judge

    class Down:
        last_call = {}

        def search(self, query, **kw):
            raise RuntimeError("tavily down")

    m = setup(tmp_path, [custom_q()])
    m = m.model_copy(update={"web": m.web.model_copy(update={"mode": "record"})})
    s = run_manifest(m, components("A", web_store=Down(), judge_=judge(True)),
                     base_dir=tmp_path, runs_dir=tmp_path / "runs")
    assert s["web_capture_failed"] == 1 and s["degraded"] == 0 and s["failed"] == 0
