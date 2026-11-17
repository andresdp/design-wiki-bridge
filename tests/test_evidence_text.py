import shutil
import subprocess
from pathlib import Path

import pytest

from dwb.evidence_text import EvidenceTextError, hydrate
from dwb.repo import RepoReader
from dwb.retrieval.chunking import ChunkingConfig, chunk_repo
from dwb.trace import TraceRecord
from graph_fakes import FakeRetriever, components, pdf_items
from test_graph import custom_q, manifest, run

TOY = Path(__file__).resolve().parent / "fixtures" / "toy_repo"


def _git_toy(tmp_path) -> tuple[Path, str]:
    r = tmp_path / "toy"
    shutil.copytree(TOY, r)
    g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True,  # noqa: E731
                                  capture_output=True, text=True).stdout.strip()
    g("init", "-q")
    g("add", ".")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c")
    return r, g("rev-parse", "HEAD")


def test_code_text_by_reference_round_trips_exactly(tmp_path):
    repo, sha = _git_toy(tmp_path)
    items = [i for i in chunk_repo(RepoReader.from_git(repo, sha), ChunkingConfig())
             if i.locator.path.endswith(".py")]
    originals = {i.id: i.text for i in items}
    c = components("A")
    c.code_retriever = FakeRetriever(items)
    c.pdf_retriever = FakeRetriever(pdf_items())
    t = run(manifest("A"), c, custom_q(sources=("code", "pdf")))
    code_ids = [i for i in t.final_context_ids if t.evidence[i]["source_type"] == "code"]
    pdf_ids = [i for i in t.final_context_ids if t.evidence[i]["source_type"] == "pdf"]
    assert code_ids and pdf_ids
    assert all("text" not in t.evidence[i] for i in code_ids)  # stored by reference
    assert all(t.evidence[i]["text"] for i in pdf_ids)  # PDF text stays inline
    # serialize as the runner does, then restore
    back = TraceRecord.model_validate_json(t.model_dump_json())
    assert hydrate([back], repo) == len(code_ids)
    assert all(back.evidence[i]["text"] == originals[i] for i in code_ids)
    assert hydrate([back], repo) == 0  # idempotent


def test_chat_traces_keep_code_text():
    import asyncio

    from dwb.graph import build_graph, trace_from_state

    c = components("A", pdf=False)
    m = manifest("A")
    state = asyncio.run(build_graph(c, m).ainvoke({"question": custom_q(sources=("code",))}))
    t = trace_from_state(state, m, keep_code_text=True)
    assert all("text" in t.evidence[i] for i in t.final_context_ids)


def test_missing_checkout_is_a_clear_error(tmp_path):
    t = TraceRecord(question_id="q", run_id="r", condition="A", final_context_ids=["x"],
                    evidence={"x": {"source_type": "code", "locator": {
                        "commit": "abc123", "path": "a.py", "start_line": 1, "end_line": 2}}})
    with pytest.raises(EvidenceTextError, match="needs the checkout"):
        hydrate([t], tmp_path / "missing")
    with pytest.raises(EvidenceTextError, match="needs the checkout"):
        hydrate([t], None)


def test_unknown_commit_is_a_clear_error(tmp_path):
    repo, _ = _git_toy(tmp_path)
    t = TraceRecord(question_id="q", run_id="r", condition="A", final_context_ids=["x"],
                    evidence={"x": {"source_type": "code", "locator": {
                        "commit": "0" * 40, "path": "a.py", "start_line": 1, "end_line": 2}}})
    with pytest.raises(EvidenceTextError, match="not in"):
        hydrate([t], repo)
