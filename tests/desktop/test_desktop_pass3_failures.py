"""Deterministic Pass 3 lifecycle and platform-failure proofs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from productivity_app.desktop.controller import QuitResult, ShellController
from productivity_app.desktop.startup import StartupState


@dataclass
class _Settings:
    global_hotkey: str = "Ctrl+Alt+Space"
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    version: int = 1


class _SettingsService:
    def __init__(self) -> None:
        self.value, self.writes = _Settings(), 0

    def get_settings(self) -> _Settings:
        return self.value

    def update_settings(self, patch: Any) -> _Settings:
        self.writes += 1
        if patch.global_hotkey is not None:
            self.value.global_hotkey = patch.global_hotkey
        self.value.version += 1
        return self.value


class _Backend:
    def __init__(self, *, close_hook: Any = None, failures: int = 0) -> None:
        self.settings = _SettingsService()
        self.close_hook, self.failures, self.close_count = close_hook, failures, 0

    def close(self) -> None:
        self.close_count += 1
        if self.close_hook is not None:
            self.close_hook()
        if self.close_count <= self.failures:
            raise RuntimeError("backend busy")


class _Window:
    def __init__(self, *, hide_error: Exception | None = None) -> None:
        self.hidden, self.minimized = False, False
        self.show_count = self.raise_count = self.activate_count = self.hide_count = 0
        self.hide_error = hide_error

    def isHidden(self) -> bool:
        return self.hidden

    def isMinimized(self) -> bool:
        return self.minimized

    def show(self) -> None:
        self.hidden = False
        self.show_count += 1

    def showNormal(self) -> None:
        self.hidden = self.minimized = False

    def raise_(self) -> None:
        self.raise_count += 1

    def activateWindow(self) -> None:
        self.activate_count += 1

    def hide(self) -> None:
        self.hide_count += 1
        if self.hide_error is not None:
            raise self.hide_error
        self.hidden = True


class _Tray:
    available = True

    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_count = self.show_count = 0
        self.close_error = close_error

    def show(self) -> None:
        self.show_count += 1

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error

    def set_startup_state(self, **_: Any) -> None:
        return None


class _Closeable:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.close_count = 0
        self.error = error

    def close(self) -> None:
        self.close_count += 1
        if self.error is not None:
            raise self.error


class _Hotkey:
    cleanup_pending = False

    def __init__(self, *, error: Exception | None = None) -> None:
        self.close_count = self.replace_count = 0
        self.error = error

    def close(self) -> None:
        self.close_count += 1
        if self.error is not None:
            raise self.error

    def replace(self, _candidate: Any, _persist: Any) -> None:
        self.replace_count += 1


class _Startup:
    def __init__(self) -> None:
        self.calls = 0

    def inspect(self) -> Any:
        return type("Status", (), {"state": StartupState.DISABLED})()

    def set_enabled(self, _enabled: bool, _version: int, *, repair: bool = False) -> Any:
        self.calls += 1
        return self.inspect()


class _QtSignal:
    def connect(self, _callback: Any) -> None:
        return None


class _Qt:
    def __init__(self) -> None:
        self.aboutToQuit = _QtSignal()
        self.quit_count = 0

    def setQuitOnLastWindowClosed(self, _value: bool) -> None:
        return None

    def quit(self) -> None:
        self.quit_count += 1


def _build(
    *,
    backend: _Backend | None = None,
    window: _Window | None = None,
    tray: _Tray | None = None,
    hotkey: _Hotkey | None = None,
    server: _Closeable | None = None,
    mutex: _Closeable | None = None,
    startup: _Startup | None = None,
) -> tuple[ShellController, _Backend, _Window, _Tray, _Hotkey, _Closeable, _Closeable, _Qt]:
    backend, window, tray, hotkey = (
        backend or _Backend(),
        window or _Window(),
        tray or _Tray(),
        hotkey or _Hotkey(),
    )
    server, mutex, qt = server or _Closeable(), mutex or _Closeable(), _Qt()
    controller = ShellController(
        backend,
        cast(Any, qt),
        cast(Any, mutex),
        cast(Any, server),
        window=cast(Any, window),
        tray=cast(Any, tray),
        hotkey_manager=cast(Any, hotkey),
        startup_coordinator=cast(Any, startup),
    )
    return controller, backend, window, tray, hotkey, server, mutex, qt


def test_quiescing_blocks_every_shell_mutation_before_backend_close_completes() -> None:
    holder: dict[str, Any] = {}
    startup = _Startup()

    def during_close() -> None:
        controller: ShellController = holder["controller"]
        controller.show_window()
        controller.hide_window()
        controller.change_hotkey("Ctrl+Shift+F1", 1)
        controller.set_start_with_windows(True, 1)
        controller._tray_startup_toggle()

    backend = _Backend(close_hook=during_close)
    controller, _backend, window, _tray, hotkey, _server, _mutex, _qt = _build(
        backend=backend, startup=startup
    )
    holder["controller"] = controller
    assert controller.request_quit() is QuitResult.SUCCESS
    assert (window.show_count, window.hide_count, hotkey.replace_count, startup.calls) == (
        0,
        1,
        0,
        0,
    )


def test_failed_backend_close_returns_full_shell_to_running_and_retry_closes_once() -> None:
    controller, backend, window, tray, hotkey, server, mutex, qt = _build(
        backend=_Backend(failures=2), startup=_Startup()
    )
    assert controller.request_quit() is QuitResult.REJECTED
    assert controller.request_quit() is QuitResult.REJECTED
    assert not controller.is_closed and not controller.is_quiescing
    controller.show_window()
    assert (
        window.activate_count == 1
        and tray.close_count == hotkey.close_count == server.close_count == mutex.close_count == 0
    )
    assert controller.request_quit() is QuitResult.SUCCESS
    assert backend.close_count == 3 and qt.quit_count == 1
    assert tray.close_count == hotkey.close_count == server.close_count == mutex.close_count == 1


@pytest.mark.parametrize("failing", ["hotkey", "server", "tray", "window", "mutex"])
def test_post_backend_close_cleanup_attempts_every_stage_and_is_irrevocable(failing: str) -> None:
    values: dict[str, Any] = {
        "hotkey": _Hotkey(),
        "server": _Closeable(),
        "tray": _Tray(),
        "window": _Window(),
        "mutex": _Closeable(),
    }
    values[failing].error = (
        RuntimeError(f"{failing} cleanup failed") if failing != "window" else None
    )
    if failing == "window":
        values["window"] = _Window(hide_error=RuntimeError("window cleanup failed"))
    controller, backend, _window, tray, hotkey, server, mutex, qt = _build(
        backend=_Backend(),
        window=values["window"],
        tray=values["tray"],
        hotkey=values["hotkey"],
        server=values["server"],
        mutex=values["mutex"],
    )
    assert controller.request_quit() is QuitResult.SUCCESS
    assert backend.close_count == qt.quit_count == 1 and controller.is_closed
    assert tray.close_count == hotkey.close_count == server.close_count == mutex.close_count == 1
    assert controller.request_quit() is QuitResult.ALREADY_CLOSED


def test_about_to_quit_needs_approved_shutdown_and_is_idempotent() -> None:
    controller, backend, _window, tray, hotkey, server, mutex, _qt = _build(
        backend=_Backend(failures=1)
    )
    controller._about_to_quit()
    assert (
        backend.close_count
        == tray.close_count
        == hotkey.close_count
        == server.close_count
        == mutex.close_count
        == 0
    )
    assert controller.request_quit() is QuitResult.REJECTED
    assert controller.request_quit() is QuitResult.SUCCESS
    controller._about_to_quit()
    controller._about_to_quit()
    assert (
        backend.close_count == 2
        and tray.close_count == hotkey.close_count == server.close_count == mutex.close_count == 1
    )
