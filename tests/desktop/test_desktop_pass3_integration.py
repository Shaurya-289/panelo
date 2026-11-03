"""Pass 3 integration proofs for one desktop shell around swappable runtimes."""

from __future__ import annotations

import gc
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4
from weakref import ref

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtNetwork import QLocalSocket

from productivity_app.core.events import DomainEventType
from productivity_app.core.exceptions import ApplicationLifecycleError
from productivity_app.desktop.controller import ShellController
from productivity_app.desktop.hotkey import HotkeyManager
from productivity_app.desktop.instance import ActivationResponse, ActivationServer
from productivity_app.desktop.tray import ShellTray
from productivity_app.schemas.backups import RestoreBackup
from productivity_app.schemas.tasks import CreateTask


@dataclass
class _Settings:
    global_hotkey: str = "Ctrl+Alt+Space"
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    version: int = 1


class _SettingsService:
    def __init__(self) -> None:
        self.value = _Settings()

    def get_settings(self) -> _Settings:
        return self.value

    def update_settings(self, patch: Any) -> _Settings:
        if patch.global_hotkey is not None:
            self.value.global_hotkey = patch.global_hotkey
        if patch.start_with_windows is not None:
            self.value.start_with_windows = patch.start_with_windows
        self.value.version += 1
        return self.value


class _Application:
    def __init__(self) -> None:
        self.settings = _SettingsService()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _Window:
    def __init__(self) -> None:
        self.hidden, self.minimized = True, False
        self.show_count = self.normal_count = self.raise_count = self.activate_count = (
            self.hide_count
        ) = 0

    def winId(self) -> int:
        return 12345

    def isHidden(self) -> bool:
        return self.hidden

    def isMinimized(self) -> bool:
        return self.minimized

    def show(self) -> None:
        self.hidden = False
        self.show_count += 1

    def showNormal(self) -> None:
        self.hidden = self.minimized = False
        self.normal_count += 1

    def raise_(self) -> None:
        self.raise_count += 1

    def activateWindow(self) -> None:
        self.activate_count += 1

    def hide(self) -> None:
        self.hidden = True
        self.hide_count += 1


class _Tray:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.show_count = self.close_count = 0
        self.startup_states: list[tuple[bool, bool]] = []

    def show(self) -> None:
        self.show_count += 1

    def close(self) -> None:
        self.close_count += 1

    def set_startup_state(self, *, enabled: bool, checked: bool) -> None:
        self.startup_states.append((enabled, checked))


class _Mutex:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _HotkeyBackend:
    def __init__(self) -> None:
        self.registered: set[int] = set()
        self.register_calls: list[int] = []
        self.unregister_calls: list[int] = []

    def register(self, _hwnd: int, identifier: int, _modifiers: int, _key: int) -> None:
        self.register_calls.append(identifier)
        self.registered.add(identifier)

    def unregister(self, _hwnd: int, identifier: int) -> None:
        self.unregister_calls.append(identifier)
        self.registered.discard(identifier)


class _CountingShellController(ShellController):
    """Makes the otherwise private Qt shutdown connection observable in tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.about_to_quit_calls = 0
        super().__init__(*args, **kwargs)

    def _about_to_quit(self) -> None:
        self.about_to_quit_calls += 1
        super()._about_to_quit()


class _TrayCallbacks:
    def __init__(self, cycle: int, calls: list[tuple[int, str]]) -> None:
        self._cycle, self._calls = cycle, calls

    def open(self) -> None:
        self._calls.append((self._cycle, "open"))

    def hide(self) -> None:
        self._calls.append((self._cycle, "hide"))

    def quit(self) -> None:
        self._calls.append((self._cycle, "quit"))

    def toggle_startup(self) -> None:
        self._calls.append((self._cycle, "startup"))


def _controller(qapplication: Any) -> tuple[ShellController, _Application, _Window, _Tray, _Mutex]:
    app, window, tray, mutex = _Application(), _Window(), _Tray(), _Mutex()
    controller = ShellController(
        app, qapplication, cast(Any, mutex), window=cast(Any, window), tray=cast(Any, tray)
    )
    return controller, app, window, tray, mutex


def _activation_request(server: ActivationServer) -> bytes:
    socket, loop, timeout = QLocalSocket(), QEventLoop(), QTimer()
    reply: list[bytes] = []
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    socket.connected.connect(lambda: socket.write(b"ACTIVATE 1\n"))
    socket.readyRead.connect(lambda: reply.append(bytes(socket.readAll().data())) or loop.quit())
    socket.disconnected.connect(loop.quit)
    socket.connectToServer(server.server_name)
    timeout.start(1_000)
    loop.exec()
    timeout.stop()
    socket.abort()
    return b"".join(reply)


def test_twenty_five_real_trays_release_callbacks_without_cross_cycle_delivery(qapplication: Any) -> None:
    _assert_twenty_five_real_trays_release_callbacks_without_cross_cycle_delivery(qapplication)


def test_real_restore_preserves_one_live_activation_server_and_routes_once(
    application: Any, qapplication: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_real_restore_preserves_one_live_activation_server_and_routes_once(
        application, qapplication, monkeypatch
    )


@pytest.mark.parametrize("hidden,minimized", [(False, False), (True, False), (False, True)])
def test_secondary_activation_routes_every_window_state_once(
    qapplication: Any, hidden: bool, minimized: bool
) -> None:
    controller, _app, window, _tray, _mutex = _controller(qapplication)
    window.hidden, window.minimized = hidden, minimized
    server = ActivationServer(
        f"pass3-activation-{uuid4().hex}",
        lambda: controller.activate_window() or ActivationResponse.OK,
    )
    server.start()
    try:
        assert _activation_request(server) == b"OK\n"
    finally:
        server.close()
    assert window.hidden is False and window.minimized is False
    assert window.raise_count == window.activate_count == 1
    assert window.show_count == int(hidden)
    assert window.normal_count == int(minimized)


def test_delayed_primary_endpoint_returns_busy_then_exactly_one_ok(qapplication: Any) -> None:
    ready = False
    calls: list[ActivationResponse] = []

    def activate() -> ActivationResponse:
        calls.append(ActivationResponse.OK if ready else ActivationResponse.BUSY)
        return calls[-1]

    server = ActivationServer(f"pass3-busy-{uuid4().hex}", activate)
    server.start()
    try:
        assert _activation_request(server) == b"BUSY\n"
        ready = True
        assert _activation_request(server) == b"OK\n"
    finally:
        server.close()
    assert calls == [ActivationResponse.BUSY, ActivationResponse.OK]


def test_hotkey_ipc_and_tray_style_actions_share_one_window(qapplication: Any) -> None:
    controller, _app, window, _tray, _mutex = _controller(qapplication)
    backend = _HotkeyBackend()
    manager = HotkeyManager(qapplication, window, backend, controller.show_window)
    manager.start("Ctrl+Alt+Space")
    server = ActivationServer(
        f"pass3-mixed-{uuid4().hex}", lambda: controller.activate_window() or ActivationResponse.OK
    )
    server.start()
    try:
        controller.hide_window()
        manager._activate()
        controller.hide_window()
        assert _activation_request(server) == b"OK\n"
        controller.hide_window()
        controller.show_window()  # the tray Open route uses this same callback
    finally:
        server.close()
        manager.close()
    assert window.hidden is False
    assert window.activate_count == 3
    assert len(backend.register_calls) == 1


def test_mixed_activation_stress_never_replaces_window(qapplication: Any) -> None:
    controller, _app, window, _tray, _mutex = _controller(qapplication)
    identity = id(window)
    for index in range(100):
        if index % 3 == 0:
            controller.hide_window()
        controller.activate_window()
    assert id(controller.window) == identity
    assert window.activate_count == 100


def test_runtime_swap_keeps_shell_resources_and_rejects_old_services(
    application: Any, qapplication: Any
) -> None:
    controller, _shell_backend, window, tray, mutex = _controller(qapplication)
    native_backend = _HotkeyBackend()
    manager = HotkeyManager(qapplication, window, native_backend, controller.show_window)
    controller.attach_platform_integrations(manager, None)
    controller.start(start_hidden=False)
    active_id, filter_ = manager._active_id, manager._filter
    backup = application.backups.create_backup()
    old_runtime, old_settings = application._active_runtime(), application.settings
    registrations, unregistrations = (
        list(native_backend.register_calls),
        list(native_backend.unregister_calls),
    )
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    assert application._active_runtime() is not old_runtime
    assert application.settings is not old_settings
    with pytest.raises(ApplicationLifecycleError):
        old_settings.get_settings()
    assert (
        controller.window,
        controller.tray,
        controller._mutex,
        manager._filter,
        manager._active_id,
    ) == (window, tray, mutex, filter_, active_id)
    assert native_backend.register_calls == registrations
    assert native_backend.unregister_calls == unregistrations
    manager.close()


def test_three_runtime_swaps_keep_one_hotkey_filter_and_current_facade(
    application: Any, qapplication: Any
) -> None:
    controller, _shell_backend, window, _tray, _mutex = _controller(qapplication)
    native_backend = _HotkeyBackend()
    manager = HotkeyManager(qapplication, window, native_backend, controller.show_window)
    controller.attach_platform_integrations(manager, None)
    controller.start(start_hidden=False)
    filter_, active_id = manager._filter, manager._active_id
    backup = application.backups.create_backup()
    old_services: list[Any] = []
    for _ in range(3):
        old_services.append(application.settings)
        prior = application._active_runtime()
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
        )
        assert application._active_runtime() is not prior
        assert manager._filter is filter_ and manager._active_id == active_id
    for service in old_services:
        with pytest.raises(ApplicationLifecycleError):
            service.get_settings()
    assert len(native_backend.registered) == 1
    manager.close()


def test_runtime_swap_does_not_change_shell_visibility(application: Any, qapplication: Any) -> None:
    controller, _shell_backend, window, _tray, _mutex = _controller(qapplication)
    controller.start(start_hidden=True)
    backup = application.backups.create_backup()
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    assert window.hidden is True and window.show_count == 0


def test_tray_availability_loss_does_not_poll_and_ipc_can_restore(qapplication: Any) -> None:
    controller, _app, window, tray, _mutex = _controller(qapplication)
    controller.hide_window()
    tray.available = False
    server = ActivationServer(
        f"pass3-tray-loss-{uuid4().hex}",
        lambda: controller.activate_window() or ActivationResponse.OK,
    )
    server.start()
    try:
        assert _activation_request(server) == b"OK\n"
    finally:
        server.close()
    assert window.hidden is False


def test_activation_server_close_releases_incomplete_pending_socket(qapplication: Any) -> None:
    server = ActivationServer(f"pass3-pending-{uuid4().hex}", lambda: ActivationResponse.OK)
    socket = QLocalSocket()
    server.start()
    try:
        socket.connectToServer(server.server_name)
        socket.waitForConnected(1_000)
        socket.write(b"ACTI")
        socket.flush()
        loop, timeout, observed = QEventLoop(), QTimer(), QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        observed.timeout.connect(lambda: loop.quit() if server._buffers else None)
        timeout.start(1_000)
        observed.start(1)
        loop.exec()
        observed.stop()
        timeout.stop()
        assert server._buffers
        server.close()
        assert not server._buffers
    finally:
        socket.abort()
        server.close()


def test_twenty_five_shell_lifecycle_cycles_release_every_owned_resource(qapplication: Any) -> None:
    active_counts: list[tuple[int, int, int, int]] = []
    for _ in range(25):
        controller, backend, window, tray, mutex = _controller(qapplication)
        native_backend = _HotkeyBackend()
        manager = HotkeyManager(qapplication, window, native_backend, controller.show_window)

        def activate(current: ShellController = controller) -> ActivationResponse:
            current.activate_window()
            return ActivationResponse.OK

        server = ActivationServer(
            f"pass3-cycle-{uuid4().hex}",
            activate,
        )
        server.start()
        controller._activation_server = server
        controller.attach_platform_integrations(manager, None)
        controller.start(start_hidden=False)
        controller.activate_window()
        controller.hide_window()
        controller.activate_window()
        controller.change_hotkey("Ctrl+Alt+Space", backend.settings.get_settings().version)
        assert controller.request_quit().value == "SUCCESS"
        active_counts.append(
            (
                len(native_backend.registered),
                len(manager._registered),
                len(server._buffers),
                mutex.close_count,
            )
        )
        assert backend.close_count == tray.close_count == mutex.close_count == 1
        assert not server.is_listening and not server._buffers and not server._sockets
        assert manager._filter is None and manager._active_id is None and not manager._registered
        assert not native_backend.registered and native_backend.unregister_calls == [0x5041]
        assert id(controller.window) == id(window) and window.hide_count >= 1
    assert active_counts == [(0, 0, 0, 1)] * 25


def test_runtime_event_subscriptions_are_isolated_across_real_restore(application: Any) -> None:
    old_events: list[str] = []
    old_subscription = application.subscribe(
        DomainEventType.TASK_CREATED, lambda event: old_events.append(event.entity_id)
    )
    before = application.tasks.create_task(CreateTask(title="before restore"))
    assert old_events == [before.id]
    backup = application.backups.create_backup()
    old_runtime, old_bus = application._active_runtime(), application._event_bus
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    assert application._active_runtime() is not old_runtime
    current_events: list[str] = []
    current_subscription = application.subscribe(
        DomainEventType.TASK_CREATED, lambda event: current_events.append(event.entity_id)
    )
    after = application.tasks.create_task(CreateTask(title="after restore"))
    assert current_events == [after.id]
    assert old_events == [before.id]
    assert old_bus is not application._event_bus
    old_subscription.close()
    current_subscription.close()


def _assert_real_restore_preserves_one_live_activation_server_and_routes_once(
    application: Any, qapplication: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shell-owned IPC listener must survive backend runtime replacement."""
    window, tray, mutex = _Window(), _Tray(), _Mutex()
    holder: dict[str, ShellController] = {}
    server = ActivationServer(
        f"pass3-live-restore-{uuid4().hex}",
        lambda: holder["controller"].activate_window() or ActivationResponse.OK,
    )
    close_calls = 0
    original_close = server.close

    def close_once() -> None:
        nonlocal close_calls
        close_calls += 1
        original_close()

    monkeypatch.setattr(server, "close", close_once)
    controller = ShellController(
        application,
        qapplication,
        cast(Any, mutex),
        server,
        window=cast(Any, window),
        tray=cast(Any, tray),
    )
    holder["controller"] = controller
    server.start()
    controller.start(start_hidden=False)
    try:
        server_identity, server_name = id(server), server.server_name
        facade, initial_runtime = controller._application, application._active_runtime()
        activation_count = window.activate_count
        assert _activation_request(server) == b"OK\n"
        assert window.activate_count == activation_count + 1

        backup = application.backups.create_backup()
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
        )

        assert application._active_runtime() is not initial_runtime
        assert controller._application is facade is application
        assert id(controller) == id(holder["controller"])
        assert id(controller._activation_server) == server_identity
        assert server.server_name == server_name and server.is_listening
        assert _activation_request(server) == b"OK\n"
        assert window.activate_count == activation_count + 2
        assert not server._buffers and not server._sockets

        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
        )
        assert id(controller._activation_server) == server_identity
        assert server.server_name == server_name and server.is_listening
        assert _activation_request(server) == b"OK\n"
        assert window.activate_count == activation_count + 3
    finally:
        assert controller.request_quit().value == "SUCCESS"
    assert close_calls == 1
    assert not server.is_listening and not server._buffers and not server._sockets


def _assert_twenty_five_real_trays_release_callbacks_without_cross_cycle_delivery(qapplication: Any) -> None:
    """QAction connections are cycle-local and disappear with their closed tray."""
    calls: list[tuple[int, str]] = []
    callback_refs = []
    for cycle in range(25):
        callbacks = _TrayCallbacks(cycle, calls)
        callback_refs.append(ref(callbacks))
        tray = ShellTray(
            callbacks.open,
            callbacks.hide,
            callbacks.quit,
            callbacks.toggle_startup,
        )
        tray.startup_action.setEnabled(True)
        tray.open_action.trigger()
        tray.hide_action.trigger()
        tray.startup_action.trigger()
        tray.quit_action.trigger()
        assert calls[-4:] == [
            (cycle, "open"),
            (cycle, "hide"),
            (cycle, "startup"),
            (cycle, "quit"),
        ]
        tray.close()
        del tray
        del callbacks
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapplication.processEvents()
        gc.collect()
        assert callback_refs[-1]() is None
    assert calls == [
        (cycle, action) for cycle in range(25) for action in ("open", "hide", "startup", "quit")
    ]
    assert all(callback() is None for callback in callback_refs)


def test_twenty_five_shell_cycles_disconnect_old_about_to_quit_callbacks(qapplication: Any) -> None:
    """Only the live shell remains wired to the application shutdown signal."""
    closed: list[tuple[_CountingShellController, _Application, _Tray, _Mutex, _Mutex]] = []
    for _ in range(24):
        backend, window, tray, mutex, server = _Application(), _Window(), _Tray(), _Mutex(), _Mutex()
        controller = _CountingShellController(
            backend,
            qapplication,
            cast(Any, mutex),
            cast(Any, server),
            window=cast(Any, window),
            tray=cast(Any, tray),
        )
        controller.start(start_hidden=False)
        assert controller.request_quit().value == "SUCCESS"
        closed.append((controller, backend, tray, mutex, server))

    live_backend, live_window, live_tray, live_mutex, live_server = (
        _Application(),
        _Window(),
        _Tray(),
        _Mutex(),
        _Mutex(),
    )
    live = _CountingShellController(
        live_backend,
        qapplication,
        cast(Any, live_mutex),
        cast(Any, live_server),
        window=cast(Any, live_window),
        tray=cast(Any, live_tray),
    )
    live.start(start_hidden=False)
    qapplication.aboutToQuit.emit()

    assert live.about_to_quit_calls == 1
    assert all(controller.about_to_quit_calls == 0 for controller, *_rest in closed)
    assert all(
        backend.close_count == tray.close_count == mutex.close_count == server.close_count == 1
        for _controller, backend, tray, mutex, server in closed
    )
    assert live_backend.close_count == live_tray.close_count == live_mutex.close_count == live_server.close_count == 0

    assert live.request_quit().value == "SUCCESS"
    assert live_backend.close_count == live_tray.close_count == live_mutex.close_count == live_server.close_count == 1
