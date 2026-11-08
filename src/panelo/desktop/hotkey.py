"""Pure hotkey parsing plus Qt routing and replacement ownership."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from PySide6.QtCore import QAbstractNativeEventFilter

from panelo.core.exceptions import HotkeyRegistrationError
from panelo.desktop.errors import ShellIntegrationError
from panelo.windows.hotkeys import HotkeyBackend

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
_HOTKEY_IDS = (0x5041, 0x5042)
_LOGGER = logging.getLogger("panelo.desktop.hotkey")


@dataclass(frozen=True, slots=True)
class Hotkey:
    canonical: str
    modifiers: int
    virtual_key: int

    @property
    def registration_modifiers(self) -> int:
        return self.modifiers | MOD_NOREPEAT


def parse_hotkey(value: str) -> Hotkey:
    """Parse a user-facing setting into a platform-independent value object."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Hotkey must be a non-empty canonical combination.")
    raw_parts = value.split("+")
    if any(not part for part in raw_parts):
        raise ValueError("Hotkey separators must not create empty parts.")
    aliases = {
        "ctrl": ("Ctrl", MOD_CONTROL),
        "alt": ("Alt", MOD_ALT),
        "shift": ("Shift", MOD_SHIFT),
    }
    modifiers = 0
    modifier_names: set[str] = set()
    key_name: str | None = None
    virtual_key: int | None = None
    for raw in raw_parts:
        normalized = raw.casefold()
        if normalized == "win":
            raise ValueError("Win is not supported for global hotkeys.")
        if normalized in aliases:
            name, mask = aliases[normalized]
            if name in modifier_names:
                raise ValueError("Hotkey modifiers must be distinct.")
            modifier_names.add(name)
            modifiers |= mask
            continue
        if key_name is not None:
            raise ValueError("Hotkey must contain exactly one non-modifier key.")
        if len(raw) == 1 and raw.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            key_name, virtual_key = raw.upper(), ord(raw.upper())
        elif normalized == "space":
            key_name, virtual_key = "Space", 0x20
        elif (
            normalized.startswith("f")
            and normalized[1:].isdigit()
            and 1 <= int(normalized[1:]) <= 11
        ):
            key_name, virtual_key = f"F{int(normalized[1:])}", 0x70 + int(normalized[1:]) - 1
        else:
            raise ValueError("Hotkey uses an unsupported key.")
    if len(modifier_names) < 2 or key_name is None or virtual_key is None:
        raise ValueError("Hotkey requires at least two modifiers and one supported key.")
    ordered = [name for name in ("Ctrl", "Alt", "Shift") if name in modifier_names]
    return Hotkey("+".join((*ordered, key_name)), modifiers, virtual_key)


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(
        self, hwnd: int, active_id: Callable[[], int | None], activate: Callable[[], None]
    ) -> None:
        super().__init__()
        self._hwnd = hwnd
        self._active_id = active_id
        self._activate = activate

    def nativeEventFilter(self, event_type: bytes, message: object) -> tuple[bool, int]:  # type: ignore[override]
        if sys.platform != "win32" or event_type != b"windows_generic_MSG":
            return False, 0
        try:

            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", ctypes.c_void_p),
                    ("message", ctypes.c_uint),
                    ("wParam", ctypes.c_size_t),
                    ("lParam", ctypes.c_ssize_t),
                    ("time", ctypes.c_uint),
                    ("pt_x", ctypes.c_long),
                    ("pt_y", ctypes.c_long),
                ]

            native = MSG.from_address(int(cast(Any, message)))
        except (TypeError, ValueError):
            return False, 0
        if (
            native.message != WM_HOTKEY
            or int(native.hwnd or 0) != self._hwnd
            or native.wParam != self._active_id()
        ):
            return False, 0
        self._activate()
        return True, 0


class HotkeyManager:
    """Owns both reserved IDs and guarantees candidate-first replacement."""

    def __init__(
        self,
        qapplication: object,
        window: object,
        backend: HotkeyBackend,
        activate: Callable[[], None],
    ) -> None:
        self._application = cast(Any, qapplication)
        self._window = cast(Any, window)
        self._backend, self._activate = backend, activate
        self._hwnd: int | None = None
        self._filter: _NativeFilter | None = None
        self._active_id: int | None = None
        self._active_hotkey: Hotkey | None = None
        self._registered: set[int] = set()
        self._cleanup_pending: set[int] = set()
        self._replacement_pending = False

    @property
    def active_hotkey(self) -> Hotkey | None:
        return self._active_hotkey

    @property
    def cleanup_pending(self) -> bool:
        return bool(self._cleanup_pending)

    def start(self, value: str) -> None:
        candidate = parse_hotkey(value)
        self._ensure_receiver()
        assert self._hwnd is not None
        identifier = _HOTKEY_IDS[0]
        try:
            self._backend.register(
                self._hwnd, identifier, candidate.registration_modifiers, candidate.virtual_key
            )
        except Exception:
            # A nonfatal startup failure must not leave an unused native filter
            # installed for the rest of the process lifetime.
            self.close()
            raise
        self._registered.add(identifier)
        self._active_id, self._active_hotkey = identifier, candidate

    def replace(self, candidate: Hotkey, persist: Callable[[str], None]) -> None:
        if self._active_hotkey == candidate:
            persist(candidate.canonical)
            return
        self._ensure_receiver()
        assert self._hwnd is not None
        self._retry_pending_cleanup()
        candidate_id = next(
            (identifier for identifier in _HOTKEY_IDS if identifier not in self._registered), None
        )
        if candidate_id is None:
            raise HotkeyRegistrationError(
                "No reserved global-hotkey ID is available while earlier cleanup is pending."
            )
        self._backend.register(
            self._hwnd, candidate_id, candidate.registration_modifiers, candidate.virtual_key
        )
        self._registered.add(candidate_id)
        try:
            persist(candidate.canonical)
        except Exception:
            try:
                self._backend.unregister(self._hwnd, candidate_id)
                self._registered.discard(candidate_id)
            except Exception as cleanup_error:
                raise ShellIntegrationError(
                    "Could not compensate failed hotkey replacement."
                ) from cleanup_error
            raise
        old_id = self._active_id
        self._active_id, self._active_hotkey = candidate_id, candidate
        if old_id is not None:
            try:
                self._backend.unregister(self._hwnd, old_id)
                self._registered.discard(old_id)
            except HotkeyRegistrationError:
                # Candidate is already authoritative; retry during shutdown.
                self._cleanup_pending.add(old_id)
                _LOGGER.warning("Could not unregister previous global hotkey; cleanup is pending.")

    def close(self) -> None:
        if self._hwnd is not None:
            for identifier in tuple(self._registered):
                self._unregister_tracked(identifier)
        if self._filter is not None:
            self._application.removeNativeEventFilter(self._filter)
            self._filter = None
        self._active_id = None
        self._active_hotkey = None

    def replace_async(
        self,
        candidate: Hotkey,
        persist: Callable[[str, Callable[[Exception | None], None]], None],
        finished: Callable[[Exception | None], None],
    ) -> None:
        """Candidate-first native registration, with off-GUI persistence.

        persist must deliver its completion on the GUI thread. Existing sync
        replacement remains unchanged for the verified shell integration.
        """
        if self._replacement_pending:
            raise ShellIntegrationError("A hotkey replacement is already pending.")
        self._ensure_receiver()
        assert self._hwnd is not None
        self._retry_pending_cleanup()
        same = self._active_hotkey == candidate
        candidate_id = (
            self._active_id
            if same
            else next((i for i in _HOTKEY_IDS if i not in self._registered), None)
        )
        if candidate_id is None:
            raise HotkeyRegistrationError("No reserved global-hotkey ID is available.")
        if not same:
            self._backend.register(
                self._hwnd, candidate_id, candidate.registration_modifiers, candidate.virtual_key
            )
            self._registered.add(candidate_id)
        self._replacement_pending = True

        def done(error: Exception | None) -> None:
            self._replacement_pending = False
            if error:
                if not same:
                    self._unregister_tracked(candidate_id)
                finished(error)
                return
            previous = self._active_id
            self._active_id, self._active_hotkey = candidate_id, candidate
            if previous is not None and previous != candidate_id:
                self._unregister_tracked(previous)
            finished(None)

        try:
            persist(candidate.canonical, done)
        except Exception as exc:
            done(exc)

    def _retry_pending_cleanup(self) -> None:
        for identifier in tuple(self._cleanup_pending):
            self._unregister_tracked(identifier)

    def _unregister_tracked(self, identifier: int) -> bool:
        assert self._hwnd is not None
        try:
            self._backend.unregister(self._hwnd, identifier)
        except Exception:
            self._cleanup_pending.add(identifier)
            _LOGGER.warning(
                "Could not unregister global-hotkey ID %s; cleanup remains pending.", identifier
            )
            return False
        self._registered.discard(identifier)
        self._cleanup_pending.discard(identifier)
        return True

    def _ensure_receiver(self) -> None:
        if self._hwnd is not None:
            return
        self._hwnd = int(self._window.winId())
        self._filter = _NativeFilter(self._hwnd, lambda: self._active_id, self._activate)
        self._application.installNativeEventFilter(self._filter)
