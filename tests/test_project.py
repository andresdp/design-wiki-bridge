"""User mode: dwb init / index / status / ask inside an arbitrary git repository."""

import json
import os
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


def test_init_interactive_collects_pdfs_and_web(repo):
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
    cfg = ProjectConfig(name="toy", models={"chat": "gen-x", "embedding": "fake-64"},
                        pdfs=[{"source": "spec.pdf", "title": "Toy spec"}], **over)
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
    assert "Indexed" in r.output and (repo / ".dwb/state.json").exists()


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
