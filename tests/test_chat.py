import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

from dwb.chat import (
    ChatSession,
    ChatSettings,
    Sources,
    build_manifest,
    parse_command,
    print_answer,
)
from dwb.workspace import Workspace, WorkspaceError
from graph_fakes import components

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "tests" / "fixtures" / "toy_repo"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def ws(tmp_path) -> Workspace:
    """A workspace with one repo ('toy'), a code index, a PDF index, and no wiki."""
    co = tmp_path / "artifacts" / "checkouts" / "toy"
    shutil.copytree(TOY, co)
    _git(co, "init", "-q")
    _git(co, "add", ".")
    _git(co, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    sha = _git(co, "rev-parse", "HEAD")
    (tmp_path / "configs" / "pdfs").mkdir(parents=True)
    (tmp_path / "configs" / "repos.yaml").write_text(yaml.safe_dump({"repos": {"toy": {
        "url": "https://example.org/toy.git", "sha": sha, "checkout": "artifacts/checkouts/toy",
        "tag": "v1"}}}))
    (tmp_path / "configs" / "pdfs" / "toy.yaml").write_text(yaml.safe_dump(
        {"corpus_id": "toy-v1", "documents": []}))
    (tmp_path / "configs" / "chat.yaml").write_text(yaml.safe_dump({
        "models": {"generator": "gen-x", "reranker": "gen-x", "embedding": "emb-x"},
        "sources": {"wiki": False, "pdf": True, "web": False},
        "repos": {"toy": {"pdf_corpus": "configs/pdfs/toy.yaml",
                          "web_exclude_domains": ["toy.example.org"]}}}))
    for kind, idx, parts in (("code", "code-toy-1", {"repo": "toy", "sha": sha}),
                             ("pdf", "pdf-toy-1", {"corpus_id": "toy-v1"})):
        d = tmp_path / "artifacts" / "indexes" / idx
        d.mkdir(parents=True)
        (d / "index.json").write_text(json.dumps({
            "index_id": idx, "kind": kind, "embedding_model": "emb-x", "n_items": 1,
            "parts": parts, "created_at": "2026-11-17"}))
    return Workspace(root=tmp_path)


def test_manifest_from_sources_and_discovery(ws):
    m, notes = build_manifest(ws, ChatSettings(repo="toy"))
    assert m.condition == "A" and m.indexes.code_index_id == "code-toy-1"
    assert m.pdf_enabled and m.indexes.pdf_index_id == "pdf-toy-1"
    assert not m.web.enabled and notes == []
    m, _ = build_manifest(ws, ChatSettings(repo="toy", sources=Sources(pdf=False, web=True)))
    assert not m.pdf_enabled and m.web.enabled and m.web.mode == "record"
    assert m.web.store_id == "chat-toy" and m.web.exclude_domains == ["toy.example.org"]
    m, _ = build_manifest(ws, ChatSettings(repo="toy", generator="other"))
    assert m.models.generator == m.models.reranker == "other"


def test_missing_artifacts_give_the_fixing_command(ws):
    with pytest.raises(WorkspaceError, match="dwb wiki snapshot toy"):
        build_manifest(ws, ChatSettings(repo="toy", sources=Sources(wiki=True)))
    with pytest.raises(WorkspaceError, match="dwb index code toy --embedding-model other"):
        build_manifest(ws, ChatSettings(repo="toy", embedding="other"))
    with pytest.raises(WorkspaceError, match="unknown repository"):
        build_manifest(ws, ChatSettings(repo="nope"))
    shutil.rmtree(ws.root / "artifacts" / "indexes" / "pdf-toy-1")
    m, notes = build_manifest(ws, ChatSettings(repo="toy"))
    assert not m.pdf_enabled and "dwb index pdf" in notes[0]  # PDFs degrade, not fail


def test_session_answers_with_repo_always_on_and_toggles(ws):
    async def go():
        async with ChatSession(ws, ChatSettings(repo="toy", sources=Sources(pdf=True)),
                               components=components("A")) as s:
            t1 = await s.ask("How does the app run?")
            await s.set_sources(pdf=False)
            t2 = await s.ask("And without PDFs?")
            return s, t1, t2

    s, t1, t2 = asyncio.run(go())
    assert not t1.errors and t1.claims and t1.final_context_ids
    assert any(t1.evidence[i]["source_type"] == "pdf" for i in t1.candidates["pdf"])
    assert t2.candidates["pdf"] == [] and t2.candidates["repo"]
    assert s.sources == Sources(wiki=False, pdf=False, web=False)
    assert t1.question_id != t2.question_id


def test_print_answer_lists_cited_sources():
    from dwb.trace import AnswerClaim, TraceRecord

    t = TraceRecord(question_id="q", run_id="r", condition="A", aliases={"E1": "ev_a"},
                    final_context_ids=["ev_a"], evidence={"ev_a": {
                        "source_type": "code", "text": "def run(): ...",
                        "locator": {"path": "src/toy/app.py", "start_line": 23, "end_line": 25},
                        "provenance": {"branch": "vector"}}},
                    claims=[AnswerClaim(text="run returns the port", cited_aliases=["E1"],
                                        evidence_ids=["ev_a"])])
    con = Console(record=True, width=120)
    print_answer(t, console=con)
    text = con.export_text()
    assert "run returns the port" in text and "[E1]" in text
    assert "src/toy/app.py:23-25" in text and "vector search" in text


@pytest.mark.parametrize("line,expected", [
    ("/web on", ("web", "on")), ("/WIKI off", ("wiki", "off")), ("/q", ("quit", None)),
    ("/help", ("help", None)), ("  How does it work? ", ("ask", "How does it work?")),
    ("", ("noop", None)),
])
def test_parse_command(line, expected):
    assert parse_command(line) == expected


def test_cli_ask_and_status(ws, monkeypatch):
    import dwb.cli as cli
    import dwb.runner as runner

    monkeypatch.chdir(ws.root)
    monkeypatch.setattr(cli, "WS", Workspace())
    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))
    r = CliRunner().invoke(cli.app, ["ask", "How does the app run?", "--repo", "toy", "--no-pdf"])
    assert r.exit_code == 0, r.output
    assert "Answer" in r.output and "Sources" in r.output
    r = CliRunner().invoke(cli.app, ["ask", "q?", "--repo", "toy", "--wiki"])
    assert r.exit_code == 2 and "dwb wiki snapshot toy" in r.output
    r = CliRunner().invoke(cli.app, ["status", "--embedding-model", "emb-x"])
    assert r.exit_code == 0 and "code-toy-1" in r.output


@pytest.mark.parametrize("cmd", [[], ["ask", "--help"], ["chat", "--help"], ["index", "--help"],
                                 ["index", "code", "--help"], ["experiment", "run", "--help"],
                                 ["experiment", "evaluate", "--help"], ["wiki", "snapshot",
                                                                        "--help"]])
def test_every_command_has_help(cmd):
    import dwb.cli as cli

    r = CliRunner().invoke(cli.app, cmd)
    assert r.exit_code in (0, 2) and "Usage" in r.output


def test_render_shows_gap_claims():
    from dwb.trace import AnswerClaim, TraceRecord

    t = TraceRecord(question_id="q", run_id="r", condition="B", aliases={},
                    final_context_ids=["x"], evidence={},
                    claims=[AnswerClaim(text="the default status code", kind="gap")])
    con = Console(record=True, width=120)
    print_answer(t, console=con)
    assert "Not established by the evidence" in con.export_text()


def test_failed_source_switch_keeps_the_old_session(ws):
    async def go():
        async with ChatSession(ws, ChatSettings(repo="toy", sources=Sources(pdf=True)),
                               components=components("A")) as s:
            before = s.manifest
            with pytest.raises(WorkspaceError, match="dwb wiki snapshot"):
                await s.set_sources(wiki=True)  # no wiki snapshot exists
            assert s.manifest is before and s.sources == Sources(wiki=False, pdf=True)
            t = await s.ask("Still answering?")
            return t

    t = asyncio.run(go())
    assert not t.errors and t.claims
