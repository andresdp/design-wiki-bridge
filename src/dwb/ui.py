"""Presentation for every ``dwb`` command: theme, symbols, consoles, messages, output mode.

Results go to stdout and messages to stderr. Consoles and the output mode are resolved when
output is written, not at import: ``CliRunner`` swaps ``sys.stdout``/``sys.stderr`` on every
call, and the global ``--json``/``--no-color`` options change per invocation (``configure``).

Symbols are Unicode with ASCII fallbacks, chosen per console from its encoding. Color and
animation need a terminal, no ``NO_COLOR`` and no ``--no-color``. ``force_terminal`` is the
test hook that makes the current streams count as a terminal.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    verbosity: int = 0  # -1 quiet (-q), 0 normal, 1+ verbose (-v)


_settings = _Settings()
_forced: bool | None = None
_cache: dict[str, tuple[tuple, Console]] = {}
def _new_record() -> dict[str, Any]:
    """An empty stop record: what a Ctrl-C keeps, and notes printed after it."""
    return {"kept": [], "notes": []}


_fallback_record: dict[str, Any] = _new_record()


def configure(*, json: bool = False, no_color: bool = False, verbosity: int = 0) -> None:
    """Set the global options for this invocation (called by the CLI callback)."""
    global _settings
    _settings = _Settings(json=json, no_color=no_color, verbosity=verbosity)
    _fallback_record.clear()
    _fallback_record.update(_new_record())


def quiet() -> bool:
    """``-q``: warnings and errors only, no progress."""
    return _settings.verbosity < 0


def verbose() -> bool:
    """``-v``: diagnostics (timestamps, levels, per-stage timings)."""
    return _settings.verbosity > 0


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
                  force_terminal=_forced,
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
    return _glyphs_for((console or err()).encoding or "utf-8")


@functools.cache
def _glyphs_for(encoding: str) -> Glyphs:
    try:
        _ALL_UNICODE.encode(encoding)
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


def _record() -> dict[str, Any]:
    """The stop record of the current command, kept on the shared Click context meta."""
    try:
        from typer._click.globals import get_current_context
    except ImportError:  # pragma: no cover
        return _fallback_record
    ctx = get_current_context(silent=True)
    if ctx is None:
        return _fallback_record
    return ctx.meta.setdefault(STOP_KEY, _new_record())


def _put(kind: str, text: str | None, key: str | None) -> None:
    """Append ``text`` to the record's ``kind`` list; with a ``key``, replace (or with
    ``text=None`` remove) the entry earlier recorded under that key."""
    rec = _record()
    items: list[str] = rec[kind]
    keyed: dict[str, str] = rec.setdefault(f"_{kind}_keys", {})
    old = keyed.pop(key, None) if key is not None else None
    if old is not None and old in items:
        pos = items.index(old)
        if text is None:
            del items[pos]
        else:
            items[pos] = text
    elif text is not None:
        items.append(text)
    if key is not None and text is not None:
        keyed[key] = text


def keep(item: str, *, key: str | None = None) -> None:
    """Record something finished that survives a Ctrl-C (an index built, a trace written).
    With ``key``, a later call with the same key replaces the entry (a running count)."""
    _put("kept", item, key)


def note_on_stop(text: str | None, *, key: str | None = None) -> None:
    """A line to print after ``! Stopped`` (e.g. "the run is incomplete"). With ``key``, a
    later call replaces it, and ``None`` removes it."""
    _put("notes", text, key)


def stopped(record: dict[str, Any] | None = None, *,
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


# ------------------------------------------------------------------------------ progress

_KIND = {"code": ("Code", "spans"), "pdf": ("PDFs", "pages")}


def _secs(s: float) -> str:
    return f"{s:.1f} s"


def _columns(con: Console) -> list[Any]:
    """Rich progress columns drawn with the console's glyphs (ASCII fallback): a spinner
    frame, the description, a bar and counts when the total is known, a note, elapsed."""
    from rich.progress import ProgressColumn, TextColumn
    from rich.text import Text

    g = glyphs(con)

    class Lead(ProgressColumn):
        def render(self, task):
            frame = g.spinner[int((task.elapsed or 0) * 10) % len(g.spinner)]
            return Text.from_markup(f"  [accent]{escape(frame)}[/]")

    class Bar(ProgressColumn):
        width = 24

        def render(self, task):
            if task.total is None:
                return Text("")
            n = int(self.width * task.completed / task.total) if task.total else 0
            return Text.from_markup(f"[accent]{g.bar_done * n}[/]"
                                    f"[secondary]{g.bar_todo * (self.width - n)}[/] "
                                    f"{int(task.completed)}/{int(task.total)}")

    class Note(ProgressColumn):
        def render(self, task):
            return Text.from_markup(f"[secondary]{escape(task.fields.get('note', ''))}[/]")

    class Elapsed(ProgressColumn):
        def render(self, task):
            return Text(_secs(task.elapsed or 0.0))

    return [Lead(), TextColumn("{task.description}"), Bar(), Note(), Elapsed()]


class ProgressRenderer:
    """Renders ``dwb.progress.ProgressEvent``s on stderr and keeps the Ctrl-C record.

    On a colored terminal with human output: a bar with counts and elapsed time for
    embedding, a spinner with elapsed time for OpenWiki, one bar over the questions of a run
    (running failed/degraded counts, one result line per question printed above it) and over
    each evaluated condition. Otherwise (no terminal, ``NO_COLOR``, JSON output) one plain
    line per milestone; ``-q`` prints nothing. Either way finished indexes and written
    traces are recorded with ``keep`` so a Ctrl-C reports them. Use it through
    ``progress()``, which stops the display on the way out (also on Ctrl-C).
    """

    def __init__(self) -> None:
        self.con = err()
        self.quiet = quiet()
        self.animate = animations() and human() and not self.quiet
        self._live: Any = None  # a rich.progress.Progress while a task is shown
        self._tasks: dict[str, Any] = {}
        self._embed_shown: dict[str, int] = {}
        self._state: dict[str, Any] = {}

    # -- display plumbing

    def line(self, markup: str) -> None:
        """Print a line (Rich markup) above the live display, or plainly without one."""
        if self.quiet:
            return
        target = self._live.console if self._live is not None else self.con
        target.print(markup, soft_wrap=True)

    def show(self, key: str, description: str, total: int | None = None,
             note: str = "") -> None:
        """Add a live task: a bar with counts when ``total`` is known, otherwise a spinner
        with elapsed time. Nothing without animations."""
        if not self.animate:
            return
        if self._live is None:
            from rich.progress import Progress

            self._live = Progress(*_columns(self.con), console=self.con, transient=True,
                                  refresh_per_second=10)
            self._live.start()
        self._tasks[key] = self._live.add_task(description, total=total, note=note)

    def _update(self, key: str, **fields: Any) -> None:
        if self._live is not None and key in self._tasks:
            self._live.update(self._tasks[key], **fields)

    def hide(self, key: str) -> None:
        """Remove a live task; the display stops with the last one."""
        tid = self._tasks.pop(key, None)
        if self._live is not None and tid is not None:
            self._live.remove_task(tid)
            if not self._tasks:
                self.close()


    def close(self) -> None:
        """Stop the live display, if any (idempotent)."""
        live, self._live, self._tasks = self._live, None, {}
        if live is not None:
            live.stop()

    # -- events

    def __call__(self, ev: Any) -> None:
        handler = getattr(self, f"_on_{ev.task}", None)
        if handler is not None:
            handler(ev)

    def _on_embed(self, ev: Any) -> None:
        key, g = f"embed:{ev.label}", glyphs(self.con)
        if ev.phase == "start":
            title, unit = _KIND.get((ev.data or {}).get("kind", ""), ("Index", "items"))
            self._embed_shown[ev.label] = 0
            if self.animate:
                self.show(key, f"{title:<6}", ev.total or 0, note=unit)
            else:
                self.line(f"Embedding {ev.total} {unit} into {escape(ev.label)}")
        elif ev.phase == "step":
            if self.animate:
                self._update(key, completed=ev.done)
            elif ev.total and ev.done < ev.total:
                tenth = ev.done * 10 // ev.total  # one line per ~10%, at most one per batch
                if tenth > self._embed_shown.get(ev.label, 0):
                    self._embed_shown[ev.label] = tenth
                    self.line(f"  embedded {ev.done}/{ev.total}")
        elif ev.phase == "done":
            kind = getattr(ev.data, "kind", "")
            title, unit = _KIND.get(kind, ("Index", "items"))
            self.hide(key)
            when = "reused" if getattr(ev.data, "reused", False) else _secs(ev.elapsed_s)
            self.line(f"  [ok]{g.ok}[/] {title:<6} {ev.done} {unit}  "
                      f"[secondary]{escape(ev.label)}  {when}[/]")
            keep(f"{kind} index {ev.label}" if kind else f"index {ev.label}")

    def _on_wiki(self, ev: Any) -> None:
        g, data = glyphs(self.con), ev.data or {}
        log = data.get("log")
        if ev.phase == "start":
            what = f"OpenWiki {ev.label}"
            if data.get("provider"):
                what += f" ({data['provider']}/{data.get('model', '')})"
            if self.animate and log is not None:
                self.show("wiki", what, None, note=f"output: {log}; can take ~15 min ")
            else:
                self.line(f"Running {escape(what)}; it makes model calls and can take ~15 min"
                          + (f". Output: {escape(str(log))}" if log else ""))
        elif ev.phase == "done":
            self.hide("wiki")
            self.line(f"  [ok]{g.ok}[/] Wiki   generated by OpenWiki  "
                      f"[secondary]{_secs(ev.elapsed_s)}[/]")
            keep("wiki generated in openwiki/")

    def _run_note(self) -> None:
        st = self._state
        note_on_stop(f"Run incomplete ({st['done']} of {st['total']} questions, no "
                     "run_summary.json): it cannot be evaluated. Rerun with --overwrite to "
                     "restart it.", key="run")

    def _on_run(self, ev: Any) -> None:
        g, st = glyphs(self.con), self._state
        if ev.phase == "start":
            st.update(done=0, total=ev.total or 0, traces=(ev.data or {}).get("traces"))
            self._run_note()
            if self.animate:
                self.show("run", "Questions", ev.total or 0, note="0 failed, 0 degraded ")
            else:
                self.line(f"Running {ev.total} questions")
        elif ev.phase == "step":
            data = ev.data or {}
            st["done"] = ev.done
            n = ev.done
            keep(f"{n} trace{'s' if n != 1 else ''} in {st.get('traces')}", key="traces")
            self._run_note()
            sym = {"ok": f"[ok]{g.ok}[/]", "failed": f"[fail]{g.fail}[/]"}.get(
                ev.outcome, f"[warn]{g.warn}[/]")
            what = {"ok": "", "failed": "failed", "degraded": "degraded",
                    "empty": "empty context"}.get(ev.outcome, ev.outcome)
            self.line(f"  {sym} {escape(ev.label)}  " + (f"{what}  " if what else "")
                      + f"[secondary]{_secs(ev.elapsed_s)}[/]")
            self._update("run", completed=ev.done,
                         note=f"{data.get('failed', 0)} failed, "
                              f"{data.get('degraded', 0)} degraded ")
        elif ev.phase == "done":
            self.hide("run")
            note_on_stop(None, key="run")

    def _on_evaluate(self, ev: Any) -> None:
        g = glyphs(self.con)
        if ev.phase == "start":
            if self.animate:
                self.show("evaluate", f"Judging {escape(ev.label)}", ev.total or 0)
            else:
                self.line(f"Evaluating condition {escape(ev.label)}: {ev.total} traces")
        elif ev.phase == "step":
            self._update("evaluate", completed=ev.done)
            if ev.outcome == "skipped":
                self.line(f"  [warn]{g.warn}[/] {escape(ev.label)}  skipped (not in the "
                          "question set)")
            elif not self.animate:
                self.line(f"  [{ev.done}/{ev.total}] {escape(ev.label)}")
        elif ev.phase == "done":
            self.hide("evaluate")
            self.line(f"  [ok]{g.ok}[/] Condition {escape(ev.label)}: {ev.total} traces scored")


@contextmanager
def progress(stop_hint: str | None = None) -> Iterator[ProgressRenderer]:
    """A progress renderer for one long-running command; pass it as ``on_progress``.
    ``stop_hint`` (e.g. "Ctrl-C stops; finished indexes are kept.") is printed first
    (R15). The live display stops on the way out, before any error or ``! Stopped``."""
    r = ProgressRenderer()
    if stop_hint and not r.quiet:
        r.con.print(f"[secondary]{escape(stop_hint)}[/]", soft_wrap=True)
    try:
        yield r
    finally:
        r.close()
