"""The self-test environment: a complete app on a temporary database and library.

Nothing here can reach the user's data: the database, the data folder, the backups, the log and
the Files library live in one temporary folder; the global hotkey, the Run registry key, the
Recycle Bin, the sound card and the screen's wallpaper are replaced by fakes.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from productivity_app.bootstrap import create_application
from productivity_app.core.config import AppConfig, DatabaseConfig
from productivity_app.core.exceptions import HotkeyRegistrationError
from productivity_app.core.paths import AppPaths
from productivity_app.desktop.app import DesktopApp, build_desktop
from productivity_app.files.library import FileLibrary
from productivity_app.ui.selftest.seed import Aliases, OffsetClock, seed_demo, seed_library
from productivity_app.windows.startup import REG_SZ, RegistryValue

MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x0001, 0x0002, 0x0004


def describe_hotkey(modifiers: int, virtual_key: int) -> str:
    """``Ctrl+Alt+K`` for the Win32 modifier bits and virtual-key code."""
    names = [
        name
        for bit, name in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"))
        if modifiers & bit
    ]
    if virtual_key == 0x20:
        key = "Space"
    elif 0x70 <= virtual_key <= 0x7B:
        key = f"F{virtual_key - 0x6F}"
    else:
        key = chr(virtual_key)
    return "+".join([*names, key])


class FakeHotkeyBackend:
    """Stands in for ``RegisterHotKey``: records each registration; combinations in ``taken``
    behave as registered by another app."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.registered: dict[int, str] = {}
        self.taken: set[str] = set()

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, virtual_key: int) -> None:
        name = describe_hotkey(modifiers, virtual_key)
        self.calls.append(name)
        if name in self.taken:
            raise HotkeyRegistrationError("RegisterHotKey failed (fake: already registered)")
        self.registered[hotkey_id] = name

    def unregister(self, hwnd: int, hotkey_id: int) -> None:
        self.registered.pop(hotkey_id, None)


class FakeStartupRegistry:
    """Stands in for the HKCU Run key: one value, kept in memory."""

    def __init__(self) -> None:
        self.value: RegistryValue | None = None

    def inspect(self) -> RegistryValue | None:
        return self.value

    def write(self, command: str) -> None:
        self.value = RegistryValue(command, REG_SZ)

    def restore(self, value: RegistryValue | None) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = None


def hard_delete(path: Path) -> bool:
    """The test's Recycle Bin: the file is simply gone."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def biggest_screen() -> Any:
    """The largest screen, so a 1320x820 window always fits (the cursor may be on a small one)."""
    screens = QGuiApplication.screens()
    return max(screens, key=lambda s: s.availableGeometry().width() * s.availableGeometry().height())


@dataclass
class SelfTestEnv:
    root: Path
    clock: OffsetClock
    config: AppConfig
    desktop: DesktopApp
    aliases: Aliases
    hotkeys: FakeHotkeyBackend
    registry: FakeStartupRegistry
    launcher: Path
    keep: bool = False
    _cleaned: bool = field(default=False, repr=False)

    @property
    def application(self) -> Any:
        return self.desktop.application

    @property
    def backend(self) -> Any:
        return self.desktop.backend

    @property
    def controller(self) -> Any:
        return self.desktop.controller

    @property
    def data_dir(self) -> Path:
        return self.config.paths.data_dir

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.desktop.close()
        self.application.close()
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)


def make_env(
    qt_app: QApplication,
    *,
    seed: bool = True,
    library_seed: bool = True,
    keep: bool = False,
    biggest: bool = True,
) -> SelfTestEnv:
    """Build the whole app on a throwaway database (seeded with the prototype's demo data)."""
    root = Path(tempfile.mkdtemp(prefix="productivity-selftest-"))
    paths = AppPaths(data_dir=root / "data", log_dir=root / "logs")
    config = AppConfig(
        paths=paths, database=DatabaseConfig(path=paths.data_dir / "productivity.db"), log_level="WARNING"
    )
    clock = OffsetClock()
    application = create_application(config, clock)
    aliases = seed_demo(application, clock) if seed else Aliases()
    library = FileLibrary(root / "library", trash=hard_delete)
    if library_seed:
        seed_library(library.root)
    launcher = root / "launcher" / "productivity-app.exe"
    launcher.parent.mkdir()
    launcher.write_bytes(b"")
    hotkeys, registry = FakeHotkeyBackend(), FakeStartupRegistry()
    desktop = build_desktop(
        qt_app,
        application,
        data_dir=paths.data_dir,
        clock=clock,
        library=library,
        fake_backdrop=True,
        tray=False,
        hotkey_backend=hotkeys,
        startup_registry=registry,
        startup_launcher=launcher,
        stub_sounds=True,
        stub_launch=True,
        timezone=None,
    )
    if biggest:  # the self-test needs room for a 1320x820 window; screenshots follow the cursor
        desktop.controller.screen_override = biggest_screen
    desktop.controller.foreground = None  # tests never ask Windows to move the focus
    desktop.controller.settings_path = paths.data_dir / "window.ini"  # type: ignore[attr-defined]
    return SelfTestEnv(
        root=root, clock=clock, config=config, desktop=desktop, aliases=aliases,
        hotkeys=hotkeys, registry=registry, launcher=launcher, keep=keep,
    )
