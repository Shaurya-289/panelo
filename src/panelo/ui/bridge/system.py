"""Small Qt objects the Settings page talks to: the hotkey recorder and the sound player."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol

from PySide6.QtCore import QObject, QUrl, Slot

from panelo.core.exceptions import HotkeyRegistrationError
from panelo.desktop.hotkey import Hotkey, parse_hotkey

WINDOWS_MEDIA: Final = Path(r"C:\Windows\Media")
# (label, candidate files: the first that exists is used)
SOUND_CHOICES: Final[list[tuple[str, list[str]]]] = [
    ("Notify", ["Windows Notify System Generic.wav", "Windows Notify.wav"]),
    ("Ding", ["Windows Ding.wav"]),
    ("Chimes", ["chimes.wav"]),
    ("Chord", ["chord.wav"]),
    ("Balloon", ["Windows Balloon.wav"]),
    ("Alarm", ["Alarm01.wav"]),
    ("Ring", ["Ring01.wav"]),
]


def clock_is_12h(time_format: str) -> bool:
    """Does Windows show a 12-hour clock?  ``time_format`` is
    ``QLocale.system().timeFormat(ShortFormat)``: a lowercase "h" or an AM/PM marker ("AP",
    "ap") means 12-hour ("h:mm AP"), "HH:mm" means 24-hour."""
    return "h" in time_format or "ap" in time_format.lower()


def friendly_hotkey_error(message: str) -> str:
    """The Settings page's wording for ``parse_hotkey``'s errors."""
    low = message.lower()
    if "two modifiers" in low:
        return "needs two modifiers (Ctrl, Alt or Shift)"
    if "win" in low:
        return "the Win key isn't supported"
    if "unsupported key" in low:
        return "that key isn't supported (use A-Z, 0-9, Space or F1-F11)"
    return message


class HotkeyReplacer(Protocol):
    def replace(self, candidate: Hotkey, persist: Callable[[str], None]) -> None: ...


class HotkeyBridge(QObject):
    """The Settings page's press-to-record box talks to this (QML name: ``Hotkeys``).

    It validates a combination with ``parse_hotkey``, then re-registers the real global hotkey
    through the existing ``HotkeyManager.replace()``, which calls ``persist`` (saving the
    setting) only once the new combination is registered.  QML shows the result.
    """

    def __init__(
        self, manager: HotkeyReplacer | None = None, persist: Callable[[str], None] | None = None
    ) -> None:
        super().__init__()
        self.manager = manager
        self._persist_hotkey = persist or (lambda _canonical: None)

    @Slot(str, result="QVariantMap")
    def tryHotkey(self, text: str) -> dict[str, Any]:  # noqa: N802
        try:
            candidate = parse_hotkey(text)
        except ValueError as error:
            return {"ok": False, "error": friendly_hotkey_error(str(error)), "text": text}
        if self.manager is None:
            return {"ok": False, "error": "global hotkeys aren't available", "text": text}
        try:
            self.manager.replace(candidate, self._persist_hotkey)
        except HotkeyRegistrationError:
            return {"ok": False, "error": "already used by another app", "text": text}
        except Exception as error:
            return {"ok": False, "error": str(error), "text": text}
        return {"ok": True, "error": "", "text": candidate.canonical}


class SoundPlayer(QObject):
    """Plays the notification sound and the previews in Settings (QML name: ``Sounds``).

    One ``QSoundEffect``, so starting a new sound stops the previous one.  With ``stub=True``
    (self-test, screenshots, tests) nothing is played: the calls are recorded in ``calls`` as
    ("preview" | "play", path) and ``stops`` counts the ``stop()`` calls.
    """

    def __init__(self, *, stub: bool = False, media_dir: Path = WINDOWS_MEDIA) -> None:
        super().__init__()
        self.stub = stub
        self.media_dir = media_dir
        self.calls: list[tuple[str, str]] = []
        self.stops = 0
        self._effect: Any = None

    def options(self) -> list[dict[str, str]]:
        """"None" first, then the Windows sounds that exist on this PC (path "" means none)."""
        found = [{"path": "", "label": "None"}]
        for label, candidates in SOUND_CHOICES:
            for name in candidates:
                path = self.media_dir / name
                if path.is_file():
                    found.append({"path": str(path), "label": label})
                    break
        return found

    @Slot(result="QVariantList")
    def soundOptions(self) -> list[dict[str, str]]:  # noqa: N802
        return self.options()

    def _start(self, kind: str, path: str) -> None:
        self.stop()
        if not path or not Path(path).is_file():
            return
        self.calls.append((kind, path))
        if self.stub:
            return
        if self._effect is None:
            from PySide6.QtMultimedia import QSoundEffect  # noqa: PLC0415

            self._effect = QSoundEffect(self)
            self._effect.setVolume(0.8)
        self._effect.setSource(QUrl.fromLocalFile(path))
        self._effect.play()

    @Slot(str)
    def preview(self, path: str) -> None:
        """Play a sound once as a preview (stopping the previous one).  "" just stops."""
        self._start("preview", path)

    @Slot(str)
    def play(self, path: str) -> None:
        """Play the chosen notification sound (an alert just appeared)."""
        self._start("play", path)

    @Slot()
    def stop(self) -> None:
        self.stops += 1
        if self._effect is not None:
            self._effect.stop()
