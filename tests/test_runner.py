import json
import re
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
    (tmp_path / "src/dwb/prompt_files").mkdir(parents=True)
    (tmp_path / "src/dwb/prompt_files/generate_v2.md").write_text(
        (ROOT / "src/dwb/prompt_files/generate_v2.md").read_text())
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
                         "empty_context", "web_triggered", "unresolved_citation_ids",
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
