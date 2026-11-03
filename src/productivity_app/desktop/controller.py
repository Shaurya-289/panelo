"""Central ownership and lifecycle behavior for the desktop shell."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from productivity_app.desktop.hotkey import HotkeyManager, parse_hotkey
from productivity_app.desktop.instance import ActivationServer
from productivity_app.desktop.startup import StartupCoordinator, StartupState
from productivity_app.desktop.tray import ShellTray
from productivity_app.desktop.window import ShellWindow
from productivity_app.windows.mutex import MutexLease


class BackendApplication(Protocol):
    def close(self) -> None: ...

    @property
    def settings(self) -> _SettingsReader: ...


class _SettingsReader(Protocol):
    def get_settings(self) -> _ShellSettings: ...

    def update_settings(self, patch: object) -> object: ...


class _ShellSettings(Protocol):
    minimize_to_tray: bool
    global_hotkey: str
    start_with_windows: bool
    version: int


class QuitResult(StrEnum):
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    ALREADY_CLOSED = "ALREADY_CLOSED"


class ShellController(QObject):
    """Owns process shell resources while retaining only the backend facade."""

    def __init__(
        self,
        application: BackendApplication,
        qapplication: QApplication,
        mutex: MutexLease,
        activation_server: ActivationServer | None = None,
        *,
        window: ShellWindow | None = None,
        tray: ShellTray | None = None,
        hotkey_manager: HotkeyManager | None = None,
        startup_coordinator: StartupCoordinator | None = None,
    ) -> None:
        super().__init__()
        self._application = application
        self._qapplication = qapplication
        self._mutex = mutex
        self._activation_server = activation_server
        self._quiescing = False
        self._closed = False
        self._cleanup_started = False
        self._shutdown_approved = False
        self.status_text = "Ready"
        self.ui: object | None = None
        self.window = window or ShellWindow(self._on_window_close, self._on_window_minimized)
        if isinstance(self.window, ShellWindow):
            self.window.hide_requested.connect(self.request_overlay_hide)
        self.tray = tray or ShellTray(
            self.show_window, self.hide_window, self._tray_quit, self._tray_startup_toggle
        )
        self._hotkey_manager = hotkey_manager
        self._startup_coordinator = startup_coordinator
        self._qapplication.setQuitOnLastWindowClosed(False)
        self._qapplication.aboutToQuit.connect(self._about_to_quit)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def is_quiescing(self) -> bool:
        return self._quiescing

    def start(self, *, start_hidden: bool) -> None:
        self.tray.show()
        if not start_hidden:
            self.show_window()
        self._initialize_platform_integrations()

    def show_window(self) -> None:
        if self._closed or self._quiescing:
            return
        if self.window.isHidden():
            self.window.show()
        if self.window.isMinimized():
            self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if isinstance(self.window, ShellWindow):
            self.window.request_foreground()
            self.window.focus_dashboard()
        self.status_text = "Window active"

    def activate_window(self) -> None:
        self.show_window()

    def toggle_window(self) -> None:
        """User hotkey route only; tray Open and IPC remain show-only."""
        if self._closed or self._quiescing:
            return
        if self.window.isHidden() or self.window.isMinimized():
            self.show_window()
        else:
            self.request_overlay_hide()

    def request_overlay_hide(self) -> None:
        """Explicit hide ignores close preferences but keeps a recovery route."""
        if self._closed or self._quiescing:
            return
        if self.tray.available:
            self.hide_window()
        else:
            self.window.showMinimized()
            self.status_text = "Tray unavailable; window minimized to taskbar"

    def hide_window(self) -> None:
        if self._closed or self._quiescing:
            return
        self.window.hide()
        self.status_text = "Window hidden"

    def request_quit(self) -> QuitResult:
        if self._closed:
            return QuitResult.ALREADY_CLOSED
        if self._quiescing:
            return QuitResult.REJECTED
        if self.ui is not None:
            from typing import Any, cast

            if not cast(Any, self.ui).prepare_quit():
                self.status_text = "Shutdown deferred: resolve drafts or wait for the active action"
                return QuitResult.REJECTED
        self._quiescing = True
        try:
            self._application.close()
        except Exception as exc:
            self._quiescing = False
            self.status_text = f"Shutdown deferred: {exc}"
            if self.ui is not None:
                cast(Any, self.ui).resume_after_rejected_close()
            return QuitResult.REJECTED
        self._shutdown_approved = True
        cleanup_error: Exception | None = None
        try:
            self._close_shell_resources()
        except Exception as exc:
            cleanup_error = exc
        finally:
            # Backend closure is irreversible.  Resource cleanup remains
            # best-effort so one platform fault cannot strand the rest.
            self._closed = True
            self._qapplication.quit()
        if cleanup_error is not None:
            self.status_text = f"Shutdown cleanup incomplete: {cleanup_error}"
        return QuitResult.SUCCESS

    def quit_application(self) -> QuitResult:
        """Compatibility shell command routed through the explicit pre-quit gate."""
        return self.request_quit()

    def attach_platform_integrations(
        self,
        hotkey_manager: HotkeyManager | None,
        startup_coordinator: StartupCoordinator | None,
    ) -> None:
        """Attach optional Pass 2 adapters after the stable shell exists."""
        self._hotkey_manager = hotkey_manager
        self._startup_coordinator = startup_coordinator

    def change_hotkey(self, value: str, expected_version: int) -> None:
        if self._closed or self._quiescing:
            return
        candidate = parse_hotkey(value)
        if self._hotkey_manager is None:
            raise RuntimeError("Global hotkeys are not configured for this shell.")

        def persist(canonical: str) -> None:
            from productivity_app.core.exceptions import StaleVersionError
            from productivity_app.schemas.settings import SettingsPatch

            settings = self._application.settings.get_settings()
            if settings.version != expected_version:
                raise StaleVersionError(
                    "Settings were changed by another operation. Refresh and retry."
                )
            if settings.global_hotkey != canonical:
                self._application.settings.update_settings(
                    SettingsPatch(expected_version=expected_version, global_hotkey=canonical)
                )

        self._hotkey_manager.replace(candidate, persist)
        suffix = (
            "; previous binding cleanup pending" if self._hotkey_manager.cleanup_pending else ""
        )
        self.status_text = f"Global hotkey active: {candidate.canonical}{suffix}"

    def set_start_with_windows(
        self, enabled: bool, expected_version: int, *, repair: bool = False
    ) -> None:
        if self._closed or self._quiescing:
            return
        if self._startup_coordinator is None:
            raise RuntimeError("Windows startup registration is not configured for this shell.")
        status = self._startup_coordinator.set_enabled(enabled, expected_version, repair=repair)
        self._apply_startup_status(status.state)
        self.status_text = f"Start with Windows: {status.state.value.lower()}"

    def _tray_quit(self) -> None:
        self.request_quit()

    def _tray_startup_toggle(self) -> None:
        if self._closed or self._quiescing:
            return
        if self._startup_coordinator is None:
            return
        if self.ui is not None:
            from typing import Any, cast

            cast(Any, self.ui).toggle_startup()
            return
        try:
            status = self._startup_coordinator.inspect()
            if status.state in {StartupState.MISMATCHED, StartupState.UNAVAILABLE}:
                self._apply_startup_status(status.state)
                self.status_text = "Windows startup registration requires manual repair."
                return
            settings = self._application.settings.get_settings()
            self.set_start_with_windows(status.state is not StartupState.ENABLED, settings.version)
        except Exception as exc:
            self.status_text = f"Could not change Windows startup registration: {exc}"

    def _initialize_platform_integrations(self) -> None:
        if self._hotkey_manager is not None:
            try:
                if self.ui is not None:
                    from typing import Any, cast
                    settings = cast(Any, self.ui).shell_settings
                    if settings is None:
                        raise RuntimeError("Initial shell settings unavailable")
                else:
                    settings = self._application.settings.get_settings()
                self._hotkey_manager.start(settings.global_hotkey)
                self.status_text = "Global hotkey active"
            except Exception as exc:
                self.status_text = f"Global hotkey inactive: {exc}"
        if self._startup_coordinator is not None:
            try:
                self._apply_startup_status(self._startup_coordinator.inspect().state)
            except Exception as exc:
                self.status_text = f"Could not inspect Windows startup registration: {exc}"

    def _apply_startup_status(self, state: StartupState) -> None:
        if not hasattr(self.tray, "set_startup_state"):
            return
        self.tray.set_startup_state(
            enabled=state is not StartupState.UNAVAILABLE,
            checked=state is StartupState.ENABLED,
        )

    def _can_hide_to_tray(self) -> bool:
        if not self.tray.available:
            return False
        if self.ui is not None:
            from typing import Any, cast

            settings = cast(Any, self.ui).shell_settings
            return bool(settings is not None and settings.minimize_to_tray)
        try:
            settings = self._application.settings.get_settings()
            return bool(settings.minimize_to_tray)
        except Exception as exc:
            self.status_text = f"Could not read shell settings: {exc}"
            return False

    def _on_window_close(self) -> bool:
        if self._can_hide_to_tray():
            self.hide_window()
            return True
        return self.request_quit() is not QuitResult.SUCCESS

    def _on_window_minimized(self) -> None:
        if self._can_hide_to_tray():
            self.hide_window()

    def abort_startup(self) -> None:
        """Release only shell-owned Qt objects during a failed construction."""
        first_error: Exception | None = None
        cleanups = [self.tray.close, self.window.hide]
        if self.ui is not None:
            from typing import Any, cast

            cleanups.insert(0, cast(Any, self.ui).abort_startup)
        if self._hotkey_manager is not None:
            cleanups.insert(0, self._hotkey_manager.close)
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        self._disconnect_about_to_quit()
        if first_error is not None:
            raise first_error

    def _close_shell_resources(self) -> None:
        if self._cleanup_started:
            return
        self._cleanup_started = True
        if self.ui is not None:
            from typing import Any, cast

            cast(Any, self.ui).close()
        self._disconnect_about_to_quit()
        first_error: Exception | None = None
        cleanups: list[Callable[[], None]] = []
        if self._hotkey_manager is not None:
            cleanups.append(self._hotkey_manager.close)
        if self._activation_server is not None:
            cleanups.append(self._activation_server.close)
        cleanups.extend((self.tray.close, self.window.hide, self._mutex.close))
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _disconnect_about_to_quit(self) -> None:
        """Detach this completed shell from the process-wide Qt shutdown signal."""
        try:
            self._qapplication.aboutToQuit.disconnect(self._about_to_quit)
        except (AttributeError, RuntimeError, TypeError):
            # The connection may already be absent during repeated cleanup or
            # Qt teardown.  Either state is safe and requires no retry.
            return

    def _about_to_quit(self) -> None:
        # Qt cannot veto this signal.  Normal shell exits have already passed
        # request_quit(); external process termination remains an OS limitation.
        if self._shutdown_approved:
            self._close_shell_resources()
