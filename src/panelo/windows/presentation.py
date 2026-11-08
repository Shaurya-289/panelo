"""Best-effort supported Windows presentation, with no native import effects."""

from __future__ import annotations

import ctypes
import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum


class BackdropMode(StrEnum):
    NATIVE_REQUESTED = "NATIVE_REQUESTED"
    ALPHA_ONLY = "ALPHA_ONLY"
    OPAQUE = "OPAQUE"


@dataclass(frozen=True, slots=True)
class BackdropResult:
    mode: BackdropMode
    reason: str


class _NativePresentation:
    """Loaded only by an explicit Windows operation, never by module import."""

    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

    def high_contrast(self) -> bool:
        class HIGHCONTRAST(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint), ("scheme", ctypes.c_wchar_p)]

        value = HIGHCONTRAST()
        value.cbSize = ctypes.sizeof(value)
        function = self.user32.SystemParametersInfoW
        function.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if not function(0x0042, value.cbSize, ctypes.byref(value), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(value.dwFlags & 0x0001)

    def backdrop(self, hwnd: int) -> None:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        attribute = dwm.DwmSetWindowAttribute
        attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        attribute.restype = ctypes.c_long
        material = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW (Desktop Acrylic).
        result = attribute(ctypes.c_void_p(hwnd), 38, ctypes.byref(material), ctypes.sizeof(material))
        if result < 0:
            raise OSError(f"DwmSetWindowAttribute failed: HRESULT 0x{result & 0xffffffff:08x}")

        class MARGINS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]

        margins = MARGINS(-1, -1, -1, -1)
        extend = dwm.DwmExtendFrameIntoClientArea
        extend.argtypes = [ctypes.c_void_p, ctypes.POINTER(MARGINS)]
        extend.restype = ctypes.c_long
        result = extend(ctypes.c_void_p(hwnd), ctypes.byref(margins))
        if result < 0:
            material.value = 1  # DWMSBT_NONE: compensate an incomplete request.
            with suppress(OSError):
                attribute(ctypes.c_void_p(hwnd), 38, ctypes.byref(material), ctypes.sizeof(material))
            raise OSError(f"DwmExtendFrameIntoClientArea failed: HRESULT 0x{result & 0xffffffff:08x}")

    def foreground(self, hwnd: int) -> bool:
        function = self.user32.SetForegroundWindow
        function.argtypes = [ctypes.c_void_p]
        function.restype = ctypes.c_int
        return bool(function(ctypes.c_void_p(hwnd)))


def apply_backdrop(hwnd: int) -> BackdropResult:
    if sys.platform != "win32":
        return BackdropResult(BackdropMode.ALPHA_ONLY, "Native backdrop requires Windows")
    try:
        native = _NativePresentation()
        if native.high_contrast():
            return BackdropResult(BackdropMode.OPAQUE, "Windows high contrast is enabled")
        if sys.getwindowsversion().build < 22621:
            return BackdropResult(BackdropMode.ALPHA_ONLY, "System backdrop requires Windows build 22621+")
        native.backdrop(hwnd)
        # HRESULT success is not proof of visible blur on a layered Qt surface.
        return BackdropResult(BackdropMode.NATIVE_REQUESTED, "DWM accepted Acrylic; visual verification required")
    except (OSError, AttributeError) as exc:
        return BackdropResult(BackdropMode.ALPHA_ONLY, f"Native backdrop unavailable: {exc}")


def request_foreground(hwnd: int) -> bool:
    """One user-triggered request; refusal is normal and never retried."""
    if sys.platform != "win32":
        return False
    try:
        return _NativePresentation().foreground(hwnd)
    except (OSError, AttributeError):
        return False
