"""Console progress logging for ``dwb`` commands.

Logs go to stderr (stdout stays clean for the JSON results commands print). Uses
``rich`` when available, plain ``logging`` otherwise.
"""

from __future__ import annotations

import logging

NOISY = ("httpx", "httpcore", "openai", "chromadb", "urllib3", "mcp", "langchain_mcp_adapters")


def setup_logging(verbosity: int = 0) -> None:
    """verbosity: -1 quiet (warnings), 0 info, 1+ debug."""
    level = logging.WARNING if verbosity < 0 else logging.DEBUG if verbosity > 0 else logging.INFO
    try:
        from rich.console import Console
        from rich.logging import RichHandler

        handler: logging.Handler = RichHandler(
            console=Console(stderr=True), show_path=False, rich_tracebacks=True,
            markup=True, log_time_format="[%H:%M:%S]",
        )
        fmt = "%(message)s"
    except ImportError:  # pragma: no cover
        handler = logging.StreamHandler()
        fmt = "%(asctime)s %(levelname)s %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger("dwb")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("dwb") else f"dwb.{name}")
