"""Presentation for every ``dwb`` command: theme, symbols, consoles, messages, output mode.

Results go to stdout and messages to stderr. Consoles and the output mode are resolved when
output is written, not at import: ``CliRunner`` swaps ``sys.stdout``/``sys.stderr`` on every
call, and the global ``--json``/``--no-color`` options change per invocation (``configure``).

Symbols are Unicode with ASCII fallbacks, chosen per console from its encoding. Color and
animation need a terminal, no ``NO_COLOR`` and no ``--no-color``. ``force_terminal`` is the
test hook that makes the current streams count as a terminal.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, TextIO

from rich.console import Console
from rich.markup import escape
from rich.theme import Theme

THEME = Theme({"ok": "green", "warn": "yellow", "fail": "red", "accent": "cyan",
               "secondary": "dim"})


@dataclass(frozen=True)
class Glyphs:
    ok: str
    fail: str
    warn: str
    bullet: str
    arrow: str
    sep: str
    ellipsis: str
    spinner: tuple[str, ...]
    bar_done: str
    bar_todo: str


UNICODE = Glyphs("✓", "✗", "!", "•", "→", "·", "…",
                 ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"), "█", "░")
ASCII = Glyphs("ok", "x", "!", "-", "->", "-", "...", ("|", "/", "-", "\\"), "#", "-")
_ALL_UNICODE = "".join([UNICODE.ok, UNICODE.fail, UNICODE.bullet, UNICODE.arrow, UNICODE.sep,
                        UNICODE.ellipsis, *UNICODE.spinner, UNICODE.bar_done,
                        UNICODE.bar_todo])


# ------------------------------------------------------------------- per-invocation state


@dataclass
class _Settings:
    json: bool = False
    no_color: bool = False


_settings = _Settings()
_forced: bool | None = None
_cache: dict[str, tuple[tuple, Console]] = {}
_fallback_record: dict[str, list[str]] = {"kept": [], "notes": []}


def configure(*, json: bool = False, no_color: bool = False) -> None:
    """Set the global options for this invocation (called by the CLI callback)."""
    global _settings
    _settings = _Settings(json=json, no_color=no_color)
    _fallback_record["kept"].clear()
    _fallback_record["notes"].clear()


def force_terminal(value: bool | None = True) -> bool | None:
    """Test hook: treat the current streams as a terminal (True), not one (False), or detect
    (None). Returns the previous value."""
    global _forced
    prev, _forced = _forced, value
    return prev


def is_terminal(stream: TextIO | None = None) -> bool:
    if _forced is not None:
        return _forced
    stream = sys.stdout if stream is None else stream
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def color_enabled(stream: TextIO | None = None) -> bool:
    return (is_terminal(stream) and not _settings.no_color
            and not os.environ.get("NO_COLOR"))


def animations(stream: TextIO | None = None) -> bool:
    """Spinners and live bars: only on a colored terminal (stderr by default)."""
    return color_enabled(sys.stderr if stream is None else stream)


def json_mode() -> bool:
    """Data commands print JSON when stdout is not a terminal or ``--json`` is set."""
    return _settings.json or not is_terminal(sys.stdout)


def human() -> bool:
    """The opposite of ``json_mode``: a data command's header, summary and outcome line show."""
    return not json_mode()


def json_requested() -> bool:
    """The global ``--json`` was given (for commands that are human even when piped)."""
    return _settings.json


def interactive() -> bool:
    """Prompting is allowed: stdin is a terminal (or the test hook says so)."""
    return is_terminal(sys.stdin)


# ---------------------------------------------------------------------------- consoles


def _console(kind: str, stream: TextIO) -> Console:
    color = color_enabled(stream)
    key = (stream, color, _forced)
    hit = _cache.get(kind)
    if hit is not None and hit[0] == key:
        return hit[1]
    con = Console(file=stream, theme=THEME, highlight=False,
                  force_terminal=_forced if _forced is not None else None,
                  color_system="auto" if color else None,
                  no_color=not color)
    _cache[kind] = (key, con)
    return con


def out() -> Console:
    """Console for results (the current stdout)."""
    return _console("out", sys.stdout)


def err() -> Console:
    """Console for progress, messages and errors (the current stderr)."""
    return _console("err", sys.stderr)


def glyphs(console: Console | None = None) -> Glyphs:
    """Unicode symbols when the console's encoding can represent them, else ASCII."""
    enc = (console or err()).encoding or "utf-8"
    try:
        _ALL_UNICODE.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return ASCII
    return UNICODE


# ---------------------------------------------------------------------------- messages


def error(what: str, fix: str | None = None, *, console: Console | None = None) -> None:
    """``✗ <what happened>`` and, when there is one, ``→ <how to fix>`` (stderr)."""
    con = console or err()
    g = glyphs(con)
    con.print(f"[fail]{g.fail}[/] {escape(what)}", soft_wrap=True)
    if fix:
        con.print(f"[accent]{g.arrow}[/] {escape(fix)}", soft_wrap=True)


def warning(msg: str, *, console: Console | None = None) -> None:
    con = console or err()
    con.print(f"[warn]{glyphs(con).warn}[/] {escape(msg)}", soft_wrap=True)


def outcome(msg: str, next_cmd: str | None = None, *, console: Console | None = None) -> None:
    """``✓ <what changed>`` plus ``→ next: <command>`` on the same line (stderr)."""
    con = console or err()
    g = glyphs(con)
    line = f"[ok]{g.ok}[/] {escape(msg)}"
    if next_cmd:
        line += f" [accent]{g.arrow}[/] next: [bold]{escape(next_cmd)}[/]"
    con.print(line, soft_wrap=True)


def header(*parts: str, console: Console | None = None) -> None:
    """The one-line context header, e.g. ``requests · user mode · b25c87d7cb8d`` (stderr)."""
    con = console or err()
    sep = f" [secondary]{glyphs(con).sep}[/] "
    con.print(sep.join(escape(p) for p in parts if p), soft_wrap=True)


def bullet(msg: str, *, console: Console | None = None) -> None:
    con = console or err()
    con.print(f"  {glyphs(con).bullet} {escape(msg)}", soft_wrap=True)


def next_step(cmd: str, *, console: Console | None = None) -> None:
    """``→ next: <command>`` on its own line, for when there is no ``✓`` to report (stderr)."""
    con = console or err()
    con.print(f"[accent]{glyphs(con).arrow}[/] next: [bold]{escape(cmd)}[/]", soft_wrap=True)


def kv(rows: list[tuple[str, Any]], *, console: Console | None = None) -> None:
    """A compact summary, one ``key  value`` pair per line, keys aligned (stdout by default).
    Plain lines, no padding after the value, so the output stays grep-parseable."""
    table(["", ""], [[str(k), "-" if v is None else str(v)] for k, v in rows], header=False,
          styles={0: lambda _: "secondary"}, console=console)


def table(columns: list[str], rows: list[list[str]], *, title: str | None = None,
          header: bool = True, styles: dict[int, Callable[[str], str]] | None = None,
          console: Console | None = None) -> None:
    """A results table: an optional title line, a header row, then one line per row with the
    columns aligned by two spaces (stdout by default). ``styles`` maps a column index to a
    function giving the Rich style of a cell value."""
    con = console or out()
    lines = [columns] if header else []
    lines += [[str(c) for c in r] for r in rows]
    if not lines:
        return
    widths = [max(len(r[i]) for r in lines) for i in range(len(columns))]
    if title:
        con.print(f"[bold]{escape(title)}[/]", soft_wrap=True)
    for n, row in enumerate(lines):
        cells = []
        for i, cell in enumerate(row):
            pad = " " * (widths[i] - len(cell)) if i < len(row) - 1 else ""
            if header and n == 0:
                style = "bold"
            else:
                style = styles[i](cell) if styles and i in styles else ""
            cells.append((f"[{style}]{escape(cell)}[/]" if style else escape(cell)) + pad)
        con.print("  ".join(cells), soft_wrap=True)


# ------------------------------------------------------------------------- output mode


def _to_json(result: Any) -> str:
    if hasattr(result, "model_dump_json"):  # pydantic
        return result.model_dump_json(indent=2)
    if is_dataclass(result) and not isinstance(result, type):
        result = asdict(result)
    return json.dumps(result, indent=2)


def emit(result: Any, human: Callable[[Any], None] | None = None) -> None:
    """Print a data command's result: JSON in JSON mode (or with no human renderer), otherwise
    ``human(result)``."""
    if human is None or json_mode():
        sys.stdout.write(_to_json(result) + "\n")
        sys.stdout.flush()
    else:
        human(result)


# ------------------------------------------------------------------ unexpected errors


def error_log_path() -> Path:
    """Where the last unexpected error's traceback goes: $XDG_CACHE_HOME/dwb/last-error.log."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "dwb" / "last-error.log"


def unexpected(exc: BaseException, *, verbose: bool = False,
               console: Console | None = None) -> None:
    """Two-line summary of an unexpected exception; the traceback goes to the error log, and
    also to stderr with ``verbose``."""
    con = console or err()
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path: Path | None = error_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from dwb import __version__

        path.write_text(f"dwb {__version__}\n{' '.join(sys.argv)}\n\n{tb}")
    except OSError:
        path = None
    msg = str(exc)
    what = f"Unexpected error: {type(exc).__name__}" + (f": {msg}" if msg else "")
    hint = "rerun with -v for the traceback"
    error(what, f"details: {path}; {hint}" if path else hint, console=con)
    if verbose:
        con.file.write(tb)
        con.file.flush()


# -------------------------------------------------------------------- Ctrl-C: kept record

STOP_KEY = "dwb.stop_record"  # on the Click context's shared ``meta``


def _record() -> dict[str, list[str]]:
    """The stop record of the current command, kept on the shared Click context meta."""
    try:
        from typer._click.globals import get_current_context
    except ImportError:  # pragma: no cover
        return _fallback_record
    ctx = get_current_context(silent=True)
    if ctx is None:
        return _fallback_record
    return ctx.meta.setdefault(STOP_KEY, {"kept": [], "notes": []})


def keep(item: str) -> None:
    """Record something finished that survives a Ctrl-C (an index built, a trace written)."""
    _record()["kept"].append(item)


def note_on_stop(text: str) -> None:
    """A line to print after ``! Stopped`` (e.g. "the run is incomplete")."""
    _record()["notes"].append(text)


def stopped(record: dict[str, list[str]] | None = None, *,
            console: Console | None = None) -> None:
    """``! Stopped``, then what was kept and any notes (stderr)."""
    con = console or err()
    g = glyphs(con)
    rec = record if record is not None else _record()
    con.print(f"[warn]{g.warn}[/] Stopped", soft_wrap=True)
    for item in rec.get("kept", []):
        con.print(f"  {g.bullet} kept: {escape(item)}", soft_wrap=True)
    for note in rec.get("notes", []):
        con.print(escape(note), soft_wrap=True)
