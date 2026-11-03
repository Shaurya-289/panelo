from types import SimpleNamespace
from typing import Any

import pytest

from productivity_app.windows import presentation


class Native:
    def __init__(self, *, high_contrast: bool = False, failure: bool = False) -> None:
        self.contrast = high_contrast
        self.failure = failure
        self.handles: list[int] = []

    def high_contrast(self) -> bool:
        return self.contrast

    def backdrop(self, hwnd: int) -> None:
        self.handles.append(hwnd)
        if self.failure:
            raise OSError("DWM rejected the surface")

    def foreground(self, hwnd: int) -> bool:
        self.handles.append(hwnd)
        return not self.failure


def test_non_windows_has_no_native_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presentation.sys, "platform", "linux")
    monkeypatch.setattr(presentation, "_NativePresentation", lambda: pytest.fail("Native library loaded"))
    assert presentation.apply_backdrop(42).mode == presentation.BackdropMode.ALPHA_ONLY
    assert not presentation.request_foreground(42)


@pytest.mark.parametrize(("contrast", "build", "failure", "mode"), [
    (False, 22621, False, presentation.BackdropMode.NATIVE_REQUESTED),
    (True, 22621, False, presentation.BackdropMode.OPAQUE),
    (False, 19045, False, presentation.BackdropMode.ALPHA_ONLY),
    (False, 22621, True, presentation.BackdropMode.ALPHA_ONLY),
])
def test_backdrop_selection_and_failure_are_explicit(monkeypatch: pytest.MonkeyPatch, contrast: bool, build: int, failure: bool, mode: presentation.BackdropMode) -> None:
    native = Native(high_contrast=contrast, failure=failure)
    monkeypatch.setattr(presentation.sys, "platform", "win32")
    monkeypatch.setattr(presentation.sys, "getwindowsversion", lambda: SimpleNamespace(build=build), raising=False)
    monkeypatch.setattr(presentation, "_NativePresentation", lambda: native)
    result = presentation.apply_backdrop(42)
    assert result.mode == mode and result.reason
    assert native.handles == ([] if contrast or build < 22621 else [42])
    assert presentation.request_foreground(42) is not failure


def test_native_loading_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise OSError("DLL unavailable")

    monkeypatch.setattr(presentation.sys, "platform", "win32")
    monkeypatch.setattr(presentation, "_NativePresentation", fail)
    assert presentation.apply_backdrop(42).mode == presentation.BackdropMode.ALPHA_ONLY
    assert not presentation.request_foreground(42)


@pytest.mark.parametrize("failing_stage", [None, "attribute", "extend", "contrast"])
def test_native_adapter_uses_documented_calls_and_compensates(monkeypatch: pytest.MonkeyPatch, failing_stage: str | None) -> None:
    ctypes = presentation.ctypes
    attributes: list[tuple[int, int, int]] = []
    margins: list[tuple[int, ...]] = []

    def contrast(action: int, size: int, pointer: Any, flags: int) -> int:
        assert action == 0x0042 and flags == 0
        if failing_stage == "contrast":
            return 0
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint))[1] = 1
        return 1

    def attribute(hwnd: Any, key: int, pointer: Any, size: int) -> int:
        assert hwnd.value == 42 and size == 4
        material = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0]
        attributes.append((hwnd.value, key, material))
        return -1 if failing_stage == "attribute" else 0

    def extend(hwnd: Any, pointer: Any) -> int:
        assert hwnd.value == 42
        values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))
        margins.append(tuple(values[i] for i in range(4)))
        return -1 if failing_stage == "extend" else 0

    def foreground(hwnd: Any) -> int:
        assert hwnd.value == 42
        return 1

    user32 = SimpleNamespace(SystemParametersInfoW=contrast, SetForegroundWindow=foreground)
    dwm = SimpleNamespace(DwmSetWindowAttribute=attribute, DwmExtendFrameIntoClientArea=extend)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: user32 if name == "user32" else dwm, raising=False)
    if failing_stage == "contrast":
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
        monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "Native query failed"), raising=False)
    native = presentation._NativePresentation()
    if failing_stage == "contrast":
        with pytest.raises(OSError):
            native.high_contrast()
        return
    assert native.high_contrast()
    if failing_stage is not None:
        with pytest.raises(OSError, match="HRESULT"):
            native.backdrop(42)
    else:
        native.backdrop(42)
    assert attributes[0] == (42, 38, 3)
    if failing_stage == "extend":
        assert attributes[-1] == (42, 38, 1)
    assert margins == ([] if failing_stage == "attribute" else [(-1, -1, -1, -1)])
    assert native.foreground(42)
