from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapplication() -> QApplication:
    return QApplication.instance() or QApplication(["phase6-tests"])


@pytest.fixture
def final_ui(application, qapplication, tmp_path):
    from tests.desktop.test_final_ui_integration import make_ui, settle

    ui = make_ui(application, qapplication, tmp_path)
    yield ui
    settle(ui)
    ui.close()
    ui.window.hide()
    ui.window.deleteLater()
    qapplication.processEvents()


@pytest.fixture
def scripted_inputs(monkeypatch):
    """Script menu/sheet choices in service integration tests, not native dialogs.

    QInputDialog methods below are test answer adapters only: production uses
    in-panel InputSheet and QMenu. Separate widget tests exercise that boundary.
    """
    from PySide6.QtWidgets import QInputDialog

    from productivity_app.desktop.ui import prompts

    def choose(parent, title, label, choices, callback):
        value, accepted = QInputDialog.getItem(parent, title, label, choices, editable=False)
        if accepted:
            callback(value)

    def ask(parent, title, label, initial, callback, *, integer=False):
        if integer:
            value, accepted = QInputDialog.getInt(parent, title, label, int(initial), 0, 86400000)
        else:
            value, accepted = QInputDialog.getText(parent, title, label, text=initial)
        if accepted:
            callback(str(value))

    monkeypatch.setattr(prompts, "choose", choose)
    monkeypatch.setattr(prompts, "ask", ask)
