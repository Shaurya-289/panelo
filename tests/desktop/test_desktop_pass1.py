from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

from productivity_app.desktop.controller import QuitResult, ShellController
from productivity_app.desktop.errors import PlatformNotSupportedError, SingleInstanceError
from productivity_app.desktop.instance import (
    ActivationResponse,
    ActivationServer,
    activation_server_name,
    parse_activation_request,
)
from productivity_app.desktop.main import DesktopOptions, parse_desktop_options, run_desktop
from productivity_app.desktop.tray import ShellTray
from productivity_app.desktop.window import ShellWindow
from productivity_app.windows.mutex import MutexLease, build_instance_identity


@dataclass
class _Settings:
    minimize_to_tray: bool = True


class _SettingsService:
    def __init__(self, settings: _Settings) -> None:
        self._settings = settings

    def get_settings(self) -> _Settings:
        return self._settings


class _Backend:
    def __init__(self, *, minimize_to_tray: bool = True, close_error: Exception | None = None) -> None:
        self.settings = _SettingsService(_Settings(minimize_to_tray))
        self.close_error = close_error
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class _Mutex:
    def __init__(self, primary: bool = True) -> None:
        self.primary = primary
        self.closed = False
        self.close_count = 0

    def close(self) -> None:
        self.closed = True
        self.close_count += 1


class _Window:
    def __init__(self) -> None:
        self.hidden = True
        self.minimized = False
        self.show_count = 0
        self.normal_count = 0
        self.raise_count = 0
        self.activate_count = 0

    def isHidden(self) -> bool:
        return self.hidden

    def isMinimized(self) -> bool:
        return self.minimized

    def show(self) -> None:
        self.hidden = False
        self.show_count += 1

    def showNormal(self) -> None:
        self.hidden = False
        self.minimized = False
        self.normal_count += 1

    def raise_(self) -> None:
        self.raise_count += 1

    def activateWindow(self) -> None:
        self.activate_count += 1

    def hide(self) -> None:
        self.hidden = True


class _Tray:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.show_count = 0
        self.hide_count = 0
        self.close_count = 0

    def show(self) -> None:
        self.show_count += 1

    def hide(self) -> None:
        self.hide_count += 1

    def close(self) -> None:
        self.close_count += 1


def _controller(
    qapplication: QApplication,
    *,
    minimize_to_tray: bool = True,
    tray_available: bool = True,
    close_error: Exception | None = None,
) -> tuple[ShellController, _Backend, _Window, _Tray, _Mutex]:
    backend = _Backend(minimize_to_tray=minimize_to_tray, close_error=close_error)
    window, tray, mutex = _Window(), _Tray(tray_available), _Mutex()
    controller = ShellController(
        backend,
        qapplication,
        cast(MutexLease, mutex),
        window=cast(Any, window),
        tray=cast(Any, tray),
    )
    return controller, backend, window, tray, mutex


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], DesktopOptions(startup=False, show=False)),
        (["--startup"], DesktopOptions(startup=True, show=False)),
        (["--show"], DesktopOptions(startup=False, show=True)),
        (["--startup", "--show"], DesktopOptions(startup=True, show=True)),
    ],
)
def test_desktop_cli_accepts_only_the_locked_launch_flags(argv: list[str], expected: DesktopOptions) -> None:
    assert parse_desktop_options(argv) == expected


def test_desktop_cli_rejects_unknown_options() -> None:
    with pytest.raises(SystemExit):
        parse_desktop_options(["--not-a-command"])


def test_instance_identity_is_stable_opaque_and_database_specific(tmp_path: Path) -> None:
    first = build_instance_identity("S-1-5-21-test", tmp_path / "one.db")
    assert first == build_instance_identity("S-1-5-21-test", tmp_path / "." / "one.db")
    assert first != build_instance_identity("S-1-5-21-test", tmp_path / "two.db")
    assert "one.db" not in first
    assert "S-1-5-21-test" not in first
    assert activation_server_name(first).endswith("-activate")


@pytest.mark.parametrize(
    ("payload", "accepted"),
    [
        (b"ACTIVATE 1\n", True),
        (b"ACTIVATE 2\n", False),
        (b"ACTIVATE 1", False),
        (b"ACTIVATE 1\nACTIVATE 1\n", False),
        (b"\xff", False),
        (b"x" * 65, False),
    ],
)
def test_activation_protocol_is_exact_and_bounded(payload: bytes, accepted: bool) -> None:
    assert parse_activation_request(payload) is accepted


def test_real_local_activation_ipc_routes_one_request(qapplication: QApplication) -> None:
    calls: list[str] = []
    server = ActivationServer(
        f"phase6-test-{uuid4().hex}", lambda: calls.append("activate") or ActivationResponse.OK
    )
    server.start()
    code = (
        "import os; os.environ['QT_QPA_PLATFORM'] = 'offscreen'; "
        "from PySide6.QtWidgets import QApplication; app = QApplication([]); "
        "from productivity_app.desktop.instance import request_activation; "
        f"request_activation({server.server_name!r}, startup_timeout_ms=1_000, attempt_timeout_ms=500)"
    )
    child = subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy())
    loop = QEventLoop()
    completion = QTimer()
    completion.setInterval(10)
    completion.timeout.connect(lambda: loop.quit() if child.poll() is not None else None)
    completion.start()
    QTimer.singleShot(2_000, loop.quit)
    loop.exec()
    completion.stop()
    child.wait(timeout=1)
    server.close()
    assert child.returncode == 0
    assert calls == ["activate"]


def _request_parts(qapplication: QApplication, server: ActivationServer, parts: list[bytes]) -> bytes:
    socket = QLocalSocket()
    response: list[bytes] = []
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)

    def write_next(index: int = 0) -> None:
        if index >= len(parts):
            return
        socket.write(parts[index])
        QTimer.singleShot(1, lambda: write_next(index + 1))

    def receive() -> None:
        response.append(bytes(socket.readAll().data()))
        loop.quit()

    socket.connected.connect(write_next)
    socket.readyRead.connect(receive)
    socket.connectToServer(server.server_name)
    timeout.start(1_000)
    loop.exec()
    timeout.stop()
    socket.abort()
    return b"".join(response)


@pytest.mark.parametrize(
    "parts",
    [
        [b"ACTI", b"VATE 1\n"],
        [bytes([byte]) for byte in b"ACTIVATE 1\n"],
        [b"ACTIVATE 1\n"],
    ],
)
def test_activation_server_buffers_fragmented_valid_requests(
    qapplication: QApplication, parts: list[bytes]
) -> None:
    calls: list[str] = []
    server = ActivationServer(
        f"phase6-buffered-{uuid4().hex}", lambda: calls.append("activate") or ActivationResponse.OK
    )
    server.start()
    try:
        assert _request_parts(qapplication, server, parts) == b"OK\n"
    finally:
        server.close()
    assert calls == ["activate"]


@pytest.mark.parametrize(
    "parts",
    [
        [b"ACTI", b"VATE 2\n"],
        [b"x" * 65],
        [b"ACTIVATE 1\nACTIVATE 1\n"],
    ],
)
def test_activation_server_rejects_invalid_or_multiple_buffered_commands(
    qapplication: QApplication, parts: list[bytes]
) -> None:
    calls: list[str] = []
    server = ActivationServer(
        f"phase6-invalid-{uuid4().hex}", lambda: calls.append("activate") or ActivationResponse.OK
    )
    server.start()
    try:
        assert _request_parts(qapplication, server, parts) == b"ERROR\n"
    finally:
        server.close()
    assert calls == []


def test_activation_client_retries_busy_primary_until_ready(qapplication: QApplication) -> None:
    responses = [ActivationResponse.BUSY, ActivationResponse.OK]
    calls: list[ActivationResponse] = []

    def activate() -> ActivationResponse:
        response = responses.pop(0)
        calls.append(response)
        return response

    server = ActivationServer(f"phase6-ready-{uuid4().hex}", activate)
    server.start()
    try:
        from productivity_app.desktop.instance import request_activation

        request_activation(server.server_name, startup_timeout_ms=1_000, retry_interval_ms=5, attempt_timeout_ms=100)
    finally:
        server.close()
    assert calls == [ActivationResponse.BUSY, ActivationResponse.OK]


def test_activation_client_fails_after_bounded_not_ready_wait(qapplication: QApplication) -> None:
    server = ActivationServer(f"phase6-busy-{uuid4().hex}", lambda: ActivationResponse.BUSY)
    server.start()
    try:
        from productivity_app.desktop.instance import request_activation

        with pytest.raises(SingleInstanceError):
            request_activation(server.server_name, startup_timeout_ms=50, retry_interval_ms=5, attempt_timeout_ms=20)
    finally:
        server.close()


def test_controller_show_hide_and_restore_use_one_central_path(qapplication: QApplication) -> None:
    controller, _backend, window, _tray, _mutex = _controller(qapplication)
    controller.show_window()
    assert window.show_count == window.raise_count == window.activate_count == 1
    window.minimized = True
    controller.activate_window()
    assert window.normal_count == 1
    controller.hide_window()
    assert window.hidden is True


def test_startup_visibility_uses_tray_and_current_minimize_policy(qapplication: QApplication) -> None:
    hidden, _backend, hidden_window, hidden_tray, _mutex = _controller(qapplication)
    hidden.start(start_hidden=True)
    assert hidden_window.hidden is True
    assert hidden_window.show_count == 0
    assert hidden_tray.show_count == 1

    visible, _backend, visible_window, visible_tray, _mutex = _controller(qapplication)
    visible.start(start_hidden=False)
    assert visible_window.hidden is False
    assert visible_window.show_count == 1
    assert visible_tray.show_count == 1


def test_window_policy_hides_only_when_setting_and_tray_allow_it(qapplication: QApplication) -> None:
    controller, backend, window, _tray, _mutex = _controller(qapplication)
    assert controller._on_window_close() is True
    assert window.hidden is True
    controller._on_window_minimized()
    assert backend.close_count == 0

    fake_qapp = _QtApplication()
    unavailable, unavailable_backend, _window, _tray, _mutex = _controller(
        cast(Any, fake_qapp), tray_available=False
    )
    assert unavailable._on_window_close() is False
    assert unavailable_backend.close_count == 1

    ordinary, ordinary_backend, _window, _tray, _mutex = _controller(
        cast(Any, _QtApplication()), minimize_to_tray=False
    )
    assert ordinary._on_window_close() is False
    assert ordinary_backend.close_count == 1


def test_quit_closes_backend_once_and_releases_mutex_last(qapplication: QApplication) -> None:
    controller, backend, _window, tray, mutex = _controller(cast(Any, _QtApplication()))
    controller.quit_application()
    controller.quit_application()
    assert backend.close_count == 1
    assert tray.close_count == 1
    assert mutex.closed is True
    assert controller.is_closed is True


def test_close_failure_leaves_shell_usable(qapplication: QApplication) -> None:
    controller, backend, window, tray, mutex = _controller(qapplication, close_error=RuntimeError("busy"))
    controller.quit_application()
    assert backend.close_count == 1
    assert controller.is_quiescing is False
    assert controller.is_closed is False
    assert tray.close_count == 0
    assert mutex.closed is False
    controller.show_window()
    assert window.activate_count == 1


def test_real_window_close_is_ignored_when_backend_shutdown_is_rejected(qapplication: QApplication) -> None:
    controller, backend, _window, tray, mutex = _controller(
        qapplication, tray_available=False, close_error=RuntimeError("active work")
    )
    window = ShellWindow(controller._on_window_close, controller._on_window_minimized)
    controller.window = window
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False
    assert backend.close_count == 1
    assert mutex.closed is False
    assert tray.close_count == 0
    controller.show_window()
    assert window.isVisible() is True


class _Signal:
    def __init__(self) -> None:
        self.callback: Any | None = None

    def connect(self, callback: Any) -> None:
        self.callback = callback


class _QtApplication:
    def __init__(self) -> None:
        self.aboutToQuit = _Signal()
        self.quit_count = 0

    def setQuitOnLastWindowClosed(self, _value: bool) -> None:
        return None

    def quit(self) -> None:
        self.quit_count += 1


def test_quit_is_preflight_gated_and_about_to_quit_cleanup_is_idempotent() -> None:
    qapp = _QtApplication()
    backend = _Backend()
    mutex = _Mutex()
    window, tray = _Window(), _Tray()
    controller = ShellController(
        backend,
        cast(Any, qapp),
        cast(MutexLease, mutex),
        window=cast(Any, window),
        tray=cast(Any, tray),
    )
    assert controller.request_quit() is QuitResult.SUCCESS
    assert qapp.quit_count == 1
    assert backend.close_count == 1
    controller._about_to_quit()
    controller._about_to_quit()
    assert backend.close_count == 1
    assert mutex.close_count == 1

    rejected_qapp = _QtApplication()
    rejected_backend = _Backend(close_error=RuntimeError("busy"))
    rejected = ShellController(
        rejected_backend,
        cast(Any, rejected_qapp),
        cast(MutexLease, _Mutex()),
        window=cast(Any, _Window()),
        tray=cast(Any, _Tray()),
    )
    assert rejected.request_quit() is QuitResult.REJECTED
    assert rejected_qapp.quit_count == 0
    assert rejected_backend.close_count == 1
    rejected._about_to_quit()
    assert rejected_backend.close_count == 1


def test_shell_tray_menu_routes_actions_once_and_never_uses_messages(qapplication: QApplication) -> None:
    calls: list[str] = []
    tray = ShellTray(lambda: calls.append("open"), lambda: calls.append("hide"), lambda: calls.append("quit"))
    tray.open_action.trigger()
    tray.hide_action.trigger()
    tray.quit_action.trigger()
    assert calls == ["open", "hide", "quit"]
    assert tray.startup_action.isEnabled() is False
    assert "showMessage" not in ShellTray.__dict__
    tray.close()


class _MutexBackend:
    def __init__(self, mutex: _Mutex) -> None:
        self.mutex = mutex
        self.identities: list[str] = []

    def acquire(self, identity: str) -> _Mutex:
        self.identities.append(identity)
        return self.mutex


class _Server:
    instances: list[_Server] = []
    fail_start = False

    def __init__(self, _name: str, callback: Any) -> None:
        self.callback = callback
        self.closed = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        if self.__class__.fail_start:
            raise SingleInstanceError("server failed")

    def close(self) -> None:
        self.closed = True


class _CleanupServer(_Server):
    close_error: Exception | None = None

    def close(self) -> None:
        self.closed = True
        if self.__class__.close_error is not None:
            raise self.__class__.close_error


class _CleanupMutex(_Mutex):
    def __init__(self, primary: bool = True, close_error: Exception | None = None) -> None:
        super().__init__(primary)
        self.close_error = close_error

    def close(self) -> None:
        super().close()
        if self.close_error is not None:
            raise self.close_error


def test_secondary_launch_activates_without_creating_backend(qapplication: QApplication, monkeypatch, app_config) -> None:
    from productivity_app.desktop import main as desktop_main

    created: list[object] = []
    activated: list[str] = []
    mutex = _Mutex(primary=False)
    monkeypatch.setattr(desktop_main, "ActivationServer", _Server)
    result = run_desktop(
        ["--startup"],
        config=app_config,
        backend_factory=lambda _config: created.append(object()),  # type: ignore[return-value]
        mutex_backend=cast(Any, _MutexBackend(mutex)),
        sid_provider=lambda: "S-1-5-21-test",
        activation_sender=lambda name: activated.append(name),
        qapplication=qapplication,
        execute=False,
    )
    assert result == 0
    assert created == []
    assert len(activated) == 1
    assert mutex.closed is True


def test_secondary_ipc_failure_fails_closed_without_creating_backend(
    qapplication: QApplication, monkeypatch, app_config
) -> None:
    from productivity_app.desktop import main as desktop_main

    created: list[object] = []
    mutex = _Mutex(primary=False)
    monkeypatch.setattr(desktop_main, "ActivationServer", _Server)

    def unreachable(_name: str) -> None:
        raise SingleInstanceError("primary activation endpoint is unavailable")

    assert run_desktop(
        [],
        config=app_config,
        backend_factory=lambda _config: created.append(object()),  # type: ignore[return-value]
        mutex_backend=cast(Any, _MutexBackend(mutex)),
        sid_provider=lambda: "S-1-5-21-test",
        activation_sender=unreachable,
        qapplication=qapplication,
        execute=False,
    ) == 1
    assert created == []
    assert mutex.closed is True


def test_primary_starts_ipc_before_backend_and_cleans_up_start_failure(qapplication: QApplication, monkeypatch, app_config) -> None:
    from productivity_app.desktop import main as desktop_main

    calls: list[str] = []
    mutex = _Mutex(primary=True)
    _Server.instances.clear()
    _Server.fail_start = True
    monkeypatch.setattr(desktop_main, "ActivationServer", _Server)
    try:
        assert run_desktop(
            [],
            config=app_config,
            backend_factory=lambda _config: calls.append("backend"),  # type: ignore[return-value]
            mutex_backend=cast(Any, _MutexBackend(mutex)),
            sid_provider=lambda: "S-1-5-21-test",
            qapplication=qapplication,
            execute=False,
        ) == 1
    finally:
        _Server.fail_start = False
    assert calls == []
    assert mutex.closed is True


@pytest.mark.parametrize("failure", ["mutex", "backend", "controller"])
def test_expected_startup_failures_return_controlled_status(
    qapplication: QApplication, monkeypatch, app_config, failure: str
) -> None:
    from productivity_app.core.exceptions import DatabaseError
    from productivity_app.desktop import main as desktop_main

    created: list[str] = []
    mutex = _Mutex(primary=True)
    if failure == "mutex":
        class _BrokenMutexBackend:
            def acquire(self, _identity: str) -> _Mutex:
                raise SingleInstanceError("mutex unavailable")

        backend = cast(Any, _BrokenMutexBackend())
    else:
        backend = cast(Any, _MutexBackend(mutex))
    monkeypatch.setattr(desktop_main, "ActivationServer", _Server)
    if failure == "controller":
        class _BrokenController:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("tray initialization failed")

        monkeypatch.setattr(desktop_main, "ShellController", _BrokenController)

    def create(_config: Any) -> Any:
        created.append("backend")
        if failure == "backend":
            raise DatabaseError("database unavailable")
        return _Backend()

    assert run_desktop(
        [],
        config=app_config,
        backend_factory=create,
        mutex_backend=backend,
        sid_provider=lambda: "S-1-5-21-test",
        qapplication=qapplication,
        execute=False,
    ) == 1
    if failure == "mutex":
        assert created == []
    else:
        assert mutex.closed is True


def test_startup_cleanup_attempts_all_resources_after_backend_close_failure(
    qapplication: QApplication, monkeypatch, app_config, caplog: pytest.LogCaptureFixture
) -> None:
    from productivity_app.desktop import main as desktop_main

    backend = _Backend(close_error=RuntimeError("backend cleanup failed"))
    mutex = _CleanupMutex(close_error=RuntimeError("mutex cleanup failed"))
    _CleanupServer.instances.clear()
    _CleanupServer.close_error = RuntimeError("ipc cleanup failed")
    monkeypatch.setattr(desktop_main, "ActivationServer", _CleanupServer)

    class _BrokenController:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("controller failed")

    monkeypatch.setattr(desktop_main, "ShellController", _BrokenController)
    try:
        with caplog.at_level(logging.ERROR, logger="productivity_app.desktop"):
            assert run_desktop(
                [],
                config=app_config,
                backend_factory=lambda _config: cast(Any, backend),
                mutex_backend=cast(Any, _MutexBackend(mutex)),
                sid_provider=lambda: "S-1-5-21-test",
                qapplication=qapplication,
                execute=False,
            ) == 1
    finally:
        _CleanupServer.close_error = None
    assert backend.close_count == 1
    assert _CleanupServer.instances[0].closed is True
    assert mutex.closed is True
    assert any(record.exc_info for record in caplog.records if record.message.startswith("Desktop startup failed"))


def test_primary_starts_ipc_then_creates_backend_and_releases_ownership_on_quit(
    qapplication: QApplication, monkeypatch, app_config
) -> None:
    from productivity_app.desktop import main as desktop_main

    calls: list[str] = []
    mutex = _Mutex(primary=True)
    _Server.instances.clear()
    monkeypatch.setattr(desktop_main, "ActivationServer", _Server)
    original_controller = desktop_main.ShellController
    controllers: list[ShellController] = []

    def capture_controller(*args: Any, **kwargs: Any) -> ShellController:
        controller = original_controller(*args, **kwargs)
        controllers.append(controller)
        return controller

    monkeypatch.setattr(desktop_main, "ShellController", capture_controller)
    backend = _Backend()
    result = run_desktop(
        [],
        config=app_config,
        backend_factory=lambda _config: calls.append("backend") or cast(Any, backend),
        mutex_backend=cast(Any, _MutexBackend(mutex)),
        sid_provider=lambda: "S-1-5-21-test",
        qapplication=qapplication,
        execute=False,
    )
    assert controllers[0].request_quit() is QuitResult.SUCCESS
    assert result == 0
    assert calls == ["backend"]
    assert backend.close_count == 1
    assert _Server.instances[0].closed is True
    assert mutex.closed is True


def test_windows_adapter_is_import_safe_and_non_windows_calls_fail_lazily() -> None:
    if __import__("sys").platform == "win32":
        pytest.skip("The real Windows adapter is covered by the integration test.")
    from productivity_app.windows.mutex import WindowsMutexBackend

    with pytest.raises(PlatformNotSupportedError):
        WindowsMutexBackend().acquire("opaque")
