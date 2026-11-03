"""Windows named-mutex ownership for the desktop shell.

No Win32 function is resolved at import time, keeping this module import-safe
for non-Windows test environments.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from productivity_app.desktop.errors import PlatformNotSupportedError, ShellIntegrationError

_ERROR_ALREADY_EXISTS = 183
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


def build_instance_identity(user_sid: str, database_path: Path) -> str:
    """Return a stable opaque identity for one user and canonical database."""
    canonical_path = str(database_path.expanduser().resolve()).casefold()
    material = f"{user_sid}\x00{canonical_path}".encode()
    return f"ProductivityApp-{hashlib.sha256(material).hexdigest()[:32]}"


def _require_windows() -> None:
    if sys.platform != "win32":
        raise PlatformNotSupportedError("Windows shell integration is only available on Windows.")


def current_user_sid() -> str:
    """Read the current access-token SID without using a display username."""
    _require_windows()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise ShellIntegrationError(f"Could not open the current process token (Win32 {ctypes.get_last_error()}).")
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, required, ctypes.byref(required)):
            raise ShellIntegrationError(f"Could not read the current user SID (Win32 {ctypes.get_last_error()}).")

        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid_pointer), ctypes.byref(sid_text)):
            raise ShellIntegrationError(f"Could not encode the current user SID (Win32 {ctypes.get_last_error()}).")
        try:
            return str(sid_text.value)
        finally:
            kernel32.LocalFree(sid_text)
    finally:
        kernel32.CloseHandle(token)


@dataclass(slots=True)
class MutexLease:
    """One retained mutex handle; it is released at most once."""

    handle: int
    primary: bool
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _require_windows()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(self.handle):
            raise ShellIntegrationError(f"Could not close instance mutex (Win32 {ctypes.get_last_error()}).")


class MutexBackend(Protocol):
    def acquire(self, identity: str) -> MutexLease: ...


class WindowsMutexBackend:
    """Create a global per-user/database mutex without process-list scanning."""

    def acquire(self, identity: str) -> MutexLease:
        _require_windows()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, f"Global\\{identity}")
        if not handle:
            raise ShellIntegrationError(f"Could not create instance mutex (Win32 {ctypes.get_last_error()}).")
        existing = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
        return MutexLease(handle=int(handle), primary=not existing)
