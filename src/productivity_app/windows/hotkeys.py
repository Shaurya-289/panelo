"""Thin, lazy Win32 ``RegisterHotKey`` adapter."""

from __future__ import annotations

import ctypes
import sys
from typing import Protocol

from productivity_app.core.exceptions import HotkeyRegistrationError
from productivity_app.desktop.errors import PlatformNotSupportedError

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


class HotkeyBackend(Protocol):
    def register(self, hwnd: int, hotkey_id: int, modifiers: int, virtual_key: int) -> None: ...

    def unregister(self, hwnd: int, hotkey_id: int) -> None: ...


class WindowsHotkeyBackend:
    """Direct, non-hook based global-hotkey adapter.

    Loading ``user32`` is deliberately deferred until a Windows operation is
    requested so importing the desktop shell remains safe on other platforms.
    """

    @staticmethod
    def _user32() -> ctypes.WinDLL:
        if sys.platform != "win32":
            raise PlatformNotSupportedError("Windows global hotkeys require Windows.")
        return ctypes.WinDLL("user32", use_last_error=True)

    @staticmethod
    def _failure(operation: str) -> HotkeyRegistrationError:
        code = ctypes.get_last_error()
        cause = OSError(code, f"{operation} failed with Win32 error {code}")
        error = HotkeyRegistrationError(str(cause))
        error.__cause__ = cause
        return error

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, virtual_key: int) -> None:
        user32 = self._user32()
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_int
        if not user32.RegisterHotKey(ctypes.c_void_p(hwnd), hotkey_id, modifiers, virtual_key):
            raise self._failure("RegisterHotKey")

    def unregister(self, hwnd: int, hotkey_id: int) -> None:
        user32 = self._user32()
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        if not user32.UnregisterHotKey(ctypes.c_void_p(hwnd), hotkey_id):
            raise self._failure("UnregisterHotKey")
