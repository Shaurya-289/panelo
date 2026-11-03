"""Desktop process bootstrap; it never changes the backend-only entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from PySide6.QtWidgets import QApplication

from productivity_app.bootstrap import Application, create_application
from productivity_app.core.config import AppConfig, default_config
from productivity_app.core.exceptions import AppError
from productivity_app.desktop.controller import QuitResult, ShellController
from productivity_app.desktop.errors import ShellIntegrationError
from productivity_app.desktop.hotkey import HotkeyManager
from productivity_app.desktop.instance import (
    ActivationResponse,
    ActivationServer,
    activation_server_name,
    request_activation,
)
from productivity_app.desktop.startup import StartupCoordinator, default_launcher
from productivity_app.windows.hotkeys import HotkeyBackend, WindowsHotkeyBackend
from productivity_app.windows.mutex import (
    MutexBackend,
    WindowsMutexBackend,
    build_instance_identity,
    current_user_sid,
)
from productivity_app.windows.startup import StartupRegistry, WindowsStartupRegistry

_LOGGER = logging.getLogger("productivity_app.desktop")
_STARTUP_FAILURE_EXIT_CODE = 1


class _Closeable(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DesktopOptions:
    startup: bool
    show: bool

    @property
    def start_hidden_requested(self) -> bool:
        return self.startup and not self.show


def parse_desktop_options(argv: Sequence[str] | None = None) -> DesktopOptions:
    parser = argparse.ArgumentParser(prog="productivity-app-desktop", allow_abbrev=False)
    parser.add_argument("--startup", action="store_true")
    parser.add_argument("--show", action="store_true")
    namespace = parser.parse_args(argv)
    return DesktopOptions(startup=namespace.startup, show=namespace.show)


def run_desktop(
    argv: Sequence[str] | None = None,
    *,
    config: AppConfig | None = None,
    backend_factory: Callable[[AppConfig], Application] = create_application,
    mutex_backend: MutexBackend | None = None,
    sid_provider: Callable[[], str] = current_user_sid,
    activation_sender: Callable[[str], None] = request_activation,
    qapplication: QApplication | None = None,
    hotkey_backend: HotkeyBackend | None = None,
    startup_registry: StartupRegistry | None = None,
    startup_launcher: Path | None = None,
    execute: bool = True,
) -> int:
    """Run one desktop process and avoid backend creation in a secondary launch."""
    options = parse_desktop_options(argv)
    qt_app = (
        qapplication
        or cast(QApplication | None, QApplication.instance())
        or QApplication([sys.argv[0]])
    )
    app_config = config or default_config()
    identity = build_instance_identity(sid_provider(), app_config.database.path)
    mutex = None
    backend: Application | None = None
    controller: ShellController | None = None
    server: ActivationServer | None = None

    def activate_if_ready() -> ActivationResponse:
        if controller is None:
            return ActivationResponse.BUSY
        controller.activate_window()
        return ActivationResponse.OK

    try:
        mutex = (mutex_backend or WindowsMutexBackend()).acquire(identity)
        server_name = activation_server_name(identity)
        if not mutex.primary:
            try:
                activation_sender(server_name)
                return 0
            finally:
                mutex.close()
        server = ActivationServer(server_name, activate_if_ready)
        server.start()
        backend = backend_factory(app_config)
        try:
            controller = ShellController(cast(Any, backend), qt_app, mutex, server)
            controller.attach_platform_integrations(
                HotkeyManager(
                    qt_app,
                    controller.window,
                    hotkey_backend or WindowsHotkeyBackend(),
                    controller.toggle_window,
                ),
                StartupCoordinator(
                    cast(Any, backend),
                    startup_registry or WindowsStartupRegistry(),
                    startup_launcher or default_launcher(),
                ),
            )
            # Fake shell-only test backends retain the verified shell contract.
            # The actual application receives the final service-backed UI before
            # its first show, without recreating any native shell resource.
            if isinstance(backend, Application):
                from productivity_app.desktop.ui.application_ui import ApplicationUi

                controller.ui = ApplicationUi(backend, controller, app_config.paths.data_dir)
                # The existing startup preference decision needs the initial
                # settings DTO, obtained by the UI worker rather than this thread.
                controller.ui.drain()
        except RuntimeError as exc:
            raise ShellIntegrationError("Could not initialize the desktop shell.") from exc
        start_hidden = (
            options.start_hidden_requested
            and controller.tray.available
            and controller._can_hide_to_tray()
        )
        controller.start(start_hidden=start_hidden)
        if not execute:
            return 0
        exit_code = qt_app.exec()
        # External Qt exit cannot be vetoed from aboutToQuit.  Once the loop
        # returns, still give the backend its normal pre-close attempt.
        if not controller.is_closed and controller.request_quit() is QuitResult.REJECTED:
            return _STARTUP_FAILURE_EXIT_CODE
        return exit_code
    except Exception as exc:
        _cleanup_failed_startup(controller, backend, server, mutex)
        if isinstance(exc, (AppError, OSError)):
            _LOGGER.error("Desktop startup failed: %s", exc, exc_info=True)
            return _STARTUP_FAILURE_EXIT_CODE
        raise


def _cleanup_failed_startup(
    controller: ShellController | None,
    backend: Application | None,
    server: ActivationServer | None,
    mutex: _Closeable | None,
) -> None:
    """Attempt every cleanup stage while preserving the original startup error."""
    stages: list[tuple[str, Callable[[], None]]] = []
    if controller is not None:
        stages.append(("desktop shell", controller.abort_startup))
    if backend is not None:
        stages.append(("backend", backend.close))
    if server is not None:
        stages.append(("activation endpoint", server.close))
    if mutex is not None:
        stages.append(("instance mutex", mutex.close))
    for name, cleanup in stages:
        try:
            cleanup()
        except Exception:
            _LOGGER.exception("Could not clean up %s after desktop startup failure.", name)


def main() -> None:
    raise SystemExit(run_desktop())
