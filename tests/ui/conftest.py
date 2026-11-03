"""Fixtures for the UI bridge tests: offscreen Qt, a temporary database and a fake clock."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_FORCE_STDERR_LOGGING", "1")

import json
from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from productivity_app.bootstrap import Application
from productivity_app.ui.bridge.backend import Backend
from tests.conftest import FakeClock


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(["tests"])
    yield app
    # Clipboard data left behind at interpreter exit crashes Qt's teardown (exit code 139).
    app.clipboard().clear()
    app.processEvents()


@pytest.fixture
def backend(qapp: QApplication, application: Application, clock: FakeClock) -> Iterator[Backend]:
    """A bridge on a temp database; jobs run inline and time is the fake clock's."""
    bridge = Backend(application, clock=clock, sync_jobs=True, timezone="UTC")
    yield bridge
    bridge.close()


def listing(backend: Backend, kind: str) -> list[dict[str, Any]]:
    """What the QML pages get for a list: the JSON the Backend published."""
    result: list[dict[str, Any]] = json.loads(backend._json[kind])
    return result


def ms(moment: Any) -> int:
    return int(moment.timestamp() * 1000)
