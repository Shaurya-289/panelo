"""The desktop assembly: QML windows over the bridge, the shell's reactions, the start-up
switch and the process-level launch rules.  Offscreen Qt, a temp database and fakes for the
machine (hotkey, registry, tray); nothing real is touched."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from panelo.bootstrap import Application
from panelo.core.config import AppConfig
from panelo.desktop.app import (
    DesktopApp,
    build_desktop,
    reconcile_startup,
    startup_handler,
)
from panelo.desktop.main import (
    DesktopOptions,
    parse_desktop_options,
    run_desktop,
    show_initial,
)
from panelo.desktop.startup import StartupCoordinator, default_launcher
from panelo.files.library import FileLibrary
from panelo.ui.bridge.backend import Backend
from panelo.ui.selftest.harness import FakeHotkeyBackend, FakeStartupRegistry, hard_delete
from tests.conftest import FakeClock


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    path = tmp_path / "launcher" / "panelo.exe"
    path.parent.mkdir()
    path.write_bytes(b"")
    return path


@pytest.fixture
def desktop(
    qapp: QApplication, application: Application, clock: FakeClock, tmp_path: Path, launcher: Path
) -> Iterator[tuple[DesktopApp, list[str], FakeHotkeyBackend, FakeStartupRegistry]]:
    warnings: list[str] = []

    def handler(kind: QtMsgType, _context: Any, message: str) -> None:
        quiet = message.startswith("QFontDatabase")  # the offscreen platform has no font folder
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) and not quiet:
            warnings.append(message)

    previous = qInstallMessageHandler(handler)
    hotkeys, registry = FakeHotkeyBackend(), FakeStartupRegistry()
    built = build_desktop(
        qapp, application, data_dir=tmp_path / "data", clock=clock,
        library=FileLibrary(tmp_path / "library", trash=hard_delete),
        fake_backdrop=True, tray=False, hotkey_backend=hotkeys, startup_registry=registry,
        startup_launcher=launcher, stub_sounds=True, stub_launch=True, sync_jobs=True,
        timezone="UTC", start_scheduler=False,
    )
    yield built, warnings, hotkeys, registry
    built.close()
    qInstallMessageHandler(previous)  # type: ignore[arg-type]


def test_the_windows_load_without_qml_warnings(
    desktop: tuple[DesktopApp, list[str], FakeHotkeyBackend, FakeStartupRegistry],
) -> None:
    built, warnings, hotkeys, _ = desktop
    assert built.store.property("focusLabel") == "Session 1 of 4"
    assert built.store.property("tourDone") is False  # a fresh database: the first run shows the tour
    assert hotkeys.calls == ["Ctrl+Alt+Space"]  # the saved hotkey is registered at start
    assert warnings == []


def test_the_store_mirrors_the_backend_and_saves_what_a_page_assigns(
    desktop: tuple[DesktopApp, list[str], FakeHotkeyBackend, FakeStartupRegistry],
) -> None:
    built, warnings, _, _ = desktop
    note_id = built.backend.addNote("From a test", "body", "teal")
    notes = json.loads(built.backend.notesJson)
    assert [n["id"] for n in notes] == [note_id]
    assert [n["title"] for n in built.store.property("notes").toVariant()] == ["From a test"]
    built.store.setProperty("userName", "Riya")  # a page assigns a setting
    built.backend.flush_settings()
    assert built.application.settings.get_settings().user_name == "Riya"
    built.store.setProperty("focusMs", 10 * 60000)  # a not-yet-started cycle follows the length
    assert built.store.property("focusTotal") == 600 and built.store.property("focusRemaining") == 600
    assert warnings == []


def test_alerts_follow_the_notification_settings(
    desktop: tuple[DesktopApp, list[str], FakeHotkeyBackend, FakeStartupRegistry],
) -> None:
    built, _, _, _ = desktop
    controller = built.controller
    built.backend.reminderFired.emit({"id": "r1", "title": "Stretch"})
    assert controller.notify_log == [("toast", "Stretch")]
    built.store.setProperty("notificationStyle", "both")
    built.backend.reminderFired.emit({"id": "r1", "title": "Again"})
    assert controller.notify_log[1:] == [("toast", "Again"), ("windows", "Again")]
    built.store.setProperty("reminderAlerts", False)
    built.backend.reminderFired.emit({"id": "r2", "title": "Silenced"})
    assert len(controller.notify_log) == 3
    built.backend.focusCompleted.emit("Focus session done")
    built.backend.timerFinished.emit("Tea")
    built.backend.notice.emit("Something failed")
    assert controller.focus_toasts == 1 and controller.last_timer_message == "Tea timer done"
    assert controller.notify_log[-1] == ("windows", "Something failed") or controller.notify_log[-1][1] == "Something failed"


def test_the_hotkey_recorder_registers_then_saves(
    desktop: tuple[DesktopApp, list[str], FakeHotkeyBackend, FakeStartupRegistry],
) -> None:
    built, _, hotkeys, _ = desktop
    assert built.hotkeys.tryHotkey("Ctrl+K")["error"] == "needs two modifiers (Ctrl, Alt or Shift)"
    result = built.hotkeys.tryHotkey("Ctrl+Alt+K")
    assert result == {"ok": True, "error": "", "text": "Ctrl+Alt+K"}
    assert hotkeys.calls[-1] == "Ctrl+Alt+K"
    assert built.application.settings.get_settings().global_hotkey == "Ctrl+Alt+K"
    hotkeys.taken.add("Ctrl+Alt+F")
    assert built.hotkeys.tryHotkey("Ctrl+Alt+F")["error"] == "already used by another app"
    assert built.application.settings.get_settings().global_hotkey == "Ctrl+Alt+K"


def test_start_with_windows_writes_and_removes_the_run_entry(
    desktop: tuple[DesktopApp, list[str], FakeHotkeyBackend, FakeStartupRegistry], launcher: Path
) -> None:
    built, _, _, registry = desktop
    built.store.setProperty("startWithWindows", True)
    assert registry.value is not None and str(registry.value.value).endswith("--startup")
    assert built.application.settings.get_settings().start_with_windows is True
    built.store.setProperty("startWithWindows", False)
    assert registry.value is None
    assert built.application.settings.get_settings().start_with_windows is False


def test_start_with_windows_needs_a_launcher_and_says_so(
    qapp: QApplication, application: Application, clock: FakeClock
) -> None:
    backend = Backend(application, clock=clock, sync_jobs=True, timezone="UTC")
    notices: list[str] = []
    backend.notice.connect(notices.append)
    backend.startup_handler = startup_handler(
        backend, StartupCoordinator(application, FakeStartupRegistry(), None)
    )
    backend.setSetting("startWithWindows", True)
    assert len(notices) == 1 and "launcher" in notices[0]
    assert application.settings.get_settings().start_with_windows is False
    backend.close()


def test_a_missing_run_entry_is_repaired_at_start(
    qapp: QApplication, application: Application, clock: FakeClock, launcher: Path
) -> None:
    backend = Backend(application, clock=clock, sync_jobs=True, timezone="UTC")
    registry = FakeStartupRegistry()
    coordinator = StartupCoordinator(application, registry, launcher)
    backend.set_setting_now("startWithWindows", True)  # no handler yet: only the preference
    assert registry.value is None
    reconcile_startup(backend, coordinator)
    assert registry.value is not None
    backend.close()


def test_default_launcher_is_none_without_an_installed_script() -> None:
    launcher = default_launcher()
    assert launcher is None or launcher.name == "panelo.exe"


# -- The process ----------------------------------------------------------------------------


def test_options_parse() -> None:
    assert parse_desktop_options([]) == DesktopOptions()
    assert parse_desktop_options(["--startup"]).start_hidden_requested is True
    assert parse_desktop_options(["--startup", "--show"]).start_hidden_requested is False
    options = parse_desktop_options(["--selftest"])
    assert options.selftest and options.dev_command


def test_startup_launch_starts_hidden_and_a_normal_launch_shows() -> None:
    shown: list[str] = []
    controller = SimpleNamespace(
        tray=object(), prewarm_full=lambda done: done(), show_full=lambda section: shown.append(section)
    )
    desktop = SimpleNamespace(controller=controller)
    show_initial(desktop, DesktopOptions(startup=True))  # type: ignore[arg-type]
    assert shown == []
    show_initial(desktop, DesktopOptions(startup=True, show=True))  # type: ignore[arg-type]
    show_initial(desktop, DesktopOptions())  # type: ignore[arg-type]
    assert shown == ["notes", "notes"]
    controller.tray = None  # no tray to find the app again: always show it
    show_initial(desktop, DesktopOptions(startup=True))  # type: ignore[arg-type]
    assert shown == ["notes", "notes", "notes"]


class _SecondaryLease:
    primary = False

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_a_second_launch_only_asks_the_first_to_show_itself(
    qapp: QApplication, app_config: AppConfig
) -> None:
    lease = _SecondaryLease()
    activations: list[str] = []

    def factory(_config: AppConfig) -> Application:
        raise AssertionError("a second launch must not open the database")

    code = run_desktop(
        [],
        config=app_config,
        backend_factory=factory,
        mutex_backend=SimpleNamespace(acquire=lambda identity: lease),  # type: ignore[arg-type]
        sid_provider=lambda: "S-1-5-test",
        activation_sender=activations.append,
        qapplication=qapp,
    )
    assert code == 0 and lease.closed and len(activations) == 1
