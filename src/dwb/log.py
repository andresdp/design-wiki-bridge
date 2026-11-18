"""Diagnostic logging for ``dwb`` commands.

Logs go to stderr (stdout stays clean for the results commands print). By default a record is
just its message (warnings and errors get a ``!`` prefix); ``-v`` adds the time and the level,
``-q`` keeps warnings and errors only. Uses ``rich`` when available, plain ``logging``
otherwise.
"""

from __future__ import annotations

import logging
from typing import Any

NOISY = ("httpx", "httpcore", "openai", "chromadb", "urllib3", "mcp", "langchain_mcp_adapters")


class _Plain(logging.Formatter):
    """Default (non-verbose) format: the message, with ``!`` marking warnings and errors."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if record.levelno >= logging.ERROR:
            return f"[red]![/] {msg}"
        if record.levelno >= logging.WARNING:
            return f"[yellow]![/] {msg}"
        return msg


def setup_logging(verbosity: int = 0, console: Any = None) -> None:
    """verbosity: -1 quiet (warnings), 0 info, 1+ debug. ``console``: the Rich console to log
    to, or a zero-argument callable returning it, resolved on every record so the handler
    follows the current stderr (default: a stderr console)."""
    level = logging.WARNING if verbosity < 0 else logging.DEBUG if verbosity > 0 else logging.INFO
    verbose = verbosity > 0
    try:
        from rich.console import Console
        from rich.logging import RichHandler

        if console is None:
            fixed = Console(stderr=True)  # resolves sys.stderr itself on each write
            get_console = lambda: fixed  # noqa: E731
        elif callable(console):
            get_console = console
        else:
            get_console = lambda: console  # noqa: E731

        class _CurrentConsole(RichHandler):
            """A RichHandler that asks for the console per record, never holding a stale stream."""

            @property
            def console(self):
                return get_console()

            @console.setter
            def console(self, _value) -> None:
                pass

        handler: logging.Handler = _CurrentConsole(
            show_path=False, rich_tracebacks=True,
            markup=True, show_time=verbose, show_level=verbose, log_time_format="[%H:%M:%S]",
        )
        formatter = logging.Formatter("%(message)s") if verbose else _Plain("%(message)s")
    except ImportError:  # pragma: no cover
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s" if verbose
                                      else "%(message)s")
    handler.setFormatter(formatter)
    root = logging.getLogger("dwb")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("dwb") else f"dwb.{name}")
