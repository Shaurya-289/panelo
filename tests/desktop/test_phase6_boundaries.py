"""Read-only boundary checks for the frozen Phase 0--6 scope."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_PHASE6 = _ROOT / "src" / "productivity_app" / "desktop"


def test_phase6_has_no_notification_or_scheduler_delivery_surface() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in _PHASE6.glob("*.py"))
    # Documentation may state the frozen no-notification policy; executable
    # delivery symbols must still remain absent.
    forbidden = ("showMessage(", "toast", "pynput", "keyboard", "WH_KEYBOARD_LL")
    assert all(token.casefold() not in text.casefold() for token in forbidden)
    assert "QTimer.singleShot" in (_PHASE6 / "instance.py").read_text(encoding="utf-8")


def test_phase6_modules_import_without_creating_a_qapplication_or_backend() -> None:
    code = """
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
assert QApplication.instance() is None
import productivity_app.desktop.controller
import productivity_app.desktop.hotkey
import productivity_app.desktop.instance
import productivity_app.desktop.main
import productivity_app.desktop.startup
import productivity_app.desktop.tray
import productivity_app.desktop.window
assert QApplication.instance() is None
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_phase6_does_not_introduce_schema_or_database_lifecycle_calls() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _PHASE6.glob("*.py"))
    assert "create_all(" not in source and "drop_all(" not in source
    migration = _ROOT / "src" / "productivity_app" / "db" / "migrations" / "versions" / "20260924_0001_initial_schema.py"
    assert 'revision: str = "20260924_0001"' in migration.read_text(encoding="utf-8")


def test_desktop_production_modules_remain_small_shell_adapters() -> None:
    for path in _PHASE6.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not (_ROOT / "src" / "productivity_app" / "desktop" / "scheduler.py").exists()

