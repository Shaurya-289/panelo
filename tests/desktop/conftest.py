"""Fixtures for the desktop shell tests (single-instance IPC, hotkey, startup, Windows adapters)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Iterator

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapplication() -> Iterator[QApplication]:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(["desktop-tests"])
    yield app
    # Clipboard data left behind at interpreter exit crashes Qt's teardown (exit code 139).
    app.clipboard().clear()
    app.processEvents()
