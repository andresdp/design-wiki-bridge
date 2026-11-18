"""The presentation layer (dwb.ui) and the app-level exception boundary in dwb.cli."""

import importlib
import io
import json
import os
import re
from dataclasses import dataclass

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

import dwb
import dwb.cli as cli
import dwb.log
import dwb.manifest
import dwb.workspace
from dwb import ui

ANSI = "\x1b["


@pytest.fixture(autouse=True)
def _private_cache(tmp_path_factory, monkeypatch):
    """Unexpected-error tracebacks go to $XDG_CACHE_HOME/dwb; keep them out of the real cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))


@pytest.fixture(autouse=True)
def _reset_ui():
    ui.configure()
    ui.force_terminal(None)
    yield
    ui.configure()
    ui.force_terminal(None)


def _console(encoding: str) -> tuple[Console, io.BytesIO]:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding, write_through=True)
    return Console(file=stream, width=100), raw


@pytest.fixture
def pair(tmp_path):
    """Two manifest paths for `experiment check`; the test decides what loading them does."""
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("{}\n")
    b.write_text("{}\n")
    return [str(a), str(b)]


def _check(monkeypatch, pair, effect, *flags, env=None):
    def load(path):
        return effect()

    monkeypatch.setattr(dwb.manifest, "load_manifest", load)
    return CliRunner(env=env).invoke(cli.app, [*flags, "experiment", "check", *pair])


def _raiser(exc):
    def effect():
        raise exc
    return effect


# ------------------------------------------------------------------------------ glyphs


def test_every_glyph_falls_back_to_ascii_on_a_non_utf_console():
    con, raw = _console("ascii")
    g = ui.glyphs(con)
    assert (g.ok, g.fail, g.warn, g.bullet, g.arrow) == ("ok", "x", "!", "-", "->")
    assert g.spinner == ("|", "/", "-", "\\")
    assert (g.bar_done, g.bar_todo, g.sep, g.ellipsis) == ("#", "-", "-", "...")
    for s in (g.ok, g.fail, g.warn, g.bullet, g.arrow, g.sep, g.ellipsis, g.bar_done,
              g.bar_todo, *g.spinner):
        s.encode("ascii")
    ui.error("it broke", "do this", console=con)
    ui.warning("careful", console=con)
    ui.outcome("Indexed toy", next_cmd="dwb chat", console=con)
    ui.header("toy", "user mode", "abc123", console=con)
    text = raw.getvalue().decode("ascii")
    assert "x it broke\n-> do this\n" in text and "! careful" in text
    assert "ok Indexed toy -> next: dwb chat" in text and "toy - user mode - abc123" in text


def test_utf8_console_uses_the_unicode_glyphs():
    con, raw = _console("utf-8")
    g = ui.glyphs(con)
    assert (g.ok, g.fail, g.warn, g.bullet, g.arrow, g.sep, g.ellipsis) == \
        ("✓", "✗", "!", "•", "→", "·", "…")
    ui.error("it broke", "do this", console=con)
    assert raw.getvalue().decode() == "✗ it broke\n→ do this\n"


def test_error_without_a_fix_prints_one_line_and_escapes_markup():
    con, raw = _console("utf-8")
    ui.error("bad value [type=missing]", console=con)
    assert raw.getvalue().decode() == "✗ bad value [type=missing]\n"


# ----------------------------------------------------------------------- color / terminal


def test_no_ansi_without_a_terminal(monkeypatch, pair):
    r = _check(monkeypatch, pair, _raiser(dwb.workspace.WorkspaceError("nope")))
    assert r.exit_code == 2 and ANSI not in r.output


def test_forced_terminal_has_color_and_no_color_removes_it(monkeypatch, pair):
    ui.force_terminal(True)
    boom = _raiser(dwb.workspace.WorkspaceError("nope"))
    r = _check(monkeypatch, pair, boom, env={"NO_COLOR": None})
    assert r.exit_code == 2 and ANSI in r.output  # the check below can see color
    r = _check(monkeypatch, pair, boom, env={"NO_COLOR": "1"})
    assert r.exit_code == 2 and ANSI not in r.output and "✗ nope" in r.output
    r = _check(monkeypatch, pair, boom, "--no-color", env={"NO_COLOR": None})
    assert r.exit_code == 2 and ANSI not in r.output and "✗ nope" in r.output


def test_consoles_follow_the_current_streams(monkeypatch):
    a, b = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", a)
    ui.out().print("one")
    monkeypatch.setattr("sys.stdout", b)
    ui.out().print("two")
    assert a.getvalue() == "one\n" and b.getvalue() == "two\n"


def test_animations_only_on_a_colored_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert not ui.animations()
    ui.force_terminal(True)
    assert ui.animations()
    ui.configure(no_color=True)
    assert not ui.animations()


# ------------------------------------------------------------------------------- emit


@dataclass
class _Info:
    name: str
    n: int


def test_emit_prints_json_when_stdout_is_not_a_terminal(capsys):
    called = []
    ui.emit({"a": 1, "b": [1, 2]}, human=lambda r: called.append(r))
    assert capsys.readouterr().out == json.dumps({"a": 1, "b": [1, 2]}, indent=2) + "\n"
    assert not called
    ui.emit(_Info("x", 3))
    assert json.loads(capsys.readouterr().out) == {"name": "x", "n": 3}
    ui.emit(dwb.manifest.PairedCheck(ok=True))
    assert capsys.readouterr().out == dwb.manifest.PairedCheck(ok=True).model_dump_json(
        indent=2) + "\n"


def test_emit_calls_the_human_renderer_at_a_terminal_unless_json_is_set(capsys):
    ui.force_terminal(True)
    called = []
    ui.emit({"a": 1}, human=lambda r: called.append(r))
    assert called == [{"a": 1}] and capsys.readouterr().out == ""
    assert not ui.json_mode()
    ui.configure(json=True)
    assert ui.json_mode()
    ui.emit({"a": 1}, human=lambda r: called.append(r))
    assert called == [{"a": 1}] and json.loads(capsys.readouterr().out) == {"a": 1}


def test_global_json_flag_is_parsed(monkeypatch, pair):
    seen = []

    def effect():
        seen.append(ui.json_mode())
        raise typer.Exit(0)

    ui.force_terminal(True)
    assert _check(monkeypatch, pair, effect, "--json").exit_code == 0
    assert _check(monkeypatch, pair, effect).exit_code == 0
    assert seen == [True, False]


# ----------------------------------------------------------------------------- errors


EXPECTED = ["dwb.workspace.WorkspaceError", "dwb.retrieval.openwiki_client.OpenWikiError",
            "dwb.runner.QuestionSetError", "dwb.runner.IndexMismatchError",
            "dwb.manifest.ManifestError", "dwb.evidence_text.EvidenceTextError",
            "dwb.eval.command.UnpairedRunsError"]


def _cls(dotted: str):
    mod, name = dotted.rsplit(".", 1)
    return getattr(importlib.import_module(mod), name)


def test_the_boundary_knows_exactly_the_expected_error_types():
    assert set(cli.EXPECTED_ERRORS) == set(EXPECTED)
    for dotted in EXPECTED:
        assert issubclass(_cls(dotted), Exception)


@pytest.mark.parametrize("dotted", EXPECTED)
def test_each_expected_error_prints_the_short_form_and_exits_2(monkeypatch, pair, dotted):
    r = _check(monkeypatch, pair, _raiser(_cls(dotted)(f"boom {dotted}")))
    assert r.exit_code == 2, r.output
    assert f"✗ boom {dotted}" in r.output
    assert "Unexpected error" not in r.output and "Traceback" not in r.output


def test_workspace_error_with_a_fix_prints_two_lines(monkeypatch, pair):
    e = dwb.workspace.WorkspaceError("PDF a.pdf is listed more than once.")
    e.fix = "Remove the duplicate from pdfs: in .dwb/config.yaml, then run dwb index."
    r = _check(monkeypatch, pair, _raiser(e))
    assert r.exit_code == 2
    assert ("✗ PDF a.pdf is listed more than once.\n"
            "→ Remove the duplicate from pdfs: in .dwb/config.yaml, then run dwb index.\n"
            ) in r.output


def test_real_workspace_error_outside_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli.app, ["ask", "q?"])
    assert r.exit_code == 2 and r.output.startswith("✗ this folder is not a dwb project")


def test_malformed_manifest_is_an_expected_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("run_id: x\n")
    r = CliRunner().invoke(cli.app, ["experiment", "check", str(bad), str(bad)])
    assert r.exit_code == 2, r.output
    assert "✗ invalid manifest" in r.output and "Unexpected error" not in r.output


def _last_error():
    return os.path.join(os.environ["XDG_CACHE_HOME"], "dwb", "last-error.log")


def test_unexpected_error_prints_a_summary_and_writes_the_traceback(monkeypatch, pair):
    r = _check(monkeypatch, pair, _raiser(ValueError("kaboom")))
    assert r.exit_code == 1
    assert "✗ Unexpected error: ValueError: kaboom" in r.output
    assert f"→ details: {_last_error()}; rerun with -v for the traceback" in r.output
    assert "Traceback" not in r.output
    log = open(_last_error()).read()
    assert "Traceback" in log and "ValueError: kaboom" in log


def test_unexpected_error_with_verbose_prints_the_traceback(monkeypatch, pair):
    r = _check(monkeypatch, pair, _raiser(ValueError("kaboom")), "-v")
    assert r.exit_code == 1
    assert "✗ Unexpected error: ValueError: kaboom" in r.output and "Traceback" in r.output


def test_typer_exit_passes_through(monkeypatch, pair):
    r = _check(monkeypatch, pair, _raiser(typer.Exit(1)))
    assert r.exit_code == 1 and "Unexpected error" not in r.output and "✗" not in r.output
    r = _check(monkeypatch, pair, _raiser(typer.Exit(0)))
    assert r.exit_code == 0 and r.output == ""


def test_usage_errors_keep_clicks_own_handling():
    r = CliRunner().invoke(cli.app, ["experiment", "check", "--nope"])
    assert r.exit_code == 2 and "Unexpected error" not in r.output


def test_ctrl_c_stops_with_130_and_no_traceback(monkeypatch, pair):
    r = _check(monkeypatch, pair, _raiser(KeyboardInterrupt()))
    assert r.exit_code == 130
    assert r.output.startswith("! Stopped") and "Traceback" not in r.output


def test_ctrl_c_reports_what_was_kept(monkeypatch, pair):
    def effect():
        ui.keep("code index code-toy-1")
        ui.keep("trace q1")
        ui.note_on_stop("The run is incomplete.")
        raise KeyboardInterrupt

    r = _check(monkeypatch, pair, effect)
    assert r.exit_code == 130
    lines = r.output.splitlines()
    assert lines[0] == "! Stopped"
    assert "  • kept: code index code-toy-1" in lines and "  • kept: trace q1" in lines
    assert "The run is incomplete." in lines
    # the record belongs to that invocation only
    r = _check(monkeypatch, pair, _raiser(KeyboardInterrupt()))
    assert r.output == "! Stopped\n"


# ---------------------------------------------------------------------- logging / version

TIME = re.compile(r"\d\d:\d\d:\d\d")


def _logs(monkeypatch, pair, *flags):
    def effect():
        log = dwb.log.get_logger("test")
        log.info("hello-info")
        log.debug("hello-debug")
        log.warning("hello-warning")
        raise typer.Exit(0)

    return _check(monkeypatch, pair, effect, *flags, env={"COLUMNS": "200"}).output


def test_default_logs_have_no_time_or_level(monkeypatch, pair):
    out = _logs(monkeypatch, pair)
    assert "hello-info" in out and "hello-debug" not in out and "! hello-warning" in out
    assert "INFO" not in out and "WARNING" not in out and not TIME.search(out)


def test_verbose_logs_have_time_and_level(monkeypatch, pair):
    out = _logs(monkeypatch, pair, "-v")
    assert "hello-debug" in out and "INFO" in out and "DEBUG" in out and TIME.search(out)


def test_quiet_logs_show_warnings_only(monkeypatch, pair):
    out = _logs(monkeypatch, pair, "-q")
    assert "hello-info" not in out and "hello-warning" in out


def test_version_flag_prints_the_version():
    r = CliRunner().invoke(cli.app, ["--version"])
    assert r.exit_code == 0 and r.output == f"dwb {dwb.__version__}\n"


def test_short_v_is_still_verbose(monkeypatch, pair):
    assert "hello-debug" in _logs(monkeypatch, pair, "-v")
    r = CliRunner().invoke(cli.app, ["-v", "--help"])
    assert r.exit_code == 0 and f"dwb {dwb.__version__}" not in r.output
