"""Shared fixtures."""

import pytest

from dwb import ui


@pytest.fixture
def tty(monkeypatch):
    """Treat CliRunner's streams as a terminal (human output), without color, wide."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    ui.configure()
    prev = ui.force_terminal(True)
    yield
    ui.force_terminal(prev)
    ui.configure()
