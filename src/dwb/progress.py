"""Progress events for long-running work (KTD2).

Lower layers (indexing, the runner, OpenWiki generation, evaluation) report progress by
calling an optional ``on_progress(event)`` callback; ``dwb.ui`` renders the events. Without a
callback nothing is reported and the work runs as before. This module is foundation-level so
every emitter can import it without importing any presentation code.

One event shape for every task:

- ``task``: ``embed`` (an index being embedded), ``wiki`` (OpenWiki generation), ``run``
  (an experiment run over questions), ``web`` (a web-only record pass) or ``evaluate``
  (judging one condition's traces).
- ``phase``: ``start`` (``total`` known when countable), ``step`` (``done`` of ``total``
  finished) or ``done`` (the task finished; a task that raises sends no ``done``).
- ``label``: what the task is about (an index ID, a question ID, a condition).
- ``outcome``: for a ``run`` step, ``ok``, ``failed``, ``degraded`` or ``empty``; for a
  ``web`` step, ``ok`` or ``failed``.
- ``data``: an optional payload (the ``TraceRecord`` of a run step, the ``IndexInfo`` of a
  finished index, the paths a renderer may report).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    task: str
    phase: str
    done: int = 0
    total: int | None = None
    label: str = ""
    outcome: str = ""
    elapsed_s: float = 0.0
    data: Any = None


OnProgress = Callable[[ProgressEvent], None]


def report(on_progress: OnProgress | None, task: str, phase: str, **fields: Any) -> None:
    """Send one event when there is a callback; a no-op otherwise."""
    if on_progress is not None:
        on_progress(ProgressEvent(task=task, phase=phase, **fields))
