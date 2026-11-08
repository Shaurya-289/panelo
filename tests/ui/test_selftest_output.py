"""Where ``--selftest`` writes its report: the console, or a file for the windowed (frozen) build."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import qWarning
from PySide6.QtWidgets import QApplication

from panelo.ui.selftest import runner


def _fake_inner(code: int = 0) -> Any:
    def inner(_options: Any, _qt_app: Any) -> int:
        print("PASS: something works")
        qWarning("a Qt warning")
        print("selftest: 1/1 passed")
        return code

    return inner


def test_console_run_without_env_var_is_unchanged(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(runner.SELFTEST_OUTPUT_ENV, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = sys.stdout
    seen: list[Any] = []

    def inner(_options: Any, _qt_app: Any) -> int:
        seen.append(sys.stdout)
        print("PASS: on the console")
        return 3

    monkeypatch.setattr(runner, "_run_dev_command", inner)
    assert runner.run_dev_command(None, qapp) == 3  # type: ignore[arg-type]
    assert seen == [before]
    assert sys.stdout is before
    assert "PASS: on the console" in capsys.readouterr().out
    assert not (tmp_path / runner.DEFAULT_OUTPUT_NAME).exists()


def test_env_var_sends_report_and_qt_messages_to_the_file(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "selftest.log"
    monkeypatch.setenv(runner.SELFTEST_OUTPUT_ENV, str(target))
    monkeypatch.setattr(runner, "_run_dev_command", _fake_inner(1))
    before = sys.stdout, sys.stderr

    assert runner.run_dev_command(None, qapp) == 1  # type: ignore[arg-type]

    assert (sys.stdout, sys.stderr) == before
    assert runner._qt_handler is None
    text = target.read_text(encoding="utf-8")
    assert "PASS: something works" in text
    assert "qt: a Qt warning" in text
    assert "selftest: 1/1 passed" in text


def test_no_stdout_writes_the_default_file_in_temp(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(runner.SELFTEST_OUTPUT_ENV, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(runner, "_run_dev_command", _fake_inner(0))

    assert runner.run_dev_command(None, qapp) == 0  # type: ignore[arg-type]

    assert sys.stdout is None
    text = (tmp_path / runner.DEFAULT_OUTPUT_NAME).read_text(encoding="utf-8")
    assert "PASS: something works" in text
    assert "qt: a Qt warning" in text


def test_crash_is_logged_to_the_file_and_fails(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "selftest.log"
    monkeypatch.setenv(runner.SELFTEST_OUTPUT_ENV, str(target))

    def inner(_options: Any, _qt_app: Any) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_dev_command", inner)
    before = sys.stdout

    assert runner.run_dev_command(None, qapp) == 1  # type: ignore[arg-type]

    assert sys.stdout is before
    text = target.read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "RuntimeError: boom" in text


def test_crash_on_the_console_still_raises(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(runner.SELFTEST_OUTPUT_ENV, raising=False)

    def inner(_options: Any, _qt_app: Any) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_dev_command", inner)
    with pytest.raises(RuntimeError, match="boom"):
        runner.run_dev_command(None, qapp)  # type: ignore[arg-type]
