"""User mode: dwb init / index / status / ask inside an arbitrary git repository."""

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pymupdf
import pytest
import yaml
from typer.testing import CliRunner

from dwb.project import (
    ProjectConfig,
    ProjectWorkspace,
    find_project,
    hide_from_git,
    index_project,
    save_global_key,
    suggest_exclude_domains,
    write_config,
)
from dwb.workspace import WorkspaceError
from graph_fakes import components

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "tests" / "fixtures" / "toy_repo"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(path: Path, msg: str = "c") -> str:
    _git(path, "add", ".")
    _git(path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    r = tmp_path / "toyproj"
    shutil.copytree(TOY, r)
    (r / "pyproject.toml").write_text(
        '[project]\nname = "toy"\n[project.urls]\n'
        'Documentation = "https://toy.readthedocs.io/en/latest/"\n'
        'Changelog = "https://opensource.org/licenses/MIT"\n')
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "The application object is a callable.")
    doc.save(r / "spec.pdf")
    _git(r, "init", "-q")
    _git(r, "remote", "add", "origin", "git@github.com:acme/toy.git")
    _commit(r, "init")
    monkeypatch.chdir(r)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return r


def cli():
    import dwb.cli as c

    return c.app


def test_init_yes_writes_config_and_hides_it_from_git(repo):
    r = CliRunner().invoke(cli(), ["init", "--yes"])
    assert r.exit_code == 0, r.output
    cfg_path = repo / ".dwb" / "config.yaml"
    cfg = ProjectConfig.model_validate(yaml.safe_load(cfg_path.read_text()))
    assert cfg.name == "toyproj" and cfg.models.chat and cfg.models.embedding
    assert cfg.sources.pdf is False and cfg.pdfs == []  # --yes adds no PDFs
    assert {"github.com", "toy.readthedocs.io", "pypi.org"} <= set(cfg.web.exclude_domains)
    assert "opensource.org" not in cfg.web.exclude_domains
    assert "/.dwb/" in (repo / ".git" / "info" / "exclude").read_text()
    assert _git(repo, "status", "--porcelain") == ""  # nothing dwb wrote shows up in git
    assert cfg_path.read_text().startswith("# dwb project configuration")
    assert find_project(repo / "src") == repo


def test_init_refuses_to_overwrite_without_force(repo):
    assert CliRunner().invoke(cli(), ["init", "--yes"]).exit_code == 0
    r = CliRunner().invoke(cli(), ["init", "--yes"])
    assert r.exit_code == 2 and "already exists" in r.output or "kept" in r.output
    assert CliRunner().invoke(cli(), ["init", "--yes", "--force"]).exit_code == 0


def test_init_interactive_collects_pdfs_and_web(repo, tty):
    answers = "\n".join([
        "myproj",            # project name
        "chat-x",            # chat model
        "emb-x",             # embedding model
        "missing.pdf",       # a PDF that does not exist -> rejected, asked again
        "spec.pdf",          # a real one
        "Toy spec",          # its title
        "",                  # finish PDFs
        "y",                 # web by default
        "github.com, toy.readthedocs.io",  # excluded domains
    ]) + "\n" + "\n" * 10
    r = CliRunner().invoke(cli(), ["init"], input=answers)
    assert r.exit_code == 0, r.output
    cfg = ProjectConfig.model_validate(yaml.safe_load((repo / ".dwb/config.yaml").read_text()))
    assert cfg.name == "myproj" and cfg.models.chat == "chat-x" and cfg.models.embedding == "emb-x"
    assert [(p.source, p.title) for p in cfg.pdfs] == [("spec.pdf", "Toy spec")]
    assert cfg.sources.web and cfg.sources.pdf
    assert cfg.web.exclude_domains == ["github.com", "toy.readthedocs.io"]
    assert "Not found" in r.output


def test_init_without_a_terminal_needs_yes(repo):
    r = CliRunner().invoke(cli(), ["init"], input="myproj\n" * 20)
    assert r.exit_code == 2, r.output
    assert "--yes" in r.output and "terminal" in r.output
    assert not (repo / ".dwb" / "config.yaml").exists()
    assert CliRunner().invoke(cli(), ["init", "--yes"]).exit_code == 0  # still works


def test_init_shows_numbered_steps_and_ends_with_the_path_and_next(repo, tty):
    from dwb import __version__

    r = CliRunner().invoke(cli(), ["init", "--yes"])
    assert r.exit_code == 0, r.output
    assert r.output.startswith(f"dwb {__version__}\n")
    for n, title in enumerate(["Models", "PDF documents", "Web search", "OpenWiki",
                               "Default sources", "API keys"], 1):
        assert f"Step {n} of 6: {title}" in r.output
    last = r.output.strip().splitlines()[-1]
    assert str(repo / ".dwb" / "config.yaml") in last and "→ next: dwb index" in last


def test_ctrl_c_during_init_says_nothing_was_written(repo, tty, monkeypatch):
    import dwb.init_wizard as wiz

    def stop():
        raise KeyboardInterrupt

    monkeypatch.setattr(wiz, "openwiki_version", stop)
    r = CliRunner().invoke(cli(), ["init", "--yes"])
    assert r.exit_code == 130, r.output
    assert "! Stopped" in r.output and "Nothing was written" in r.output
    assert not (repo / ".dwb" / "config.yaml").exists()


def test_ctrl_c_during_init_states_what_was_written(repo, tty, monkeypatch):
    import dwb.init_wizard as wiz
    import dwb.project as project

    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(project, "GLOBAL_ENV", repo / "no-global.env")  # not the user's file
    monkeypatch.setattr(wiz, "save_global_key", lambda name, value: None)
    monkeypatch.setattr(wiz, "openwiki_version", lambda: None)

    def stop(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(wiz, "write_config", stop)
    key = "sk-proj-SECRETVALUE123xyz"
    answers = "\n".join(["myproj", "", "", "", "n", "", key]) + "\n" + "\n" * 10
    r = CliRunner().invoke(cli(), ["init"], input=answers)
    assert r.exit_code == 130, r.output
    assert "! Stopped" in r.output and f"kept: OPENAI_API_KEY in {wiz.GLOBAL_ENV}" in r.output
    assert "config.yaml was not written" in r.output and "SECRETVALUE" not in r.output


def test_init_outside_git_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli(), ["init", "--yes"])
    assert r.exit_code == 2 and "not inside a git repository" in r.output


def test_ask_before_index_says_run_dwb_index(repo):
    CliRunner().invoke(cli(), ["init", "--yes"])
    r = CliRunner().invoke(cli(), ["ask", "How does the app run?"])
    assert r.exit_code == 2 and "dwb index" in r.output


def test_ask_outside_a_project_says_run_dwb_init(tmp_path, monkeypatch):
    d = tmp_path / "plain"
    d.mkdir()
    _git(d, "init", "-q")
    monkeypatch.chdir(d)
    r = CliRunner().invoke(cli(), ["ask", "q?"])
    assert r.exit_code == 2 and "dwb init" in r.output


def _project(repo, **over) -> ProjectWorkspace:
    over.setdefault("pdfs", [{"source": "spec.pdf", "title": "Toy spec"}])
    cfg = ProjectConfig(name="toy", models={"chat": "gen-x", "embedding": "fake-64"}, **over)
    write_config(repo, cfg)
    return ProjectWorkspace(repo)


def test_index_builds_code_and_pdfs_for_head(repo):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    ws = _project(repo, wiki={"enabled": True})
    state = index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    assert state["sha"] == _git(repo, "rev-parse", "HEAD")
    assert state["code_index_id"].startswith("code-toy-")
    assert state["pdf_index_id"].startswith("pdf-toy-pdfs-")
    assert "wiki_snapshot_id" not in state and "openwiki/ missing" in state["wiki_note"]
    assert json.loads((repo / ".dwb/state.json").read_text()) == state
    rows = {s: (st, d) for s, st, d in ws.status_rows()}
    assert rows["code"][0] == "ok" and rows["pdf"][0] == "ok" and rows["wiki"][0] == "missing"
    # the indexes are found by the same discovery chat uses
    assert ws.find_code_index("toy", state["sha"], "fake-64") == state["code_index_id"]
    assert ws.find_pdf_index(ws.pdf_corpus_id("toy"), "fake-64") == state["pdf_index_id"]


def test_wiki_in_place_is_registered_when_it_matches_head(repo):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    sha = _git(repo, "rev-parse", "HEAD")
    shutil.copytree(ROOT / "tests/fixtures/openwiki/openwiki", repo / "openwiki")
    last = json.loads((repo / "openwiki/.last-update.json").read_text())
    (repo / "openwiki/.last-update.json").write_text(json.dumps({**last, "gitHead": sha}))
    ws = _project(repo, wiki={"enabled": True})
    state = index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    assert state["wiki_snapshot_id"].startswith("wiki-toy-")
    assert ws.find_wiki_snapshot("toy", sha)["claims"] == 10
    assert ws.restores_wiki is False  # used in place, never overwritten


def test_project_ask_answers_and_warns_when_head_moves(repo, monkeypatch, caplog):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    import dwb.runner as runner

    ws = _project(repo)
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))
    r = CliRunner().invoke(cli(), ["ask", "How does the app run?"])
    assert r.exit_code == 0, r.output
    assert "Answer" in r.output and "Sources" in r.output
    (repo / "new.py").write_text("x = 1\n")
    _commit(repo, "move head")
    r = CliRunner().invoke(cli(), ["ask", "How does the app run?"])
    assert r.exit_code == 0 and "dwb index" in r.output  # stale-index warning
    r = CliRunner().invoke(cli(), ["status"])
    assert r.exit_code == 0 and "stale" in r.output


def test_cli_index_runs_from_a_subfolder(repo, monkeypatch):
    CliRunner().invoke(cli(), ["init", "--yes"])
    monkeypatch.chdir(repo / "src")
    r = CliRunner().invoke(cli(), ["index", "--fake"])
    assert r.exit_code == 0, r.output
    state = json.loads((repo / ".dwb/state.json").read_text())
    assert r.stdout == json.dumps(state, indent=2) + "\n"  # piped: the state, as JSON


def test_cli_index_at_a_terminal_prints_header_summary_and_outcome(repo, tty):
    CliRunner().invoke(cli(), ["init", "--yes"])
    sha = _git(repo, "rev-parse", "HEAD")[:12]
    r = CliRunner().invoke(cli(), ["index", "--fake"])
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith(f"toyproj · user mode · {sha}\n")
    assert "{" not in r.stdout and re.search(r"^code\s+code-toyproj-", r.stdout, re.M)
    assert re.search(r"^pdf\s+off$", r.stdout, re.M)
    assert f"✓ Indexed toyproj at {sha} → next: dwb chat" in r.stderr
    r = CliRunner().invoke(cli(), ["--json", "index", "--fake"])
    assert r.exit_code == 0 and json.loads(r.stdout)["sha"].startswith(sha)
    assert "user mode" not in r.stderr and "Indexed toyproj" not in r.stderr


def test_cli_status_in_a_project_piped_and_at_a_terminal(repo, monkeypatch):
    from dwb import ui

    _project(repo)
    r = CliRunner().invoke(cli(), ["status"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["name"] == "toy" and data["root"] == str(repo)
    assert [s["source"] for s in data["sources"]] == ["commit", "code", "pdf", "wiki", "web"]
    assert data["sources"][1] == {"source": "code", "state": "missing",
                                  "detail": "run dwb index"}
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    ui.force_terminal(True)
    try:
        r = CliRunner().invoke(cli(), ["status"])
    finally:
        ui.force_terminal(None)
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith("toy · user mode · ")
    assert re.search(r"^Source\s+State\s+Detail$", r.stdout, re.M)
    assert re.search(r"^code\s+missing\s+run dwb index$", r.stdout, re.M)
    assert "→ next: dwb index" in r.stderr


def test_ask_with_no_question_exits_2_without_a_terminal(repo):
    r = CliRunner().invoke(cli(), ["ask"])
    assert r.exit_code == 2 and 'Usage: dwb ask "<question>"' in r.output


def test_ask_at_a_terminal_prints_the_header_then_the_answer(repo, monkeypatch, tty):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    import dwb.runner as runner

    ws = _project(repo)
    state = index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))
    r = CliRunner().invoke(cli(), ["ask", "How does the app run?"])
    assert r.exit_code == 0, r.output
    head = f"toy · user mode · {state['sha'][:12]}"
    assert r.stderr.startswith(head + "\n")
    assert "Answer" in r.stdout and "Sources" in r.stdout
    assert r.output.index(head) < r.output.index("Answer")
    # with no question at a terminal, it asks for one
    r = CliRunner().invoke(cli(), ["ask"], input="How does the app run?\n")
    assert r.exit_code == 0, r.output
    assert "Question:" in r.stderr and "Answer" in r.stdout


def test_ask_stays_human_when_piped_and_json_prints_the_trace(repo, monkeypatch):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    import dwb.runner as runner
    from dwb.trace import TraceRecord

    ws = _project(repo)
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    monkeypatch.setattr(runner, "build_components", lambda m, **kw: components("A"))
    r = CliRunner().invoke(cli(), ["ask", "How does the app run?"])
    assert r.exit_code == 0 and "Answer" in r.stdout and "toy · user mode" in r.stderr
    for flags in (["ask", "q?", "--json"], ["--json", "ask", "q?"]):
        r = CliRunner().invoke(cli(), flags)
        assert r.exit_code == 0, r.output
        t = TraceRecord.model_validate_json(r.stdout)
        assert r.stdout == t.model_dump_json(indent=2) + "\n" and "user mode" not in r.stderr


def test_index_outside_project_points_to_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli(), ["index"])
    assert r.exit_code == 2 and "dwb init" in r.output


def test_hide_from_git_is_idempotent(repo):
    hide_from_git(repo)
    hide_from_git(repo)
    assert (repo / ".git/info/exclude").read_text().count("/.dwb/") == 1


def test_suggest_exclude_domains(repo):
    assert suggest_exclude_domains(repo)[0] == "github.com"


def test_save_global_key_is_private(tmp_path, monkeypatch):
    p = tmp_path / "dwb" / ".env"
    monkeypatch.delenv("DWB_TEST_KEY", raising=False)
    save_global_key("DWB_TEST_KEY", "one", p)
    save_global_key("DWB_TEST_KEY", "two", p)
    assert p.read_text() == "DWB_TEST_KEY=two\n"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert os.environ["DWB_TEST_KEY"] == "two"


def test_research_mode_still_needs_repo_flag_outside_projects(monkeypatch):
    monkeypatch.chdir(ROOT)  # design-wiki-bridge: a git repo without .dwb/
    r = CliRunner().invoke(cli(), ["ask", "q?"])
    assert r.exit_code == 2 and "--repo" in r.output
    with pytest.raises(WorkspaceError):
        ProjectWorkspace(ROOT)


# ------------------------------------------------------------------ review fixes


def test_project_env_only_supplies_api_keys(tmp_path, monkeypatch):
    from dwb.project import load_env

    proj = tmp_path / "cloned"
    proj.mkdir()
    (proj / ".env").write_text("OPENAI_API_KEY=from-repo\nOPENAI_BASE_URL=https://evil.example\n"
                               "HTTPS_PROXY=http://evil.example:8080\n")
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "HTTPS_PROXY"):
        monkeypatch.delenv(k, raising=False)
    loaded = load_env(cwd=proj, global_env=tmp_path / "none.env")
    assert loaded == [proj / ".env"]
    assert os.environ["OPENAI_API_KEY"] == "from-repo"
    assert "OPENAI_BASE_URL" not in os.environ and "HTTPS_PROXY" not in os.environ


def test_harness_env_and_global_env_load_in_full(tmp_path, monkeypatch):
    from dwb.project import load_env

    harness = tmp_path / "harness"
    (harness / "configs").mkdir(parents=True)
    (harness / "configs/repos.yaml").write_text("repos: {}\n")
    (harness / ".env").write_text("DWB_T_LOCAL=1\n")
    glob = tmp_path / "global.env"
    glob.write_text("DWB_T_GLOBAL=2\n")
    for k in ("DWB_T_LOCAL", "DWB_T_GLOBAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "shell")
    (harness / ".env").write_text("DWB_T_LOCAL=1\nOPENAI_API_KEY=file\n")
    load_env(cwd=harness, global_env=glob)
    assert os.environ["DWB_T_LOCAL"] == "1" and os.environ["DWB_T_GLOBAL"] == "2"
    assert os.environ["OPENAI_API_KEY"] == "shell"  # the shell always wins


def test_saved_key_is_never_world_readable(tmp_path, monkeypatch):
    p = tmp_path / "dwb" / ".env"
    monkeypatch.delenv("DWB_TEST_KEY", raising=False)
    old = os.umask(0o022)
    try:
        save_global_key("DWB_TEST_KEY", "one", p)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert not list(p.parent.glob("*.tmp"))


def test_pdf_names_include_a_url_hash():
    from dwb.project import pdf_filename

    a = pdf_filename("https://a.example.org/spec.pdf")
    b = pdf_filename("https://b.example.org/spec.pdf?v=2")
    assert a != b and a.startswith("spec-") and a.endswith(".pdf")
    assert pdf_filename("https://a.example.org/spec.pdf") == a


def test_pdf_download_errors_are_workspace_errors(tmp_path, monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from dwb.project import _fetch_pdf

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp(b"<html>login"))
    with pytest.raises(WorkspaceError, match="did not return a PDF"):
        _fetch_pdf("https://x.example.org/a.pdf", tmp_path)
    assert not list(tmp_path.iterdir())  # nothing cached

    def offline(req, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(WorkspaceError, match="could not download"):
        _fetch_pdf("https://x.example.org/a.pdf", tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp(b"%PDF-1.7 ..."))
    p = _fetch_pdf("https://x.example.org/a.pdf", tmp_path)
    assert p.read_bytes().startswith(b"%PDF") and not list(tmp_path.glob("*.part"))


def test_wiki_generation_failures(repo, monkeypatch):
    import dwb.project as project
    from dwb.project import WikiCfg, _generate_wiki

    monkeypatch.setattr(project.shutil, "which", lambda name: None)
    with pytest.raises(WorkspaceError, match="not installed"):
        _generate_wiki(repo, WikiCfg(), update=False)
    monkeypatch.setattr(project.shutil, "which", lambda name: "/bin/openwiki")
    monkeypatch.setattr(project.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1))
    with pytest.raises(WorkspaceError, match="exited with status 1"):
        _generate_wiki(repo, WikiCfg(), update=True)


def test_malformed_config_is_a_clear_error(repo):
    (repo / ".dwb").mkdir(exist_ok=True)
    (repo / ".dwb/config.yaml").write_text("name: [unclosed\n")
    with pytest.raises(WorkspaceError, match="is not valid"):
        ProjectWorkspace(repo)


def test_chat_uses_the_indexed_ids_not_the_newest_index(repo):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    ws = _project(repo)
    state = index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    sha = state["sha"]
    newer = ws.index_root / "code-toy-newer"
    newer.mkdir(parents=True)
    (newer / "index.json").write_text(json.dumps({
        "index_id": "code-toy-newer", "kind": "code", "embedding_model": "fake-64",
        "n_items": 1, "parts": {"repo": "toy", "sha": sha}, "created_at": "2999-01-01"}))
    assert ws.find_code_index("toy", sha, "fake-64") == state["code_index_id"]
    assert ws.find_code_index("toy", sha, "other-model") is None
    assert ws.find_code_index("toy", "0" * 40, "fake-64") is None


def test_removing_pdfs_drops_the_old_corpus(repo):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    ws = _project(repo)
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    assert ws.pdf_corpus_id("toy")
    cfg = yaml.safe_load((repo / ".dwb/config.yaml").read_text())
    cfg["pdfs"] = []
    (repo / ".dwb/config.yaml").write_text(yaml.safe_dump(cfg))
    ws = ProjectWorkspace(repo)
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    assert not (repo / ".dwb/pdf_corpus.yaml").exists()
    assert ws.pdf_corpus_id("toy") is None


def test_wiki_edited_after_index_is_refused(repo):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    sha = _git(repo, "rev-parse", "HEAD")
    shutil.copytree(ROOT / "tests/fixtures/openwiki/openwiki", repo / "openwiki")
    last = json.loads((repo / "openwiki/.last-update.json").read_text())
    (repo / "openwiki/.last-update.json").write_text(json.dumps({**last, "gitHead": sha}))
    ws = _project(repo, wiki={"enabled": True})
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    assert ws.find_wiki_snapshot("toy", sha)
    page = next((repo / "openwiki").rglob("*.md"))
    page.write_text(page.read_text() + "\nregenerated text\n")
    with pytest.raises(WorkspaceError, match="changed since dwb index"):
        ws.find_wiki_snapshot("toy", sha)


def test_user_mode_and_registry_count_wiki_pages_the_same(repo, tmp_path):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from dwb.retrieval.openwiki_client import register_snapshot

    sha = _git(repo, "rev-parse", "HEAD")
    shutil.copytree(ROOT / "tests/fixtures/openwiki/openwiki", repo / "openwiki")
    last = json.loads((repo / "openwiki/.last-update.json").read_text())
    (repo / "openwiki/.last-update.json").write_text(json.dumps({**last, "gitHead": sha}))
    ws = _project(repo, wiki={"enabled": True})
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    user = ws.find_wiki_snapshot("toy", sha)
    reg = register_snapshot(repo, "toy", sha, tmp_path / "wikis", provider="openai",
                            model=last.get("model") or "m", version="0.9.0")
    assert (user["pages"], user["claims"], user["tree_sha256"]) == (
        reg.pages, reg.claims, reg.tree_sha256)


def test_custom_span_size_is_carried_into_chat(repo, monkeypatch):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from dwb.chat import ChatSettings, build_manifest

    cfg = ProjectConfig(name="toy", models={"chat": "gen-x", "embedding": "fake-64"},
                        code={"max_span_tokens": 900})
    write_config(repo, cfg)
    ws = ProjectWorkspace(repo)
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32))
    m, _ = build_manifest(ws, ChatSettings(repo="toy"))
    assert m.budgets.max_span_tokens == 900


# ------------------------------------------------- PDF folders, duplicates, masked API key


def _pdf(path: Path, text: str) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _index(ws):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    return index_project(ws, embedding=DeterministicFakeEmbedding(size=32))


def _corpus_docs(repo) -> list[dict]:
    return yaml.safe_load((repo / ".dwb/pdf_corpus.yaml").read_text())["documents"]


def test_the_same_pdf_listed_twice_is_a_clear_error(repo):
    ws = _project(repo, pdfs=[{"source": "spec.pdf"}, {"source": "spec.pdf"}])
    with pytest.raises(WorkspaceError, match="spec.pdf.*more than once"):
        _index(ws)
    assert not (repo / ".dwb/state.json").exists()  # refused before anything was indexed


def test_identical_pdfs_under_two_names_are_a_clear_error(repo):
    shutil.copy(repo / "spec.pdf", repo / "copy.pdf")
    ws = _project(repo, pdfs=[{"source": "spec.pdf"}, {"source": "copy.pdf"}])
    with pytest.raises(WorkspaceError, match="copy.pdf.*same file as spec.pdf"):
        _index(ws)


def test_a_folder_entry_indexes_every_pdf_inside_it(repo):
    _pdf(repo / "docs/specs/alpha.pdf", "Alpha protocol.")
    _pdf(repo / "docs/specs/sub/Beta.PDF", "Beta framing.")
    (repo / "docs/specs/notes.txt").write_text("not a pdf")
    ws = _project(repo, pdfs=[{"source": "docs/specs"}])
    state = _index(ws)
    docs = _corpus_docs(repo)
    assert [d["doc_id"] for d in docs] == ["alpha", "beta"]
    assert state["pdf_index_id"].startswith("pdf-toy-pdfs-")


def test_folders_and_files_overlapping_are_indexed_once(repo):
    _pdf(repo / "docs/a/spec.pdf", "Spec A.")
    _pdf(repo / "docs/b/spec.pdf", "Spec B, a different file with the same name.")
    ws = _project(repo, pdfs=[{"source": "docs"}, {"source": "docs/a/spec.pdf",
                                                   "title": "Spec A"}])
    _index(ws)
    docs = _corpus_docs(repo)
    assert [d["doc_id"] for d in docs] == ["spec-a", "spec"]  # unique ids, file kept once
    assert docs[0]["title"] == "Spec A"  # the explicit entry's title wins


def test_a_folder_without_pdfs_is_a_clear_error(repo):
    (repo / "empty").mkdir()
    ws = _project(repo, pdfs=[{"source": "empty"}])
    with pytest.raises(WorkspaceError, match="no PDF files in empty"):
        _index(ws)


def test_init_accepts_folders_and_rejects_repeated_pdfs(repo, tty):
    _pdf(repo / "docs/specs/alpha.pdf", "Alpha.")
    _pdf(repo / "docs/specs/beta.pdf", "Beta.")
    answers = "\n".join([
        "myproj", "chat-x", "emb-x",
        "spec.pdf", "Toy spec",   # a file, with its title
        "spec.pdf",               # the same file again -> rejected
        "docs/specs",             # a folder -> no title asked
        "./docs/specs/",          # the same folder again -> rejected
        "",                       # finish PDFs
        "n", "",                  # no web, default excluded domains
    ]) + "\n" + "\n" * 10
    r = CliRunner().invoke(cli(), ["init"], input=answers)
    assert r.exit_code == 0, r.output
    cfg = ProjectConfig.model_validate(yaml.safe_load((repo / ".dwb/config.yaml").read_text()))
    assert [(p.source, p.title) for p in cfg.pdfs] == [("spec.pdf", "Toy spec"),
                                                       ("docs/specs", "")]
    assert r.output.count("Already added") == 2 and "2 PDF files" in r.output


def test_mask_secret_shows_only_the_ends():
    from dwb.init_wizard import mask_secret

    assert mask_secret("sk-proj-abcdefghijklmnop") == "sk-*****nop"
    assert mask_secret("short") == "*****"


def test_init_echoes_a_masked_key_and_never_the_key(repo, tty, monkeypatch):
    import dwb.init_wizard as wiz
    import dwb.project as project

    saved = {}
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(project, "GLOBAL_ENV", repo / "no-global.env")  # not the user's file
    monkeypatch.setattr(wiz, "save_global_key", lambda name, value: saved.update({name: value}))
    monkeypatch.setattr(wiz, "openwiki_version", lambda: None)  # no wiki questions
    key = "sk-proj-SECRETVALUE123xyz"
    answers = "\n".join(["myproj", "", "", "", "n", "", key]) + "\n" + "\n" * 10
    r = CliRunner().invoke(cli(), ["init"], input=answers)
    assert r.exit_code == 0, r.output
    assert saved == {"OPENAI_API_KEY": key}
    assert "sk-*****xyz" in r.output and "SECRETVALUE" not in r.output


# ------------------------------------------------------------------------------ progress


def test_index_reports_embedding_progress_and_keeps_going_without_a_callback(repo, monkeypatch):
    import dwb.retrieval.vector_store as vs

    monkeypatch.setattr(vs, "BATCH", 2)
    ws = _project(repo)
    events = []
    from langchain_core.embeddings import DeterministicFakeEmbedding

    state = index_project(ws, embedding=DeterministicFakeEmbedding(size=32),
                          on_progress=events.append)
    code = [e for e in events if e.label == state["code_index_id"]]
    assert code[0].phase == "start" and code[0].data == {"kind": "code"}
    steps = [e.done for e in code if e.phase == "step"]
    assert steps == sorted(steps) and len(steps) > 1  # one step per batch
    assert code[-1].phase == "done" and code[-1].done == steps[-1] == code[-1].data.n_items
    pdf = [e for e in events if e.label == state["pdf_index_id"]]
    assert [e.phase for e in pdf] == ["start", "step", "done"] and pdf[-1].done == 1
    # a reused index reports one done event carrying the reused info
    again = []
    index_project(ws, embedding=DeterministicFakeEmbedding(size=32), on_progress=again.append)
    assert [(e.phase, e.data.reused) for e in again] == [("done", True), ("done", True)]


def _fake_openwiki(tmp_path, monkeypatch, body: str) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "openwiki"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return exe


def test_openwiki_output_goes_to_the_log_not_the_terminal(repo, tmp_path, monkeypatch, capfd):
    from dwb.project import WikiCfg, _generate_wiki

    _fake_openwiki(tmp_path, monkeypatch,
                   'echo "openwiki says hello $1 $2"\necho "openwiki warns" >&2\nexit 0\n')
    events = []
    _generate_wiki(repo, WikiCfg(), update=False, on_progress=events.append)
    out, err = capfd.readouterr()
    assert "openwiki says" not in out + err and "openwiki warns" not in out + err
    log = (repo / ".dwb/openwiki.log").read_text()
    assert "openwiki says hello code --init" in log and "openwiki warns" in log
    assert [(e.task, e.phase) for e in events] == [("wiki", "start"), ("wiki", "done")]
    assert events[0].data["log"] == repo / ".dwb/openwiki.log"


def test_openwiki_failure_shows_the_last_lines_and_the_log_path(repo, tmp_path, monkeypatch):
    from dwb.project import WikiCfg, _generate_wiki

    _fake_openwiki(tmp_path, monkeypatch,
                   'i=1\nwhile [ $i -le 30 ]; do echo "step $i"; i=$((i+1)); done\n'
                   'echo "boom: no API key" >&2\nexit 3\n')
    with pytest.raises(WorkspaceError) as exc:
        _generate_wiki(repo, WikiCfg(), update=True)
    msg = str(exc.value)
    assert "OpenWiki exited with status 3" in msg and "boom: no API key" in msg
    assert "step 30" in msg and "step 12" in msg and "step 11\n" not in msg  # last 20 lines
    assert str(repo / ".dwb/openwiki.log") in msg


def test_openwiki_output_is_not_captured_with_verbose(repo, tmp_path, monkeypatch, capfd):
    from dwb.project import WikiCfg, _generate_wiki

    _fake_openwiki(tmp_path, monkeypatch, 'echo "openwiki streams"\nexit 0\n')
    events = []
    _generate_wiki(repo, WikiCfg(), update=False, inherit_output=True,
                   on_progress=events.append)
    out, _ = capfd.readouterr()
    assert "openwiki streams" in out
    assert not (repo / ".dwb/openwiki.log").exists() and events[0].data["log"] is None


def test_cli_index_progress_without_a_terminal_is_plain_milestones(repo):
    _project(repo)
    r = CliRunner().invoke(cli(), ["index", "--fake"])
    assert r.exit_code == 0, r.output
    state = json.loads(r.stdout)
    assert "Ctrl-C stops; finished indexes are kept." in r.stderr
    assert re.search(r"^Embedding \d+ spans into code-toy-", r.stderr, re.M)
    assert re.search(rf"^  ✓ Code   \d+ spans  {state['code_index_id']}  \d+\.\d s$",
                     r.stderr, re.M)
    assert re.search(r"^  ✓ PDFs   1 pages  pdf-toy-pdfs-", r.stderr, re.M)
    assert "\r" not in r.stderr and "\x1b[" not in r.stderr
    assert "  embedded " not in r.stderr  # one small batch: no 10% lines


def test_cli_index_generates_the_wiki_with_a_spinner_line(repo, tmp_path, monkeypatch, tty):
    _fake_openwiki(tmp_path, monkeypatch, 'echo "noisy openwiki output"\nexit 0\n')
    _project(repo, pdfs=[], wiki={"enabled": True, "generate": True})
    r = CliRunner().invoke(cli(), ["index", "--fake"])
    assert r.exit_code == 0, r.output
    assert "noisy openwiki output" not in r.output
    assert "Running OpenWiki code --init (openai/" in r.stderr
    assert "Output: " in r.stderr and ".dwb/openwiki.log" in r.stderr
    assert "✓ Wiki   generated by OpenWiki" in r.stderr


def test_cli_index_interrupted_after_the_code_index_reports_it_kept(repo, monkeypatch):
    import dwb.retrieval.pdf_index as pdf_index

    def stop(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(pdf_index, "build_pdf_index", stop)
    _project(repo)
    r = CliRunner().invoke(cli(), ["index", "--fake"])
    assert r.exit_code == 130, r.output
    lines = r.stderr.splitlines()
    assert "! Stopped" in lines and "Traceback" not in r.output
    kept = [ln for ln in lines if ln.startswith("  • kept: ")]
    assert len(kept) == 1 and kept[0].startswith("  • kept: code index code-toy-")
    assert not (repo / ".dwb/state.json").exists()
