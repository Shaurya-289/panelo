"""Assembly of the desktop app: the backend bridge, the QML windows and the shell around them.

``build_desktop`` is the one place that wires everything together.  ``run_desktop`` (in
``main.py``) calls it with the real pieces; the self-test, the screenshots and the tests call it
with a temporary database and library and with fakes for everything that touches the machine
(the global hotkey, the Run registry key, the Recycle Bin, the sound card, the screen).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QSettings, QUrl
from PySide6.QtGui import QFontDatabase, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from productivity_app.bootstrap import Application
from productivity_app.core.clock import Clock
from productivity_app.core.exceptions import AppError
from productivity_app.desktop.hotkey import HotkeyManager
from productivity_app.desktop.shell import (
    Controller,
    FullWindowState,
    SnapshotProvider,
    style_full_window,
)
from productivity_app.desktop.startup import StartupCoordinator, StartupState
from productivity_app.files.library import FileLibrary, library_root
from productivity_app.ui.bridge.backend import Backend
from productivity_app.ui.bridge.library import Library, qt_trash
from productivity_app.ui.bridge.scheduler import Scheduler
from productivity_app.ui.bridge.system import HotkeyBridge, SoundPlayer, clock_is_12h
from productivity_app.windows.hotkeys import HotkeyBackend, WindowsHotkeyBackend
from productivity_app.windows.startup import StartupRegistry, WindowsStartupRegistry

_LOGGER = logging.getLogger("productivity_app.desktop.app")

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
QML_DIR = UI_DIR / "qml"
FONT_PATH = UI_DIR / "fonts" / "Outfit[wght].ttf"
WINDOW_NAMES = ("Main", "Overlay", "Toast")


@dataclass
class DesktopApp:
    """Everything that exists while the app runs."""

    qt_app: QApplication
    application: Application
    backend: Backend
    scheduler: Scheduler
    library: Library
    engine: QQmlApplicationEngine
    full: Any
    overlay: Any
    toast: Any
    store: Any
    controller: Controller
    hotkeys: HotkeyBridge
    sounds: SoundPlayer
    provider: SnapshotProvider
    window_settings: QSettings
    hotkey_manager: HotkeyManager | None = None
    hotkey_window: QWindow | None = None
    startup: StartupCoordinator | None = None
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        """Stop everything that runs on its own and release the machine (hotkey, files).  The
        application (database) is closed by whoever created it."""
        if self._closed:
            return
        self._closed = True
        self.controller.save_state()
        self.scheduler.stop()
        self.backend.close()
        if self.hotkey_manager is not None:
            self.hotkey_manager.close()
        self.library.close()
        self.window_settings.sync()
        # Take the QML down while the objects it binds to (Backend, Library, ...) still exist,
        # or their bindings would complain about a null while the process winds down.
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def startup_handler(
    backend: Backend, coordinator: StartupCoordinator
) -> Callable[[bool], str]:
    """The Settings switch "Start with Windows": registers (or removes) the Run entry and saves
    the setting, or says why it could not.  Returns "" on success."""

    def apply(enabled: bool) -> str:
        backend.flush_settings()
        try:
            coordinator.set_enabled(enabled, backend.settings.version)
        except AppError as error:
            return f"Could not change Start with Windows: {error}"
        return ""

    return apply


def reconcile_startup(backend: Backend, coordinator: StartupCoordinator) -> None:
    """The setting says "start with Windows" but the Run entry is missing or points somewhere
    else (the app was moved): write it again."""
    try:
        status = coordinator.inspect()
        if backend.settings.start_with_windows and status.state in {
            StartupState.DISABLED,
            StartupState.MISMATCHED,
        }:
            coordinator.set_enabled(True, backend.settings.version, repair=True)
            backend.refresh("settings")
    except AppError as error:
        _LOGGER.warning("Start with Windows could not be checked: %s", error)


def build_desktop(
    qt_app: QApplication,
    application: Application,
    *,
    data_dir: Path,
    clock: Clock | None = None,
    library: FileLibrary | None = None,
    fake_backdrop: bool = False,
    tray: bool = True,
    hotkey_backend: HotkeyBackend | None = None,
    startup_registry: StartupRegistry | None = None,
    startup_launcher: Path | None = None,
    stub_sounds: bool = False,
    stub_launch: bool = False,
    sync_jobs: bool = False,
    window_settings: QSettings | None = None,
    qml_dir: Path = QML_DIR,
    timezone: str | None = None,
    start_scheduler: bool = True,
) -> DesktopApp:
    """Create the backend bridge, load the three QML windows and build the shell.

    Nothing is shown yet: the caller decides (the hotkey, the tray, ``--show``, the tour).
    """
    qt_app.setQuitOnLastWindowClosed(False)
    if QFontDatabase.addApplicationFont(str(FONT_PATH)) < 0:
        _LOGGER.warning("could not load the Outfit font")

    backend = Backend(application, clock=clock, sync_jobs=sync_jobs, timezone=timezone)
    scheduler = Scheduler(backend)

    if library is None:
        try:
            library = FileLibrary(library_root(backend.settings.library_path), trash=qt_trash)
        except OSError as error:
            # The saved folder is gone (an unplugged drive, say): use the default one.
            _LOGGER.warning("the library folder is not available (%s); using the default", error)
            library = FileLibrary(library_root(""), trash=qt_trash)
            backend.notice.emit("Your Files folder could not be opened, so the default one is used.")
    library_bridge = Library(
        library,
        stub_launch=stub_launch,
        sync_jobs=sync_jobs,
        on_root_changed=lambda path: backend.set_setting_now("libraryPath", path),
    )

    hotkeys = HotkeyBridge(None, lambda canonical: backend.set_setting_now("hotkey", canonical))
    sounds = SoundPlayer(stub=stub_sounds)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_dir))
    context = engine.rootContext()
    context.setContextProperty("Backend", backend)
    context.setContextProperty("Library", library_bridge)
    context.setContextProperty("Hotkeys", hotkeys)
    context.setContextProperty("Sounds", sounds)
    provider = SnapshotProvider()
    engine.addImageProvider("snapshot", provider)
    for name in WINDOW_NAMES:
        engine.load(QUrl.fromLocalFile(str(qml_dir / f"{name}.qml")))
    roots = engine.rootObjects()
    if len(roots) != len(WINDOW_NAMES):
        raise RuntimeError("The user interface could not be loaded (see the QML errors above).")
    full, overlay, toast = roots
    store = engine.singletonInstance("App", "Store")
    theme = engine.singletonInstance("App", "Theme")
    assert store is not None and theme is not None
    style_full_window(full, theme)
    # The overlay clock follows the Windows 12h/24h setting.
    store.setProperty(
        "twelveHour", clock_is_12h(QLocale.system().timeFormat(QLocale.FormatType.ShortFormat))
    )

    settings_file = window_settings or QSettings(
        str(data_dir / "window.ini"), QSettings.Format.IniFormat
    )
    controller = Controller(
        qt_app, full, overlay, toast, store, backend, provider,
        use_fake_backdrop=fake_backdrop, tray=tray, library=library_bridge,
        state=FullWindowState(settings_file),
    )
    controller.sounds = sounds
    engine.quit.connect(controller.quit)

    desktop = DesktopApp(
        qt_app=qt_app, application=application, backend=backend, scheduler=scheduler,
        library=library_bridge, engine=engine, full=full, overlay=overlay, toast=toast,
        store=store, controller=controller, hotkeys=hotkeys, sounds=sounds, provider=provider,
        window_settings=settings_file,
    )

    # The global hotkey: a hidden window owns the registration (only its handle gets WM_HOTKEY).
    desktop.hotkey_window = QWindow()
    desktop.hotkey_manager = HotkeyManager(
        qt_app, desktop.hotkey_window, hotkey_backend or WindowsHotkeyBackend(), controller.on_hotkey
    )
    hotkeys.manager = desktop.hotkey_manager
    wanted = backend.settings.global_hotkey
    try:
        desktop.hotkey_manager.start(wanted)
    except Exception as error:
        _LOGGER.warning("could not register %s: %s", wanted, error)
        backend.notice.emit(f"{wanted} is already used by another app. Pick another hotkey in Settings.")

    # Start with Windows: the Settings switch drives the Run entry.
    desktop.startup = StartupCoordinator(
        cast(Any, application), startup_registry or WindowsStartupRegistry(), startup_launcher
    )
    backend.startup_handler = startup_handler(backend, desktop.startup)
    reconcile_startup(backend, desktop.startup)

    qt_app.aboutToQuit.connect(desktop.close)
    if start_scheduler:
        scheduler.start()
    return desktop
