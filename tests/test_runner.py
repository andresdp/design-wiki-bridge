import json
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
    (tmp_path / "src/dwb/prompt_files/generate_v1.md").write_text(
        (ROOT / "src/dwb/prompt_files/generate_v1.md").read_text())
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
