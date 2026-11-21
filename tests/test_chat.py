import asyncio
import json
import re
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


def test_chat_web_is_gated_by_default_with_the_shipped_judge_prompt(ws):
    from dwb.manifest import require_runnable
    from dwb.prompts import SUFFICIENCY_PROMPT

    m, _ = build_manifest(ws, ChatSettings(repo="toy", sources=Sources(web=True)))
    assert m.web.conditional_web_search and m.web.gated
    assert m.prompts["sufficiency"].path == str(SUFFICIENCY_PROMPT)
    require_runnable(m)
    m, _ = build_manifest(ws, ChatSettings(repo="toy"))  # web off: no judge prompt needed
    assert "sufficiency" not in m.prompts
    cfg = yaml.safe_load((ws.root / "configs" / "chat.yaml").read_text())
    cfg["web"] = {"conditional_web_search": False}
    (ws.root / "configs" / "chat.yaml").write_text(yaml.safe_dump(cfg))
    m, _ = build_manifest(Workspace(root=ws.root),
                          ChatSettings(repo="toy", sources=Sources(web=True)))
    assert not m.web.gated and "sufficiency" not in m.prompts


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
    # piped: the research status rows, as they are
    assert r.stdout == json.dumps(Workspace().status("emb-x"), indent=2) + "\n"
    assert json.loads(r.stdout)[0]["code_index"] == "code-toy-1"


@pytest.fixture
def research(ws, monkeypatch):
    import dwb.cli as cli

    monkeypatch.chdir(ws.root)
    monkeypatch.setattr(cli, "WS", Workspace())
    return ws


def test_cli_status_at_a_terminal_uses_the_project_table_shape(research, tty):
    import dwb.cli as cli

    r = CliRunner().invoke(cli.app, ["status", "--embedding-model", "emb-x"])
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith("all repositories · research mode · indexes built with emb-x")
    lines = r.stdout.splitlines()
    assert lines[0] == "toy (v1)" and re.match(r"^Source\s+State\s+Detail$", lines[1])
    assert re.search(r"^code\s+ok\s+code-toy-1$", r.stdout, re.M)
    assert re.search(r"^wiki\s+missing\s+no snapshot", r.stdout, re.M)
    assert "✓ 1 repositories ready → next: dwb chat --repo <name>" in r.stderr
    r = CliRunner().invoke(cli.app, ["--json", "status", "--embedding-model", "emb-x"])
    assert json.loads(r.stdout)[0]["repo"] == "toy" and "research mode" not in r.stderr


def test_unknown_repo_lists_the_configured_ones(research):
    import dwb.cli as cli

    for cmd in (["ask", "q?", "--repo", "nope"], ["chat", "--repo", "nope"],
                ["index", "code", "nope"]):
        r = CliRunner().invoke(cli.app, cmd)
        assert r.exit_code == 2, (cmd, r.output)
        assert "unknown repository 'nope'. Configured: toy" in r.output


def test_ask_with_no_question_exits_2_without_a_terminal(research):
    import dwb.cli as cli

    r = CliRunner().invoke(cli.app, ["ask", "--repo", "toy"])
    assert r.exit_code == 2 and "no question given" in r.output and "--repo" in r.output


@pytest.mark.parametrize("stop", ["ctrl-c", "ctrl-d"])
def test_stopping_at_the_ask_question_prompt_is_a_stop(research, monkeypatch, tty, stop):
    import typer

    import dwb.cli as cli

    if stop == "ctrl-c":  # Click's prompt turns KeyboardInterrupt into Abort
        def prompt(*a, **kw):
            raise typer.Abort()
        monkeypatch.setattr(typer, "prompt", prompt)
    r = CliRunner().invoke(cli.app, ["ask", "--repo", "toy"], input="")  # ctrl-d: no input
    assert r.exit_code == 130, r.output
    assert "! Stopped" in r.output and "Aborted" not in r.output


def test_research_ask_at_a_terminal_has_the_header(research, monkeypatch, tty):
    import dwb.cli as cli
    import dwb.runner as runner

    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))
    sha = research.repo("toy").sha[:12]
    r = CliRunner().invoke(cli.app, ["ask", "How does the app run?", "--repo", "toy"])
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith(f"toy · research mode · {sha}\n")
    assert "Answer" in r.stdout and "Sources" in r.stdout


def _index_code(tmp_path, *flags):
    import dwb.cli as cli

    return CliRunner().invoke(cli.app, [*flags, "index", "code", "toy", "--path", str(TOY),
                                        "--sha", "f" * 40, "--fake", "--embedding-model",
                                        "fake-64", "--root", str(tmp_path / "idx")])


def test_cli_index_code_piped_and_at_a_terminal(research, tmp_path, monkeypatch):
    from dwb import ui

    r = _index_code(tmp_path)
    assert r.exit_code == 0, r.output
    info = json.loads(r.stdout)
    assert list(info) == ["index_id", "kind", "embedding_model", "n_items", "parts",
                          "created_at", "reused"]
    assert r.stdout == json.dumps(info, indent=2) + "\n"  # json.dumps(asdict(info), indent=2)
    monkeypatch.setenv("NO_COLOR", "1")
    ui.force_terminal(True)
    try:
        r = _index_code(tmp_path)
        rj = _index_code(tmp_path, "--json")
    finally:
        ui.force_terminal(None)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith("toy · research mode · ffffffffffff\n")
    assert re.search(rf"^index\s+{info['index_id']} \(reused\)$", r.stdout, re.M)
    assert re.search(rf"^items\s+{info['n_items']}$", r.stdout, re.M)
    assert f"✓ Indexed toy at ffffffffffff: {info['n_items']} code spans → next: " \
           "dwb chat --repo toy" in r.stderr
    assert json.loads(rj.stdout)["index_id"] == info["index_id"]


def test_cli_wiki_snapshot_piped_and_at_a_terminal(research, tmp_path, monkeypatch):
    import dwb.cli as cli
    from dwb import ui
    from dwb.retrieval.openwiki_client import WikiSnapshot

    r = research.repo("toy")
    shutil.copytree(ROOT / "tests/fixtures/openwiki/openwiki", r.checkout / "openwiki")
    last = r.checkout / "openwiki/.last-update.json"
    last.write_text(json.dumps({**json.loads(last.read_text()), "gitHead": r.sha}))
    cmd = ["wiki", "snapshot", "toy", "--provider", "openai", "--model", "gpt-5.6-luna",
           "--version", "0.9.0", "--root", str(tmp_path / "wikis")]
    r = CliRunner().invoke(cli.app, cmd)
    assert r.exit_code == 0, r.output
    snap = WikiSnapshot.model_validate_json(r.stdout)
    assert r.stdout == snap.model_dump_json(indent=2) + "\n"
    monkeypatch.setenv("NO_COLOR", "1")
    ui.force_terminal(True)
    try:
        r = CliRunner().invoke(cli.app, cmd)
    finally:
        ui.force_terminal(None)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith("toy · research mode · ")
    assert re.search(rf"^snapshot\s+{snap.snapshot_id}$", r.stdout, re.M)
    assert f"✓ Registered {snap.snapshot_id} → next: dwb chat --repo toy --wiki" in r.stderr


@pytest.mark.parametrize("cmd", [[], ["ask", "--help"], ["chat", "--help"], ["index", "--help"],
                                 ["index", "code", "--help"], ["experiment", "run", "--help"],
                                 ["experiment", "evaluate", "--help"], ["wiki", "snapshot",
                                                                        "--help"],
                                 ["benchmark", "--help"], ["benchmark", "freeze", "--help"]])
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


def test_reranker_defaults_to_the_generator(ws):
    (ws.root / "configs" / "chat.yaml").write_text(yaml.safe_dump({
        "models": {"generator": "gen-x", "embedding": "emb-x"}, "sources": {"pdf": False}}))
    m, _ = build_manifest(Workspace(root=ws.root), ChatSettings(repo="toy",
                                                                 sources=Sources(pdf=False)))
    assert m.models.reranker == "gen-x" and m.web.exclude_domains == []


def test_chat_stays_human_when_piped(research, monkeypatch):
    import dwb.chat as chat
    import dwb.cli as cli
    import dwb.runner as runner

    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))

    async def fake_repl(session, console, **kw):
        console.print(f"[bold]dwb chat[/] · {session.settings.repo}")

    monkeypatch.setattr(chat, "repl", fake_repl)
    for flags in ([], ["--json"]):
        r = CliRunner().invoke(cli.app, [*flags, "chat", "--repo", "toy"])
        assert r.exit_code == 0, r.output
        assert r.stdout == "dwb chat · toy\n"


def test_cli_index_pdf_piped_and_at_a_terminal(research, tmp_path, monkeypatch):
    import pymupdf

    import dwb.cli as cli
    from dwb import ui

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "The application object is a callable.")
    doc.save(tmp_path / "spec.pdf")
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(yaml.safe_dump({"corpus_id": "spec-v1", "documents": [
        {"doc_id": "spec", "version": "1", "path": "spec.pdf"}]}))
    cmd = ["index", "pdf", str(corpus), "--base-dir", str(tmp_path), "--fake",
           "--embedding-model", "fake-64", "--root", str(tmp_path / "idx")]
    r = CliRunner().invoke(cli.app, cmd)
    assert r.exit_code == 0, r.output
    info = json.loads(r.stdout)
    assert info["kind"] == "pdf" and r.stdout == json.dumps(info, indent=2) + "\n"
    monkeypatch.setenv("NO_COLOR", "1")
    ui.force_terminal(True)
    try:
        r = CliRunner().invoke(cli.app, cmd)
    finally:
        ui.force_terminal(None)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith("spec-v1 · research mode\n")
    assert re.search(r"^items\s+1$", r.stdout, re.M)
    assert "✓ Indexed spec-v1: 1 pages from 1 PDFs → next: dwb status" in r.stderr


# ------------------------------------------------------------------ chat header and Ctrl-C


def _reader(*lines):
    """A scripted prompt: each item is a line, or an exception type raised at the prompt."""
    it = iter(lines)

    async def read() -> str:
        item = next(it, EOFError)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        return item

    return read


def _chat(ws, *lines, patch_ask=None) -> str:
    from dwb import __version__, ui
    from dwb.chat import repl

    con = Console(record=True, width=200, theme=ui.THEME)  # as ui.out()

    async def go():
        async with ChatSession(ws, ChatSettings(repo="toy", sources=Sources(pdf=True)),
                               components=components("A")) as s:
            if patch_ask:
                patch_ask(s)
            await repl(s, con, context=("toy", "research mode", ws.repo("toy").sha[:12]),
                       read=_reader(*lines))

    asyncio.run(go())
    text = con.export_text()
    assert f"dwb {__version__}" in text
    return text


def test_chat_header_shows_context_sources_and_how_to_leave(ws):
    text = _chat(ws, "/sources")
    sha = ws.repo("toy").sha[:12]
    for part in ("Repository: toy", "Mode: research mode", f"Commit: {sha}",
                 "/help · /quit or Ctrl-D to leave"):
        assert part in text, part
    assert text.count("Sources: repository (vector search), pdf") == 2  # header and /sources


def test_ctrl_c_while_answering_stops_only_that_question(ws):
    def patch(s):
        real, calls = s.ask, []

        async def ask(q):
            calls.append(q)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return await real(q)

        s.ask = ask

    text = _chat(ws, "first?", "second?", patch_ask=patch)
    assert "! Stopped" in text
    assert text.index("! Stopped") < text.index("Answer")
    assert text.count("Answer") == 1  # the next question still answers


def test_sigint_while_answering_cancels_the_question(ws):
    import os
    import signal

    def patch(s):
        real, calls = s.ask, []

        async def ask(q):
            calls.append(q)
            if len(calls) == 1:
                os.kill(os.getpid(), signal.SIGINT)  # a real Ctrl-C
                await asyncio.sleep(10)
            return await real(q)

        s.ask = ask

    prev = signal.getsignal(signal.SIGINT)
    text = _chat(ws, "first?", "second?", patch_ask=patch)
    assert "! Stopped" in text and text.count("Answer") == 1
    assert signal.getsignal(signal.SIGINT) is prev  # the handler is restored


@pytest.mark.parametrize("key", [KeyboardInterrupt, EOFError])
def test_ctrl_c_or_ctrl_d_at_the_prompt_leaves_quietly(research, monkeypatch, key):
    import dwb.chat as chat
    import dwb.cli as cli
    import dwb.runner as runner

    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))
    monkeypatch.setattr(chat, "_reader", lambda session, history: _reader(key))
    closed = []
    real_exit = chat.ChatSession.__aexit__

    async def aexit(self, *exc):
        closed.append(True)
        await real_exit(self, *exc)

    monkeypatch.setattr(chat.ChatSession, "__aexit__", aexit)
    r = CliRunner().invoke(cli.app, ["chat", "--repo", "toy"])
    assert r.exit_code == 0, r.output
    assert "Stopped" not in r.output and "Error" not in r.output
    assert closed == [True]
