"""Safe Windows-only smoke tests for Pass 2 platform adapters."""

from __future__ import annotations

from contextlib import suppress
from uuid import uuid4

import pytest

from productivity_app.core.exceptions import HotkeyRegistrationError
from productivity_app.desktop.errors import StartupRegistrationError
from productivity_app.windows.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    WindowsHotkeyBackend,
)
from productivity_app.windows.startup import REG_SZ, WindowsStartupRegistry


def test_windows_hotkey_register_unregister_and_reregister() -> None:
    backend = WindowsHotkeyBackend()
    hotkey_id = 0x6800 + (int(uuid4().hex[:3], 16) % 0x1000)
    hwnd = 0
    try:
        backend.register(hwnd, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x79)  # F10
    except HotkeyRegistrationError as exc:
        pytest.skip(f"Temporary global-hotkey combination is unavailable: {exc}")
    try:
        with pytest.raises(HotkeyRegistrationError):
            backend.register(hwnd, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x79)
    finally:
        backend.unregister(hwnd, hotkey_id)
    backend.register(hwnd, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x79)
    backend.unregister(hwnd, hotkey_id)


def test_windows_registry_adapter_uses_an_isolated_value() -> None:
    registry = WindowsStartupRegistry(value_name=f"ProductivityApp-Pass2-Test-{uuid4().hex}")
    try:
        assert registry.inspect() is None
        try:
            registry.write('"C:\\test launcher.exe" --startup')
        except StartupRegistrationError as exc:
            cause = exc.__cause__
            if isinstance(cause, PermissionError) or getattr(cause, "winerror", None) == 5:
                pytest.skip(f"Current environment does not permit HKCU Run writes: {exc}")
            raise
        value = registry.inspect()
        assert value is not None
        assert value.value_type == REG_SZ
        assert value.value == '"C:\\test launcher.exe" --startup'
        registry.delete()
        assert registry.inspect() is None
    finally:
        with suppress(StartupRegistrationError):
            registry.delete()
