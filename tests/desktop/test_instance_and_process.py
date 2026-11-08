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
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

from panelo.desktop.errors import PlatformNotSupportedError, SingleInstanceError
from panelo.desktop.instance import (
    ActivationResponse,
    ActivationServer,
    activation_server_name,
    parse_activation_request,
)
from panelo.desktop.main import DesktopOptions, parse_desktop_options, run_desktop
from panelo.windows.mutex import build_instance_identity


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
        "from panelo.desktop.instance import request_activation; "
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
        from panelo.desktop.instance import request_activation

        request_activation(server.server_name, startup_timeout_ms=1_000, retry_interval_ms=5, attempt_timeout_ms=100)
    finally:
        server.close()
    assert calls == [ActivationResponse.BUSY, ActivationResponse.OK]


def test_activation_client_fails_after_bounded_not_ready_wait(qapplication: QApplication) -> None:
    server = ActivationServer(f"phase6-busy-{uuid4().hex}", lambda: ActivationResponse.BUSY)
    server.start()
    try:
        from panelo.desktop.instance import request_activation

        with pytest.raises(SingleInstanceError):
            request_activation(server.server_name, startup_timeout_ms=50, retry_interval_ms=5, attempt_timeout_ms=20)
    finally:
        server.close()






















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
    from panelo.desktop import main as desktop_main

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
    from panelo.desktop import main as desktop_main

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
    from panelo.desktop import main as desktop_main

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
    from panelo.core.exceptions import DatabaseError
    from panelo.desktop import main as desktop_main

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
                raise RuntimeError("desktop construction failed")

        monkeypatch.setattr(desktop_main, "build_desktop", _BrokenController)

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
    from panelo.desktop import main as desktop_main

    backend = _Backend(close_error=RuntimeError("backend cleanup failed"))
    mutex = _CleanupMutex(close_error=RuntimeError("mutex cleanup failed"))
    _CleanupServer.instances.clear()
    _CleanupServer.close_error = RuntimeError("ipc cleanup failed")
    monkeypatch.setattr(desktop_main, "ActivationServer", _CleanupServer)

    class _BrokenController:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("controller failed")

    monkeypatch.setattr(desktop_main, "build_desktop", _BrokenController)
    try:
        with caplog.at_level(logging.ERROR, logger="panelo.desktop"):
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




def test_windows_adapter_is_import_safe_and_non_windows_calls_fail_lazily() -> None:
    if __import__("sys").platform == "win32":
        pytest.skip("The real Windows adapter is covered by the integration test.")
    from panelo.windows.mutex import WindowsMutexBackend

    with pytest.raises(PlatformNotSupportedError):
        WindowsMutexBackend().acquire("opaque")


def test_shell_modules_import_without_creating_a_qapplication_or_backend() -> None:

    code = (
        "import os\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "from PySide6.QtWidgets import QApplication\n"
        "assert QApplication.instance() is None\n"
        "import panelo.desktop.hotkey\n"
        "import panelo.desktop.instance\n"
        "import panelo.desktop.main\n"
        "import panelo.desktop.startup\n"
        "assert QApplication.instance() is None\n"
    )
    root = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_injected_config_skips_data_migration_but_honours_injected_legacy_registry(
    qapplication: QApplication, monkeypatch, app_config, tmp_path: Path
) -> None:
    from panelo.desktop import main as desktop_main
    from panelo.ui.selftest.harness import FakeStartupRegistry

    def no_data_migration(*_args: Any) -> Any:
        raise AssertionError("custom configurations must never migrate real per-user data")

    monkeypatch.setattr(desktop_main, "migrate_legacy_data_safely", no_data_migration)
    monkeypatch.setattr(desktop_main, "ActivationServer", _Server)
    built: list[Any] = []

    class _StopAfterBuild:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs)
            raise RuntimeError("stop here")

    monkeypatch.setattr(desktop_main, "build_desktop", _StopAfterBuild)
    launcher = tmp_path / "panelo.exe"
    launcher.write_bytes(b"")
    legacy, current = FakeStartupRegistry(), FakeStartupRegistry()
    legacy.write("old")
    assert run_desktop(
        [],
        config=app_config,
        backend_factory=lambda _config: cast(Any, _Backend()),
        mutex_backend=cast(Any, _MutexBackend(_Mutex(primary=True))),
        sid_provider=lambda: "S-1-5-21-test",
        qapplication=qapplication,
        startup_registry=current,
        startup_launcher=launcher,
        legacy_startup_registry=legacy,
        execute=False,
    ) == 1
    assert legacy.inspect() is None
    assert current.inspect() is not None and "panelo.exe" in str(current.inspect().value)
    assert built and built[0]["startup_registry"] is current
