"""Controller for the prototype: hotkey, overlay, Full App, toast and tray orchestration."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, QSettings, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
    QRadialGradient,
    QWindow,
)
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from productivity_app.core.exceptions import HotkeyRegistrationError
from productivity_app.desktop.hotkey import parse_hotkey

DOUBLE_PRESS_SECONDS = 0.35
DEFAULT_FULL_SIZE = QSize(1320, 820)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_SPI_GETDESKWALLPAPER = 0x0073
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


def _dwm_set_int(hwnd: int, attribute: int, value: int) -> None:
    if sys.platform != "win32":
        return
    data = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data)
    )


def _set_ex_style(hwnd: int, flags: int, on: bool) -> None:
    """Add or remove extended window style bits (used to pre-warm a window invisibly)."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    current = user32.GetWindowLongPtrW(ctypes.c_void_p(hwnd), _GWL_EXSTYLE)
    updated = (current | flags) if on else (current & ~flags)
    user32.SetWindowLongPtrW(ctypes.c_void_p(hwnd), _GWL_EXSTYLE, updated)


def style_full_window(window: QObject, theme: QObject | None) -> None:
    """Dark title bar and rounded corners. The window is opaque (no Acrylic): its glass
    look is the blurred wallpaper drawn by QML."""
    hwnd = int(window.winId())  # type: ignore[attr-defined]
    _dwm_set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    _dwm_set_int(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
    if theme is not None:
        theme.setProperty("acrylic", False)


class SnapshotProvider(QQuickImageProvider):
    """Serves images as image://snapshot/<key>: screen snapshots ("<n>") and wallpapers
    ("wall-<n>"). The last few are kept, so a window never loses the picture it shows."""

    MAX_IMAGES = 4

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._snapshots: dict[str, QImage] = {}
        self._walls: dict[str, QImage] = {}
        self._fallback = QImage(1, 1, QImage.Format.Format_RGB32)
        self._fallback.fill(QColor("#0b0c10"))

    def put(self, key: str, image: QImage) -> None:
        store = self._walls if key.startswith("wall-") else self._snapshots
        store.pop(key, None)
        store[key] = image
        while len(store) > self.MAX_IMAGES:
            store.pop(next(iter(store)))

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:  # noqa: A002, N803
        image = self._walls.get(id)
        if image is None:
            image = self._snapshots.get(id)
        return self._fallback if image is None else image


def fake_backdrop(width: int, height: int) -> QImage:
    """A dark gradient with a few soft colored blobs, for screenshots."""
    scale = 960 / max(1, width)
    image = QImage(960, max(1, round(height * scale)), QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, image.width(), image.height())
    gradient.setColorAt(0.0, QColor("#15171d"))
    gradient.setColorAt(1.0, QColor("#0d0e12"))
    painter.fillRect(image.rect(), gradient)
    w, h = image.width(), image.height()
    blobs = [
        (0.18, 0.25, 0.30, "#3b5bdb"),
        (0.80, 0.20, 0.26, "#9c36b5"),
        (0.62, 0.78, 0.32, "#0c8599"),
        (0.10, 0.85, 0.22, "#e8590c"),
        (0.45, 0.45, 0.18, "#f08c00"),
    ]
    for cx, cy, radius, color in blobs:
        center = QPointF(cx * w, cy * h)
        glow = QRadialGradient(center, radius * w)
        base = QColor(color)
        base.setAlpha(150)
        glow.setColorAt(0.0, base)
        base.setAlpha(0)
        glow.setColorAt(1.0, base)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius * w, radius * w)
    painter.end()
    return image


def wallpaper_path() -> str:
    """The desktop wallpaper file, or "" when Windows reports none."""
    if sys.platform != "win32":
        return ""
    buffer = ctypes.create_unicode_buffer(260)
    ok = ctypes.windll.user32.SystemParametersInfoW(_SPI_GETDESKWALLPAPER, 260, buffer, 0)
    return buffer.value if ok else ""


def wallpaper_image(width: int, height: int, *, fake: bool) -> QImage:
    """The wallpaper scaled to cover a width x height screen (center-crop, like "Fill").
    Falls back to the fake backdrop when there is no wallpaper file or it fails to load."""
    if not fake:
        path = wallpaper_path()
        image = QImage(path) if path else QImage()
        if not image.isNull():
            out_w = min(1600, max(1, width))
            out_h = max(1, round(out_w * height / max(1, width)))
            scaled = image.scaled(
                out_w, out_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            return scaled.copy((scaled.width() - out_w) // 2, (scaled.height() - out_h) // 2, out_w, out_h)
    return fake_backdrop(width, height)


def make_tray_icon() -> QIcon:
    """White rounded square on a dark tile."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#17181c"))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(18, 18, 28, 28, 7, 7)
    painter.end()
    return QIcon(pixmap)


def flatten_png(path: str, background: str = "#202125") -> None:
    """Composite a (possibly translucent) screenshot onto an opaque background."""
    image = QImage(path)
    out = QImage(image.size(), QImage.Format.Format_RGB32)
    out.fill(QColor(background))
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    out.save(path)


class FullWindowState:
    """Where the Full App was last: normal geometry, maximized yes/no and the screen.

    Stored with QSettings in the group `full` (keys x, y, w, h, maximized, screen), so it survives
    restarts. `geometry` is always the *normal* (not maximized) rectangle.
    """

    GROUP = "full"

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings
        self.geometry: QRect | None = None
        self.maximized = False
        self.screen = ""
        self.load()

    def load(self) -> None:
        settings = self.settings
        settings.beginGroup(self.GROUP)
        try:
            self.geometry = None
            if all(settings.contains(key) for key in ("x", "y", "w", "h")):
                x, y, w, h = (int(settings.value(key, 0)) for key in ("x", "y", "w", "h"))  # type: ignore[arg-type]
                if w > 0 and h > 0:
                    self.geometry = QRect(x, y, w, h)
            self.maximized = str(settings.value("maximized", "false")).lower() in ("true", "1")
            self.screen = str(settings.value("screen", ""))
        finally:
            settings.endGroup()

    def save(self) -> None:
        settings = self.settings
        settings.beginGroup(self.GROUP)
        try:
            if self.geometry is not None:
                g = self.geometry
                settings.setValue("x", g.x())
                settings.setValue("y", g.y())
                settings.setValue("w", g.width())
                settings.setValue("h", g.height())
            settings.setValue("maximized", self.maximized)
            settings.setValue("screen", self.screen)
        finally:
            settings.endGroup()
        settings.sync()


def clock_is_12h(time_format: str) -> bool:
    """Does Windows show a 12-hour clock? `time_format` is QLocale.system().timeFormat(ShortFormat):
    a lowercase "h" or an AM/PM marker ("AP", "ap") means 12-hour ("h:mm AP"), "HH:mm" means 24-hour."""
    return "h" in time_format or "ap" in time_format.lower()


def friendly_hotkey_error(message: str) -> str:
    """The Settings page's wording for parse_hotkey's errors."""
    low = message.lower()
    if "two modifiers" in low:
        return "needs two modifiers (Ctrl, Alt or Shift)"
    if "win" in low:
        return "the Win key isn't supported"
    if "unsupported key" in low:
        return "that key isn't supported (use A-Z, 0-9, Space or F1-F11)"
    return message


class StubHotkeyManager:
    """Stands in for HotkeyManager when no real global hotkey may be touched (self-test, screenshots).
    `taken` lists the combinations that behave as registered by another app."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.taken: set[str] = set()

    def replace(self, candidate, persist: Callable[[str], None]) -> None:  # noqa: ANN001
        self.calls.append(candidate.canonical)
        if candidate.canonical in self.taken:
            raise HotkeyRegistrationError("RegisterHotKey failed (stub: already registered)")
        persist(candidate.canonical)


class HotkeyBridge(QObject):
    """The Settings page's press-to-record box talks to this (QML name: Hotkeys). It validates a
    combination with parse_hotkey, then re-registers the real global hotkey through the existing
    HotkeyManager.replace(); QML stores the new value once this says ok."""

    def __init__(self, manager: object | None = None) -> None:
        super().__init__()
        self.manager = manager
        self.persisted = ""

    def _persist(self, canonical: str) -> None:
        self.persisted = canonical

    @Slot(str, result="QVariantMap")
    def tryHotkey(self, text: str) -> dict:  # noqa: N802
        try:
            candidate = parse_hotkey(text)
        except ValueError as error:
            return {"ok": False, "error": friendly_hotkey_error(str(error)), "text": text}
        if self.manager is None:
            return {"ok": False, "error": "global hotkeys aren't available", "text": text}
        try:
            self.manager.replace(candidate, self._persist)  # type: ignore[attr-defined]
        except HotkeyRegistrationError:
            return {"ok": False, "error": "already used by another app", "text": text}
        except Exception as error:
            return {"ok": False, "error": str(error), "text": text}
        return {"ok": True, "error": "", "text": candidate.canonical}


WINDOWS_MEDIA = Path(r"C:\Windows\Media")
# (label, candidate files: the first that exists is used)
SOUND_CHOICES: list[tuple[str, list[str]]] = [
    ("Notify", ["Windows Notify System Generic.wav", "Windows Notify.wav"]),
    ("Ding", ["Windows Ding.wav"]),
    ("Chimes", ["chimes.wav"]),
    ("Chord", ["chord.wav"]),
    ("Balloon", ["Windows Balloon.wav"]),
    ("Alarm", ["Alarm01.wav"]),
    ("Ring", ["Ring01.wav"]),
]


class SoundPlayer(QObject):
    """Plays the notification sound and the previews in Settings (QML name: Sounds).

    One QSoundEffect, so starting a new sound stops the previous one. With `stub=True` (self-test
    and screenshots) nothing is played: the calls are recorded in `calls` as ("preview" | "play",
    path) and `stops` counts the stop() calls.
    """

    def __init__(self, *, stub: bool = False, media_dir: Path = WINDOWS_MEDIA) -> None:
        super().__init__()
        self.stub = stub
        self.media_dir = media_dir
        self.calls: list[tuple[str, str]] = []
        self.stops = 0
        self._effect = None

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
        """Play a sound once as a preview (stopping the previous one). "" just stops."""
        self._start("preview", path)

    @Slot(str)
    def play(self, path: str) -> None:
        """Play the chosen notification sound (a toast just appeared)."""
        self._start("play", path)

    @Slot()
    def stop(self) -> None:
        self.stops += 1
        if self._effect is not None:
            self._effect.stop()


class _FrameWatcher(QObject):
    """Counts a window's frameSwapped signals. frameSwapped comes from the render thread; a
    QObject receiver makes Qt queue the call onto the main thread, where timers work."""

    def __init__(self, window: QObject, wanted: int, callback: Callable[[], None]) -> None:
        super().__init__()
        self._wanted, self._callback, self._seen, self.done = wanted, callback, 0, False
        self._window = window
        window.frameSwapped.connect(self._on_frame)  # type: ignore[attr-defined]

    @Slot()
    def _on_frame(self) -> None:
        self._seen += 1
        if self._seen >= self._wanted:
            self.finish()

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        with contextlib.suppress(RuntimeError, TypeError):
            self._window.frameSwapped.disconnect(self._on_frame)  # type: ignore[attr-defined]
        self._callback()


class Controller(QObject):
    """Owns the windows and the hotkey state machine.

    overlay state: hidden -> open -> (closing | morphing) -> hidden
    """

    def __init__(
        self,
        app: QApplication,
        full: QObject,
        overlay: QObject,
        toast: QObject,
        store: QObject,
        provider: SnapshotProvider,
        *,
        use_fake_backdrop: bool,
        native_toast: bool,
        tray: bool,
        library: object | None = None,
        state: FullWindowState | None = None,
    ) -> None:
        super().__init__()
        self.app, self.full, self.overlay, self.toast, self.store = app, full, overlay, toast, store
        self.library = library
        self.state = state or FullWindowState(QSettings())
        self._prewarming = False
        self._prewarm_done: Callable[[], None] | None = None
        self._restoring = False         # un-maximizing: ignore the OS's idea of the normal geometry
        self._was_maximized = False
        self._watchers: list[_FrameWatcher] = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(lambda: self.state.save())
        self.focus_toasts = 0
        self.last_focus_message = ""
        self.timer_toasts = 0
        self.last_timer_message = ""
        self.notify_log: list[tuple[str, str]] = []   # ("toast" | "windows", text): what _notify did
        self.sounds: SoundPlayer | None = None          # set by main.py (a stub under test)
        self._fullscreen = False                       # the focus "zen" view: the window is fullscreen
        self._fs_seen = False                          # the OS reported FullScreen after we asked
        self._fs_maximized = False                     # what to return to when leaving fullscreen
        self._wall_n = 0
        self._wall_cache: dict[tuple[str, float, int, int], str] = {}
        self._provider = provider
        self._fake_backdrop = use_fake_backdrop
        self._native_toast = native_toast
        self._state = "hidden"
        self._last_open = 0.0
        self._snapshot_n = 0
        self._pending_section = "notes"
        self.on_shot: Callable[[str], None] | None = None
        self.tray: QSystemTrayIcon | None = None

        overlay.expandRequested.connect(self.morph_to_full)  # type: ignore[attr-defined]
        overlay.closeRequested.connect(self.close_overlay)  # type: ignore[attr-defined]
        overlay.closeFinished.connect(self._overlay_closed)  # type: ignore[attr-defined]
        overlay.morphFinished.connect(self._morph_finished)  # type: ignore[attr-defined]
        for source in (full, overlay, toast):
            source.shotSaved.connect(self._shot_saved)  # type: ignore[attr-defined]
        for signal in ("xChanged", "yChanged", "widthChanged", "heightChanged"):
            getattr(full, signal).connect(self._on_full_changed)
        full.visibilityChanged.connect(self._on_full_visibility)  # type: ignore[attr-defined]
        full.visibleChanged.connect(self._on_full_visible)  # type: ignore[attr-defined]
        full.fullscreenRequested.connect(self.enter_fullscreen)  # type: ignore[attr-defined]
        full.fullscreenExitRequested.connect(self.exit_fullscreen)  # type: ignore[attr-defined]
        store.reminderFired.connect(self.on_reminder_fired)  # type: ignore[attr-defined]
        store.focusCompleted.connect(self.on_focus_completed)  # type: ignore[attr-defined]
        store.timerFinished.connect(self.on_timer_finished)  # type: ignore[attr-defined]
        store.backupRestored.connect(self.on_backup_restored)  # type: ignore[attr-defined]
        full.screenChanged.connect(lambda: self.refresh_wallpaper())  # type: ignore[attr-defined]
        if tray:
            self._build_tray()

    # ── State ────────────────────────────────────────────────────────
    @property
    def overlay_state(self) -> str:
        return self._state

    def overlay_visible(self) -> bool:
        return bool(self.overlay.isVisible())  # type: ignore[attr-defined]

    def full_visible(self) -> bool:
        return bool(self.full.isVisible()) and not self._prewarming  # type: ignore[attr-defined]

    # ── Hotkey ───────────────────────────────────────────────────────
    def on_hotkey(self) -> None:
        print(f"hotkey pressed (overlay={self._state}, full={'visible' if self.full_visible() else 'hidden'})", flush=True)
        if self._state == "open":
            if time.monotonic() - self._last_open < DOUBLE_PRESS_SECONDS:
                self.morph_to_full("notes")
            else:
                self.close_overlay()
        elif self._state in ("closing", "morphing"):
            return
        elif self.full_visible():
            self.hide_full()
        else:
            self.open_overlay()

    # ── Overlay ──────────────────────────────────────────────────────
    def _wallpaper_source(self, screen) -> str:  # noqa: ANN001
        """Load the wallpaper for `screen` into the provider and return its image URL."""
        size = screen.geometry().size()
        path = "" if self._fake_backdrop else wallpaper_path()
        try:
            stamp = Path(path).stat().st_mtime if path else 0.0
        except OSError:
            stamp = 0.0
        signature = (path, stamp, size.width(), size.height())
        cached = self._wall_cache.get(signature)
        if cached is not None:  # same file, same screen: reuse the decoded picture
            return cached
        image = wallpaper_image(size.width(), size.height(), fake=self._fake_backdrop)
        self._wall_n += 1
        key = f"wall-{self._wall_n}"
        self._provider.put(key, image)
        self._wall_cache = {signature: f"image://snapshot/{key}"}
        return self._wall_cache[signature]

    def refresh_wallpaper(self, screen=None) -> None:  # noqa: ANN001
        """Reload the wallpaper behind the Full App (on every show and screen change)."""
        screen = screen or self.full.screen()  # type: ignore[attr-defined]
        self.full.setProperty("backdropSource", self._wallpaper_source(screen))

    def _current_screen(self):  # noqa: ANN202
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def open_overlay(self) -> None:
        if self._state != "hidden":
            return
        screen = self._current_screen()
        geometry = screen.geometry()
        # Snapshot first, before the overlay covers the screen.
        if self._fake_backdrop:
            image = fake_backdrop(geometry.width(), geometry.height())
        else:
            image = screen.grabWindow(0).toImage()
            if image.width() > 1600:
                image = image.scaledToWidth(1600, Qt.TransformationMode.SmoothTransformation)
        self._snapshot_n += 1
        self._provider.put(str(self._snapshot_n), image)
        self.overlay.setProperty("backdropSource", f"image://snapshot/{self._snapshot_n}")
        self.overlay.setProperty("wallSource", self._wallpaper_source(screen))
        self.overlay.prepare()  # type: ignore[attr-defined]
        self.overlay.setScreen(screen)  # type: ignore[attr-defined]
        self.overlay.setGeometry(geometry)  # type: ignore[attr-defined]
        # Tell the overlay now where it will morph to, so it can build the target (the "ghost")
        # during the entrance and only move it during the morph.
        self.overlay.setTarget(self._morph_rect(screen))  # type: ignore[attr-defined]
        self._state = "open"
        self._last_open = time.monotonic()
        self.overlay.show()  # type: ignore[attr-defined]
        self.overlay.raise_()  # type: ignore[attr-defined]
        self.overlay.requestActivate()  # type: ignore[attr-defined]
        self.overlay.beginOpen()  # type: ignore[attr-defined]

    def close_overlay(self) -> None:
        if self._state != "open":
            return
        self._state = "closing"
        self.overlay.beginClose()  # type: ignore[attr-defined]

    def _overlay_closed(self) -> None:
        self.overlay.hide()  # type: ignore[attr-defined]
        self._state = "hidden"

    def morph_to_full(self, section: str) -> None:
        if self._state != "open":
            return
        self._state = "morphing"
        self._pending_section = section
        self.overlay.morphTo(section, self._morph_rect(self.overlay.screen()))  # type: ignore[attr-defined]

    def _morph_rect(self, screen) -> dict:  # noqa: ANN001
        """The Full App's target rectangle in overlay coordinates (see _target_rect)."""
        target = self._target_rect(screen)
        origin = self.overlay.geometry().topLeft()  # type: ignore[attr-defined]
        return {
            "x": target.x() - origin.x(),
            "y": target.y() - origin.y(),
            "width": target.width(),
            "height": target.height(),
            "maximized": self._target_maximized(),
        }

    def _morph_finished(self, section: str) -> None:
        screen = self.overlay.screen()  # type: ignore[attr-defined]
        if section == "files":
            self.full.setProperty("filesRel", self.overlay.property("filesRel"))
        self.show_full(section, screen=screen)
        # Hide the overlay once the Full App has drawn its first frame (no empty-window flash).
        self._after_full_frame(self._finish_morph)

    def _finish_morph(self) -> None:
        self.overlay.hide()  # type: ignore[attr-defined]
        self.overlay.prepare()  # type: ignore[attr-defined]
        self._state = "hidden"

    def _after_full_frame(self, callback: Callable[[], None], timeout: int = 150) -> None:
        """Run `callback` (on the main thread) after the Full App's next frame, or after
        `timeout` ms at the latest."""
        watcher: _FrameWatcher

        def run() -> None:
            if watcher in self._watchers:
                self._watchers.remove(watcher)
                callback()

        watcher = _FrameWatcher(self.full, 1, lambda: QTimer.singleShot(0, run))
        self._watchers.append(watcher)
        QTimer.singleShot(timeout, watcher.finish)
        self.full.requestUpdate()  # type: ignore[attr-defined]

    # ── Full App window state ────────────────────────────────────────
    def _target_maximized(self) -> bool:
        if self.full_visible():
            return self.full.visibility() == QWindow.Visibility.Maximized  # type: ignore[attr-defined]
        return self.state.maximized

    def _screen_named(self, name: str):  # noqa: ANN202
        return next((s for s in QGuiApplication.screens() if name and s.name() == name), None)

    def _saved_screen(self):  # noqa: ANN202
        """The screen the Full App was last on, or the one under the cursor when it's gone."""
        screen = self._screen_named(self.state.screen)
        if screen is None and self.state.geometry is not None:
            screen = QGuiApplication.screenAt(self.state.geometry.center())
        return screen or self._current_screen()

    def _normal_on(self, screen) -> QRect:  # noqa: ANN001
        """The saved normal geometry, clamped onto `screen` (default size, centered, if none)."""
        area = screen.availableGeometry()
        saved = self.state.geometry
        if saved is None:
            rect = QRect(area.topLeft(), DEFAULT_FULL_SIZE.boundedTo(area.size()))
            rect.moveCenter(area.center())
            return rect
        rect = QRect(saved.topLeft(), saved.size().boundedTo(area.size()))
        if not area.contains(saved.center()):  # saved on another screen: centered on this one
            rect.moveCenter(area.center())
        x = max(area.left(), min(rect.x(), area.right() + 1 - rect.width()))
        y = max(area.top(), min(rect.y(), area.bottom() + 1 - rect.height()))
        return QRect(x, y, rect.width(), rect.height())

    def _target_rect(self, screen) -> QRect:  # noqa: ANN001
        """Where the Full App will be: its live rectangle if it is already showing, the screen's
        available area (no taskbar) if it was maximized, else the saved normal geometry."""
        if self.full_visible():
            visibility = self.full.visibility()  # type: ignore[attr-defined]
            if visibility == QWindow.Visibility.Maximized:
                return QRect(self.full.screen().availableGeometry())  # type: ignore[attr-defined]
            if visibility == QWindow.Visibility.Windowed:
                return QRect(self.full.geometry())  # type: ignore[attr-defined]
        if self.state.maximized:
            return QRect(screen.availableGeometry())
        return self._normal_on(screen)

    def _capture_full(self) -> None:
        """Copy the live window state into self.state (the disk write is debounced)."""
        if self._prewarming or self._fullscreen or not self.full.isVisible():  # type: ignore[attr-defined]
            return  # (fullscreen is never saved as the window's geometry)
        visibility = self.full.visibility()  # type: ignore[attr-defined]
        screen = self.full.screen()  # type: ignore[attr-defined]
        if visibility == QWindow.Visibility.Maximized:
            self.state.maximized = True
            self.state.screen = screen.name()
        elif visibility == QWindow.Visibility.Windowed and not self._restoring:
            geometry = QRect(self.full.geometry())  # type: ignore[attr-defined]
            if geometry == screen.availableGeometry():
                return  # a maximize whose state change hasn't been reported yet
            self.state.geometry = geometry
            self.state.maximized = False
            self.state.screen = screen.name()

    def _on_full_changed(self) -> None:
        if self._fullscreen:
            return
        self._capture_full()
        if not self._prewarming:
            self._save_timer.start()

    def _on_full_visibility(self, visibility: QWindow.Visibility) -> None:
        if self._fullscreen:
            if visibility == QWindow.Visibility.FullScreen:
                self._fs_seen = True
                return
            if not self._fs_seen or visibility == QWindow.Visibility.Hidden:
                return  # still on the way into fullscreen, or being hidden (see _on_full_visible)
            self._clear_fullscreen()  # the OS took the window out of fullscreen: leave the zen view
        if visibility == QWindow.Visibility.Maximized:
            self._was_maximized = True
        elif visibility == QWindow.Visibility.Windowed and self._was_maximized and not self._prewarming:
            # Restored from maximized: go back to the saved normal geometry, not whatever the OS remembers.
            self._was_maximized = False
            self._restoring = True
            self.state.maximized = False
            QTimer.singleShot(0, self._restore_normal)
            return
        self._capture_full()
        if not self._prewarming:
            self._save_timer.start()

    def _restore_normal(self) -> None:
        self._restoring = False
        if self.full.visibility() != QWindow.Visibility.Windowed:  # type: ignore[attr-defined]
            return
        rect = self._normal_on(self.full.screen())  # type: ignore[attr-defined]
        if self.full.geometry() != rect:  # type: ignore[attr-defined]
            self.full.setGeometry(rect)  # type: ignore[attr-defined]
        self._save_timer.start()

    def _on_full_visible(self, visible: bool) -> None:
        if not visible and not self._prewarming:
            self._clear_fullscreen()  # hiding (hotkey, tray, close) always ends the fullscreen view
            self._save_timer.stop()
            self.state.save()  # on hide

    # ── Fullscreen focus mode (the "zen" view) ───────────────────────
    def full_is_fullscreen(self) -> bool:
        return self._fullscreen

    def enter_fullscreen(self) -> None:
        """Show the focus zen view and put the Full App window into real fullscreen. What the
        window was before (maximized, or its normal geometry) is kept so Esc can return to it."""
        if self._fullscreen or not self.full_visible():
            return
        visibility = self.full.visibility()  # type: ignore[attr-defined]
        self._fs_maximized = visibility == QWindow.Visibility.Maximized
        if visibility == QWindow.Visibility.Windowed:
            self.state.geometry = QRect(self.full.geometry())  # type: ignore[attr-defined]
            self.state.maximized = False
        else:
            self.state.maximized = self._fs_maximized
        self._save_timer.stop()
        self.state.save()
        self._fullscreen = True
        self._fs_seen = False
        self.full.setProperty("zenActive", True)  # type: ignore[attr-defined]
        self.full.showFullScreen()  # type: ignore[attr-defined]

    def _clear_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        self._fs_seen = False
        self.full.setProperty("zenActive", False)  # type: ignore[attr-defined]

    def exit_fullscreen(self) -> None:
        """Leave the zen view: the window goes back to maximized, or to its saved normal geometry."""
        if not self._fullscreen:
            return
        self._clear_fullscreen()
        if self._fs_maximized:
            self.full.showMaximized()  # type: ignore[attr-defined]
            return
        self._restoring = True  # ignore the geometry events of the transition
        self.full.showNormal()  # type: ignore[attr-defined]
        QTimer.singleShot(60, self._restore_normal)

    # ── Full App ─────────────────────────────────────────────────────
    def show_full(self, section: str | None = None, *, screen=None) -> None:  # noqa: ANN001
        """Show the Full App as it was last left: same size and place, maximized if it was."""
        if section:
            self.full.setProperty("section", section)
        if self._prewarming:
            self._finish_prewarm()
        if self.full_visible():
            if self.full.visibility() == QWindow.Visibility.Minimized:  # type: ignore[attr-defined]
                if self.state.maximized:
                    self.full.showMaximized()  # type: ignore[attr-defined]
                else:
                    self.full.showNormal()  # type: ignore[attr-defined]
        else:
            self._place_full(screen or self._saved_screen())
        self.full.raise_()  # type: ignore[attr-defined]
        self.full.requestActivate()  # type: ignore[attr-defined]

    def _place_full(self, screen) -> None:  # noqa: ANN001
        if screen is not self.full.screen():  # type: ignore[attr-defined]
            self.full.setScreen(screen)  # type: ignore[attr-defined]
        self.refresh_wallpaper(screen)
        self.full.setWindowStates(Qt.WindowState.WindowNoState)  # type: ignore[attr-defined]
        self.full.setGeometry(self._normal_on(screen))  # type: ignore[attr-defined]
        self._was_maximized = False
        if self.state.maximized:
            self.full.showMaximized()  # type: ignore[attr-defined]
        else:
            self.full.show()  # type: ignore[attr-defined]

    def open_full_from_tray(self) -> None:
        self.show_full()

    def hide_full(self) -> None:
        self.full.hide()  # type: ignore[attr-defined]

    def prepare_morph_stats(self, target: str) -> None:
        """Used by --morph-stats: pretend the Full App was (not) maximized."""
        self.state.maximized = target == "maximized"

    # ── Pre-warming ──────────────────────────────────────────────────
    def prewarm_full(self, on_done: Callable[[], None] | None = None) -> None:
        """Create the Full App's window and render it once, invisibly, so the first real show
        (the end of the morph) is instant: opacity 0, off-screen, no taskbar button, no focus."""
        if self._prewarming:
            return
        self._prewarm_done = on_done
        self._prewarming = True
        screen = self._saved_screen()
        self.refresh_wallpaper(screen)
        size = self._normal_on(screen).size()
        _set_ex_style(int(self.full.winId()), _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE, True)  # type: ignore[attr-defined]
        self.full.setOpacity(0.0)  # type: ignore[attr-defined]
        self.full.setGeometry(QRect(-20000, -20000, size.width(), size.height()))  # type: ignore[attr-defined]
        self.full.show()  # type: ignore[attr-defined]
        watcher = _FrameWatcher(self.full, 3, self._finish_prewarm)
        self._watchers.append(watcher)
        QTimer.singleShot(1500, watcher.finish)

    def _finish_prewarm(self) -> None:
        if not self._prewarming:
            return
        self._watchers = [w for w in self._watchers if not w.done]
        self.full.hide()  # type: ignore[attr-defined]
        self.full.setOpacity(1.0)  # type: ignore[attr-defined]
        _set_ex_style(int(self.full.winId()), _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE, False)  # type: ignore[attr-defined]
        self._prewarming = False
        done, self._prewarm_done = self._prewarm_done, None
        if done is not None:
            done()

    # ── Reminders / toast ────────────────────────────────────────────
    def fire_demo_reminder(self) -> None:
        self.store.fireDemoReminder()  # type: ignore[attr-defined]

    def on_reminder_fired(self, reminder: object) -> None:
        if hasattr(reminder, "toVariant"):
            reminder = reminder.toVariant()  # type: ignore[attr-defined]
        data = reminder if isinstance(reminder, dict) else {}
        title = str(data.get("title", "Reminder"))
        if not bool(self.store.property("reminderAlerts")):
            return  # Settings: reminder alerts are off
        if self._state != "hidden":
            return  # the overlay's Reminders panel already shows it as due
        self._notify(int(data.get("id", -1)), title, "Now", "reminder", "Reminder")

    def on_focus_completed(self, message: str) -> None:
        """A Pomodoro phase ran out: tell the user with the same toast as reminders."""
        self.focus_toasts += 1
        self.last_focus_message = message
        print(f"focus: {message}", flush=True)
        if self._state != "hidden":
            return  # the overlay's Timers panel already shows the new phase
        self._notify(-1, message, "Focus timer", "focus", "Focus timer")

    def on_timer_finished(self, name: str) -> None:
        """A countdown timer reached 0: the same toast path as reminders."""
        message = f"{name} timer done" if not name.lower().endswith("timer") else f"{name} done"
        self.timer_toasts += 1
        self.last_timer_message = message
        print(f"timer: {message}", flush=True)
        if self._state != "hidden":
            return  # the overlay's Timers panel already shows it
        self._notify(-1, message, "Timer", "focus", "Timer")

    def on_backup_restored(self, text: str) -> None:
        """Settings > Backups > Restore: a fake success toast."""
        self._notify(-1, text, "Backups", "focus", "Backup restored")

    def _notify(self, rid: int, title: str, when: str, kind: str, native_title: str) -> None:
        """Show an alert the way Settings says: the glass toast, a Windows notification, or both."""
        style = "windows" if self._native_toast else str(self.store.property("notificationStyle") or "toast")
        windows = style in ("windows", "both")
        if style in ("toast", "both"):
            try:
                area = QGuiApplication.primaryScreen().availableGeometry()
                self.toast.setX(area.right() + 1 - self.toast.width() - 16)  # type: ignore[attr-defined]
                self.toast.setY(area.bottom() + 1 - self.toast.height())  # type: ignore[attr-defined]
                self.toast.present(rid, title, when, kind)  # type: ignore[attr-defined]
                self.notify_log.append(("toast", title))
                sound = str(self.store.property("notificationSound") or "")
                if sound and self.sounds is not None:
                    self.sounds.play(sound)
            except Exception as error:
                print(f"toast failed ({error}); using a Windows notification", file=sys.stderr)
                windows = True
        if windows:
            self.notify_log.append(("windows", title))
            if self.tray is not None:
                self.tray.showMessage(native_title, title, QSystemTrayIcon.MessageIcon.Information, 8000)

    # ── Tray ─────────────────────────────────────────────────────────
    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("warning: no system tray available", file=sys.stderr)
            return
        self.tray = QSystemTrayIcon(make_tray_icon(), self)
        self.tray.setToolTip("Productivity")
        menu = QMenu()
        for label, handler in (
            ("Open overlay", self.open_overlay),
            ("Open full app", self.open_full_from_tray),
            ("Demo: fire reminder", self.fire_demo_reminder),
            ("Quit", self.quit),
        ):
            action = QAction(label, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_overlay()

    # ── Shutdown / screenshots ───────────────────────────────────────
    def _shot_saved(self, path: str) -> None:
        flatten_png(path)
        if self.on_shot is not None:
            self.on_shot(path)

    def quit(self) -> None:
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()
