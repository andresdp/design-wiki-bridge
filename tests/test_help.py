"""Help output: sections, both modes, an example on every command, Ctrl-C notes."""

import re

import click
import pytest
import typer
from typer.testing import CliRunner

import dwb.cli as cli


@pytest.fixture(autouse=True)
def _wide(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")


def _help(*args):
    r = CliRunner().invoke(cli.app, [*args, "--help"])
    assert r.exit_code == 0, r.output
    return r.output


def _commands(group=None, path=()):
    group = group or typer.main.get_command(cli.app)
    for name, cmd in sorted(group.commands.items()):
        yield (*path, name), cmd
        if isinstance(cmd, click.Group):
            yield from _commands(cmd, (*path, name))


def _panels(text):
    """Map panel title -> command names listed in it (parsed from the rendered boxes)."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^╭─ (.+?) ─+╮$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif line.startswith("╰"):
            cur = None
        elif cur and (m := re.match(r"^│ (\w+)\s", line)):
            out[cur].append(m.group(1))
    return out


def test_top_level_sections_and_modes():
    text = _help()
    panels = _panels(text)
    assert panels["Ask"] == ["ask", "chat"]
    assert sorted(panels["User mode"]) == ["index", "init", "status"]
    assert sorted(panels["Research mode"]) == ["benchmark", "experiment", "wiki"]
    listed = [c for p in ("Ask", "User mode", "Research mode") for c in panels[p]]
    top = set(typer.main.get_command(cli.app).commands)
    assert sorted(listed) == sorted(top)  # each command in exactly one section
    assert "dwb init" in text and "--repo" in text and "docs/usage.md" in text
    assert "--version" in text


def test_index_help_puts_code_and_pdf_under_research_mode():
    panels = _panels(_help("index"))
    assert sorted(panels["Research mode"]) == ["code", "pdf"]


def test_every_command_has_an_example():
    for path, _cmd in _commands():
        text = _help(*path)
        assert "Example" in text and f"dwb {' '.join(path)}" in text, path


@pytest.mark.parametrize("path", [("index",), ("index", "code"), ("index", "pdf"),
                                  ("experiment", "run"), ("experiment", "evaluate")])
def test_long_running_commands_mention_ctrl_c(path):
    assert "Ctrl-C" in _help(*path)
