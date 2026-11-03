"""Desktop process bootstrap: ``python -m productivity_app.desktop [--startup] [--show]``.

One process owns the app per user and database (a named mutex); a second launch only asks the
first to show itself.  The first launch opens the Full App (and the welcome tour the first
time); ``--startup`` (what Windows runs at sign-in) starts hidden in the tray.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from productivity_app.bootstrap import Application, create_application
from productivity_app.core.config import AppConfig, default_config
from productivity_app.core.exceptions import AppError
from productivity_app.desktop.app import DesktopApp, build_desktop
from productivity_app.desktop.instance import (
    ActivationResponse,
    ActivationServer,
    activation_server_name,
    request_activation,
)
from productivity_app.desktop.startup import default_launcher
from productivity_app.windows.hotkeys import HotkeyBackend
from productivity_app.windows.mutex import (
    MutexBackend,
    WindowsMutexBackend,
    build_instance_identity,
    current_user_sid,
)
from productivity_app.windows.startup import StartupRegistry

_LOGGER = logging.getLogger("productivity_app.desktop")
_STARTUP_FAILURE_EXIT_CODE = 1


class _Closeable(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DesktopOptions:
    startup: bool = False
    show: bool = False
    # Development commands (they always use a temporary database, never yours)
    selftest: bool = False
    screenshot: Path | None = None
    state: str | None = None
    delay: int = 1800
    quit_after: int | None = None

    @property
    def start_hidden_requested(self) -> bool:
        return self.startup and not self.show

    @property
    def dev_command(self) -> bool:
        return self.selftest or self.screenshot is not None


def parse_desktop_options(argv: Sequence[str] | None = None) -> DesktopOptions:
    parser = argparse.ArgumentParser(prog="productivity-app", allow_abbrev=False)
    parser.add_argument("--startup", action="store_true", help="start hidden in the tray")
    parser.add_argument("--show", action="store_true", help="show the window even with --startup")
    parser.add_argument("--selftest", action="store_true", help="run the UI self-test on a temporary database")
    parser.add_argument("--screenshot", type=Path, help="save a render of one window state and exit")
    parser.add_argument("--state", help="the window state for --screenshot")
    parser.add_argument("--delay", type=int, default=1800, help="ms to wait before the screenshot")
    parser.add_argument("--quit-after", type=int, help="quit after this many ms (scripted runs)")
    namespace = parser.parse_args(argv)
    return DesktopOptions(
        startup=namespace.startup,
        show=namespace.show,
        selftest=namespace.selftest,
        screenshot=namespace.screenshot,
        state=namespace.state,
        delay=namespace.delay,
        quit_after=namespace.quit_after,
    )


def create_qt_application(argv: Sequence[str]) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return cast(QApplication, existing)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(list(argv))
    app.setOrganizationName("ProductivityApp")
    app.setApplicationName("ProductivityApp")
    return app


def show_initial(desktop: DesktopApp, options: DesktopOptions) -> None:
    """What the first moment looks like: the Full App (with the tour on the very first run), or
    nothing but the tray icon for ``--startup``."""
    controller = desktop.controller
    hidden = options.start_hidden_requested and controller.tray is not None

    def start() -> None:
        if not hidden:
            controller.show_full("notes")

    controller.prewarm_full(start)


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
    os.environ.setdefault("QT_FORCE_STDERR_LOGGING", "1")
    qt_app = qapplication or create_qt_application([sys.argv[0]])
    if options.dev_command:
        from productivity_app.ui.selftest.runner import run_dev_command  # noqa: PLC0415

        return run_dev_command(options, qt_app)
    app_config = config or default_config()
    identity = build_instance_identity(sid_provider(), app_config.database.path)
    mutex = None
    application: Application | None = None
    desktop: DesktopApp | None = None
    server: ActivationServer | None = None

    def activate_if_ready() -> ActivationResponse:
        if desktop is None:
            return ActivationResponse.BUSY
        desktop.controller.open_full_from_tray()
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
        application = backend_factory(app_config)
        desktop = build_desktop(
            qt_app,
            application,
            data_dir=app_config.paths.data_dir,
            hotkey_backend=hotkey_backend,
            startup_registry=startup_registry,
            startup_launcher=startup_launcher or default_launcher(),
        )
        show_initial(desktop, options)
        if options.quit_after:
            QTimer.singleShot(options.quit_after, desktop.controller.quit)
        if not execute:
            return 0
        exit_code = qt_app.exec()
        _shutdown(desktop, application, server, mutex)
        return exit_code
    except Exception as exc:
        _cleanup_failed_startup(desktop, application, server, mutex)
        if isinstance(exc, (AppError, OSError, RuntimeError)):
            _LOGGER.error("Desktop startup failed: %s", exc, exc_info=True)
            return _STARTUP_FAILURE_EXIT_CODE
        raise


def _shutdown(
    desktop: DesktopApp,
    application: Application,
    server: ActivationServer,
    mutex: _Closeable,
) -> None:
    """The event loop has ended: release everything, the database last but one."""
    for name, cleanup in (
        ("desktop", desktop.close),
        ("database", application.close),
        ("activation endpoint", server.close),
        ("instance mutex", mutex.close),
    ):
        try:
            cleanup()
        except Exception:
            _LOGGER.exception("Could not clean up %s.", name)


def _cleanup_failed_startup(
    desktop: DesktopApp | None,
    application: Application | None,
    server: ActivationServer | None,
    mutex: _Closeable | None,
) -> None:
    """Attempt every cleanup stage while preserving the original startup error."""
    stages: list[tuple[str, Callable[[], None]]] = []
    if desktop is not None:
        stages.append(("desktop", cast(Any, desktop).close))
    if application is not None:
        stages.append(("backend", application.close))
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
