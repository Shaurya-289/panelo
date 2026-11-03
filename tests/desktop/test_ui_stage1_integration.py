from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from productivity_app.desktop.controller import QuitResult, ShellController
from productivity_app.desktop.hotkey import HotkeyManager
from productivity_app.windows.mutex import MutexLease


@dataclass
class Settings:
    minimize_to_tray: bool = False
    global_hotkey: str = "Ctrl+Alt+Space"
    start_with_windows: bool = False
    version: int = 1


class Backend:
    def __init__(self) -> None:
        self.settings = self
        self.closes = 0
        self.fail_close = False

    def get_settings(self) -> Settings:
        return Settings()

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("An operation is active")
        self.closes += 1


class Lease:
    primary = True

    def __init__(self) -> None:
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


class Tray:
    available = True

    def __init__(self) -> None:
        self.closes = 0

    def show(self) -> None:
        pass

    def close(self) -> None:
        self.closes += 1


def make_controller(qapplication: QApplication) -> tuple[ShellController, Backend, Tray, Lease]:
    backend, tray, lease = Backend(), Tray(), Lease()
    controller = ShellController(
        cast(Any, backend), qapplication, cast(MutexLease, lease), tray=cast(Any, tray)
    )
    return controller, backend, tray, lease


def test_hotkey_toggle_escape_and_show_only_share_one_window(qapplication: QApplication) -> None:
    controller, backend, tray, lease = make_controller(qapplication)
    window = controller.window
    try:
        controller.toggle_window()
        assert not window.isHidden()
        hwnd = int(window.winId())
        controller.toggle_window()
        assert window.isHidden()
        controller.toggle_window()
        controller.activate_window()  # IPC is not a toggle.
        controller.show_window()  # Tray Open is not a toggle.
        assert not window.isHidden()
        window.activateWindow()
        qapplication.processEvents()
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert window.isHidden()
        controller.show_window()
        assert controller.window is window and int(window.winId()) == hwnd
        assert backend.closes == 0 and lease.closes == 0 and tray.closes == 0
    finally:
        controller.request_quit()
        window.deleteLater()


def test_missing_tray_minimizes_and_hotkey_restores(qapplication: QApplication) -> None:
    controller, backend, tray, lease = make_controller(qapplication)
    tray.available = False
    try:
        controller.show_window()
        controller.request_overlay_hide()
        assert controller.window.isMinimized()
        controller.toggle_window()
        assert not controller.window.isMinimized() and not controller.window.isHidden()
        assert backend.closes == 0 and lease.closes == 0
    finally:
        controller.request_quit()
        controller.window.deleteLater()


def test_quiescing_and_close_rejection_preserve_overlay(qapplication: QApplication) -> None:
    controller, backend, tray, lease = make_controller(qapplication)
    window = controller.window
    controller.show_window()
    controller._quiescing = True
    controller.toggle_window()
    controller.request_overlay_hide()
    assert not window.isHidden()
    controller._quiescing = False
    backend.fail_close = True
    assert controller.request_quit() == QuitResult.REJECTED
    assert not controller.is_quiescing and not controller.is_closed
    controller.toggle_window()
    assert window.isHidden()
    backend.fail_close = False
    assert controller.request_quit() == QuitResult.SUCCESS
    controller.toggle_window()
    controller.request_overlay_hide()
    assert controller.is_closed and window.isHidden()
    assert backend.closes == tray.closes == lease.closes == 1
    assert controller.request_quit() == QuitResult.ALREADY_CLOSED
    window.deleteLater()


def test_production_composition_toggles_and_secondary_launch_creates_no_backend(
    qapplication: QApplication, tmp_path: Path, monkeypatch: Any
) -> None:
    from productivity_app.core.config import AppConfig, DatabaseConfig
    from productivity_app.core.paths import AppPaths
    from productivity_app.desktop import main

    controllers: list[ShellController] = []
    managers: list[HotkeyManager] = []
    backends: list[Backend] = []

    class Controller(ShellController):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, tray=cast(Any, Tray()), **kwargs)
            controllers.append(self)

    class Manager(HotkeyManager):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            managers.append(self)

    class Mutex:
        def __init__(self, primary: bool) -> None:
            self.primary = primary

        def acquire(self, identity: str) -> Lease:
            lease = Lease()
            lease.primary = self.primary
            return lease

    class Hotkeys:
        def register(self, *args: Any) -> None:
            pass

        def unregister(self, *args: Any) -> None:
            pass

    def backend_factory(config: AppConfig) -> Any:
        backend = Backend()
        backends.append(backend)
        return backend

    monkeypatch.setattr(main, "ShellController", Controller)
    monkeypatch.setattr(main, "HotkeyManager", Manager)
    config = AppConfig(AppPaths(tmp_path, tmp_path / "logs"), DatabaseConfig(tmp_path / "ui.db"))
    try:
        assert (
            main.run_desktop(
                [],
                config=config,
                backend_factory=backend_factory,
                mutex_backend=cast(Any, Mutex(True)),
                sid_provider=lambda: "S-1-5-21-ui-test",
                hotkey_backend=cast(Any, Hotkeys()),
                qapplication=qapplication,
                execute=False,
            )
            == 0
        )
        controller = controllers[0]
        assert len(managers) == len(controllers) == len(backends) == 1
        window = controller.window
        managers[0]._activate()  # Invoke the actual callback composed by run_desktop.
        assert window.isHidden()
        managers[0]._activate()
        assert not window.isHidden()
        calls: list[str] = []

        def activate(name: str) -> None:
            calls.append(name)
            controller.activate_window()

        assert (
            main.run_desktop(
                [],
                config=config,
                backend_factory=backend_factory,
                mutex_backend=cast(Any, Mutex(False)),
                sid_provider=lambda: "S-1-5-21-ui-test",
                activation_sender=activate,
                qapplication=qapplication,
                execute=False,
            )
            == 0
        )
        assert len(calls) == 1 and len(backends) == len(controllers) == len(managers) == 1
        assert controller.window is window and not window.isHidden()
    finally:
        for controller in controllers:
            controller.request_quit()
            controller.window.deleteLater()


def test_real_tray_actions_remain_show_only_and_hide_only(qapplication: QApplication) -> None:
    from productivity_app.desktop.tray import ShellTray

    controller, _, _, _ = make_controller(qapplication)
    tray = ShellTray(controller.show_window, controller.hide_window, controller.request_quit)
    try:
        tray.open_action.trigger()
        tray.open_action.trigger()
        assert not controller.window.isHidden()
        tray.hide_action.trigger()
        tray.hide_action.trigger()
        assert controller.window.isHidden()
    finally:
        tray.close()
        controller.request_quit()
        controller.window.deleteLater()


def test_ui_imports_are_safe_and_preview_boundary_is_enforced() -> None:
    import ast
    import hashlib
    import subprocess
    import sys

    root = Path(__file__).parents[2]
    code = """
from PySide6.QtWidgets import QApplication
assert QApplication.instance() is None
import productivity_app.desktop.ui.panel
import productivity_app.desktop.ui.panels
import productivity_app.desktop.ui.files
import productivity_app.desktop.ui.dashboard
import productivity_app.desktop.window
import productivity_app.windows.presentation
assert QApplication.instance() is None
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    forbidden_imports = (
        "productivity_app.db",
        "productivity_app.repositories",
        "productivity_app.services",
        "productivity_app.bootstrap",
    )
    for path in (root / "src/productivity_app/desktop/ui").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden_imports)
            elif isinstance(node, ast.Import):
                assert all(not item.name.startswith(forbidden_imports) for item in node.names)
        # Final UI supersedes the preview-only ban with one bounded worker
        # pool and one display-only clock. All other boundary bans stay locked.
        if path.name != "timer_display.py":
            assert "QTimer" not in source
        if path.name != "operations.py":
            assert "QThread" not in source
        assert all(
            token not in source
            for token in (
                "showMessage(",
                "os.startfile",
                "QFileSystemModel",
                "QDesktopServices",
                "QGraphicsBlurEffect",
                "create_all(",
                "drop_all(",
            )
        )
    expected = {
        "db/models/initial.py": "BA10C2C9C3F9F2D90FC30654EC130AC1A76C98393B7FB867B490BF8049FB6198",
        "db/migrations/versions/20260924_0001_initial_schema.py": "1B304C57C0D531942ED0021608487D14A4BBC4023ECDC62EEB49EA9E6916EFB8",
    }
    for relative, digest in expected.items():
        assert (
            hashlib.sha256((root / "src/productivity_app" / relative).read_bytes())
            .hexdigest()
            .upper()
            == digest
        )
