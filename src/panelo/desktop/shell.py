"""The desktop shell: the windows, the hotkey state machine, the toast and the tray.

``Controller`` owns the Full App window, the Quick Overlay, the alert toast and the tray icon,
and runs the overlay's life cycle (hidden -> open -> closing | morphing -> hidden) from the
global hotkey.  It is a port of the prototype's controller, now fed by the real ``Backend``
(reminders that came due, finished timers and focus phases) instead of a fake store.

Everything that touches the screen, the wallpaper or a native window is kept here, so tests can
swap it: ``use_fake_backdrop`` never captures the real screen, and ``tray=False`` builds no tray
icon.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPointF, QRect, QSettings, QSize, Qt, QTimer, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QRadialGradient,
    QWindow,
)
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from panelo.windows.presentation import request_foreground

_LOGGER = logging.getLogger("panelo.desktop.shell")

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


def style_full_window(window: Any, theme: Any) -> None:
    """Dark title bar and rounded corners. The window is opaque (no Acrylic): its glass
    look is the blurred wallpaper drawn by QML."""
    hwnd = int(window.winId())
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

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
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


APP_ICON_PATH = Path(__file__).resolve().parent.parent / "ui" / "icons" / "app-icon.svg"


def make_tray_icon() -> QIcon:
    """The app icon (also used for the windows and the taskbar)."""
    return QIcon(str(APP_ICON_PATH))


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
                x, y, w, h = (int(str(settings.value(key, 0))) for key in ("x", "y", "w", "h"))
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


class _FrameWatcher(QObject):
    """Counts a window's frameSwapped signals. frameSwapped comes from the render thread; a
    QObject receiver makes Qt queue the call onto the main thread, where timers work."""

    def __init__(self, window: Any, wanted: int, callback: Callable[[], None]) -> None:
        super().__init__()
        self._wanted, self._callback, self._seen, self.done = wanted, callback, 0, False
        self._window = window
        window.frameSwapped.connect(self._on_frame)

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
            self._window.frameSwapped.disconnect(self._on_frame)
        self._callback()


class Controller(QObject):
    """Owns the windows and the hotkey state machine.

    overlay state: hidden -> open -> (closing | morphing) -> hidden
    """

    def __init__(
        self,
        app: QApplication,
        full: Any,
        overlay: Any,
        toast: Any,
        store: Any,
        backend: Any,
        provider: SnapshotProvider,
        *,
        use_fake_backdrop: bool,
        tray: bool,
        library: object | None = None,
        state: FullWindowState | None = None,
    ) -> None:
        """``full``, ``overlay``, ``toast`` are the QML windows, ``store`` the QML ``Store``
        singleton (the settings the alerts follow) and ``backend`` the Python ``Backend``."""
        super().__init__()
        self.app, self.full, self.overlay, self.toast, self.store = app, full, overlay, toast, store
        self.backend = backend
        self.library = library
        self.screen_override: Callable[[], Any] | None = None   # a test picks the screen
        # Windows only lets the foreground app take focus: one polite request per user action
        # (hotkey, tray, second launch).  A test sets this to None.
        self.foreground: Callable[[int], bool] | None = request_foreground
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
        self.sounds: Any = None                         # the SoundPlayer (a stub under test)
        self._fullscreen = False                       # the focus "zen" view: the window is fullscreen
        self._fs_seen = False                          # the OS reported FullScreen after we asked
        self._fs_maximized = False                     # what to return to when leaving fullscreen
        self._wall_n = 0
        self._wall_cache: dict[tuple[str, float, int, int], str] = {}
        self._provider = provider
        self._fake_backdrop = use_fake_backdrop
        self._state = "hidden"
        self._last_open = 0.0
        self._snapshot_n = 0
        self._pending_section = "notes"
        self.on_shot: Callable[[str], None] | None = None
        self.tray: QSystemTrayIcon | None = None

        overlay.expandRequested.connect(self.morph_to_full)
        overlay.closeRequested.connect(self.close_overlay)
        overlay.closeFinished.connect(self._overlay_closed)
        overlay.morphFinished.connect(self._morph_finished)
        for source in (full, overlay, toast):
            source.shotSaved.connect(self._shot_saved)
        for signal in ("xChanged", "yChanged", "widthChanged", "heightChanged"):
            getattr(full, signal).connect(self._on_full_changed)
        full.visibilityChanged.connect(self._on_full_visibility)
        full.visibleChanged.connect(self._on_full_visible)
        full.fullscreenRequested.connect(self.enter_fullscreen)
        full.fullscreenExitRequested.connect(self.exit_fullscreen)
        backend.reminderFired.connect(self.on_reminder_fired)
        backend.focusCompleted.connect(self.on_focus_completed)
        backend.timerFinished.connect(self.on_timer_finished)
        backend.backupRestored.connect(self.on_backup_restored)
        backend.notice.connect(self.on_notice)
        full.screenChanged.connect(lambda: self.refresh_wallpaper())
        if tray:
            self._build_tray()

    # ── State ────────────────────────────────────────────────────────
    @property
    def overlay_state(self) -> str:
        return self._state

    def overlay_visible(self) -> bool:
        return bool(self.overlay.isVisible())

    def full_visible(self) -> bool:
        return bool(self.full.isVisible()) and not self._prewarming

    # ── Hotkey ───────────────────────────────────────────────────────
    def on_hotkey(self) -> None:
        _LOGGER.debug("hotkey pressed (overlay=%s, full=%s)", self._state, "visible" if self.full_visible() else "hidden")
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
    def _wallpaper_source(self, screen: Any) -> str:
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

    def refresh_wallpaper(self, screen: Any = None) -> None:
        """Reload the wallpaper behind the Full App (on every show and screen change)."""
        screen = screen or self.full.screen()
        self.full.setProperty("backdropSource", self._wallpaper_source(screen))

    def _current_screen(self) -> Any:
        if self.screen_override is not None:
            return self.screen_override()
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
        self.overlay.prepare()
        self.overlay.setScreen(screen)
        self.overlay.setGeometry(geometry)
        # Tell the overlay now where it will morph to, so it can build the target (the "ghost")
        # during the entrance and only move it during the morph.
        self.overlay.setTarget(self._morph_rect(screen))
        self._state = "open"
        self._last_open = time.monotonic()
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.requestActivate()
        if self.foreground is not None:
            self.foreground(int(self.overlay.winId()))
        self.overlay.beginOpen()

    def close_overlay(self) -> None:
        if self._state != "open":
            return
        self._state = "closing"
        self.overlay.beginClose()

    def _overlay_closed(self) -> None:
        self.overlay.hide()
        self._state = "hidden"

    def morph_to_full(self, section: str) -> None:
        if self._state != "open":
            return
        self._state = "morphing"
        self._pending_section = section
        self.overlay.morphTo(section, self._morph_rect(self.overlay.screen()))

    def _morph_rect(self, screen: Any) -> dict[str, Any]:
        """The Full App's target rectangle in overlay coordinates (see _target_rect)."""
        target = self._target_rect(screen)
        origin = self.overlay.geometry().topLeft()
        return {
            "x": target.x() - origin.x(),
            "y": target.y() - origin.y(),
            "width": target.width(),
            "height": target.height(),
            "maximized": self._target_maximized(),
        }

    def _morph_finished(self, section: str) -> None:
        screen = self.overlay.screen()
        if section == "files":
            self.full.setProperty("filesRel", self.overlay.property("filesRel"))
        self.show_full(section, screen=screen)
        # Hide the overlay once the Full App has drawn its first frame (no empty-window flash).
        self._after_full_frame(self._finish_morph)

    def _finish_morph(self) -> None:
        self.overlay.hide()
        self.overlay.prepare()
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
        self.full.requestUpdate()

    # ── Full App window state ────────────────────────────────────────
    def _target_maximized(self) -> bool:
        if self.full_visible():
            return bool(self.full.visibility() == QWindow.Visibility.Maximized)
        return self.state.maximized

    def _screen_named(self, name: str) -> Any:
        return next((s for s in QGuiApplication.screens() if name and s.name() == name), None)

    def _saved_screen(self) -> Any:
        """The screen the Full App was last on, or the one under the cursor when it's gone."""
        screen = self._screen_named(self.state.screen)
        if screen is None and self.state.geometry is not None:
            screen = QGuiApplication.screenAt(self.state.geometry.center())
        return screen or self._current_screen()

    def _normal_on(self, screen: Any) -> QRect:
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

    def _target_rect(self, screen: Any) -> QRect:
        """Where the Full App will be: its live rectangle if it is already showing, the screen's
        available area (no taskbar) if it was maximized, else the saved normal geometry."""
        if self.full_visible():
            visibility = self.full.visibility()
            if visibility == QWindow.Visibility.Maximized:
                return QRect(self.full.screen().availableGeometry())
            if visibility == QWindow.Visibility.Windowed:
                return QRect(self.full.geometry())
        if self.state.maximized:
            return QRect(screen.availableGeometry())
        return self._normal_on(screen)

    def _capture_full(self) -> None:
        """Copy the live window state into self.state (the disk write is debounced)."""
        if self._prewarming or self._fullscreen or not self.full.isVisible():
            return  # (fullscreen is never saved as the window's geometry)
        visibility = self.full.visibility()
        screen = self.full.screen()
        if visibility == QWindow.Visibility.Maximized:
            self.state.maximized = True
            self.state.screen = screen.name()
        elif visibility == QWindow.Visibility.Windowed and not self._restoring:
            geometry = QRect(self.full.geometry())
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
        if self.full.visibility() != QWindow.Visibility.Windowed:
            return
        rect = self._normal_on(self.full.screen())
        if self.full.geometry() != rect:
            self.full.setGeometry(rect)
        self._save_timer.start()

    def save_state(self) -> None:
        """Write the Full App's window state now (at shutdown, a pending debounce would be lost)."""
        self._save_timer.stop()
        with contextlib.suppress(RuntimeError):      # the window may already be gone
            self._capture_full()
        self.state.save()

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
        visibility = self.full.visibility()
        self._fs_maximized = visibility == QWindow.Visibility.Maximized
        if visibility == QWindow.Visibility.Windowed:
            self.state.geometry = QRect(self.full.geometry())
            self.state.maximized = False
        else:
            self.state.maximized = self._fs_maximized
        self._save_timer.stop()
        self.state.save()
        self._fullscreen = True
        self._fs_seen = False
        self.full.setProperty("zenActive", True)
        self.full.showFullScreen()

    def _clear_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        self._fs_seen = False
        self.full.setProperty("zenActive", False)

    def exit_fullscreen(self) -> None:
        """Leave the zen view: the window goes back to maximized, or to its saved normal geometry."""
        if not self._fullscreen:
            return
        self._clear_fullscreen()
        if self._fs_maximized:
            self.full.showMaximized()
            return
        self._restoring = True  # ignore the geometry events of the transition
        self.full.showNormal()
        QTimer.singleShot(60, self._restore_normal)

    # ── Full App ─────────────────────────────────────────────────────
    def show_full(self, section: str | None = None, *, screen: Any = None) -> None:
        """Show the Full App as it was last left: same size and place, maximized if it was."""
        if section:
            self.full.setProperty("section", section)
        if self._prewarming:
            self._finish_prewarm()
        if self.full_visible():
            if self.full.visibility() == QWindow.Visibility.Minimized:
                if self.state.maximized:
                    self.full.showMaximized()
                else:
                    self.full.showNormal()
        else:
            self._place_full(screen or self._saved_screen())
        self.full.raise_()
        self.full.requestActivate()
        if self.foreground is not None:
            self.foreground(int(self.full.winId()))

    def _place_full(self, screen: Any) -> None:
        if screen is not self.full.screen():
            self.full.setScreen(screen)
        self.refresh_wallpaper(screen)
        self.full.setWindowStates(Qt.WindowState.WindowNoState)
        self.full.setGeometry(self._normal_on(screen))
        self._was_maximized = False
        if self.state.maximized:
            self.full.showMaximized()
        else:
            self.full.show()

    def open_full_from_tray(self) -> None:
        self.show_full()

    def hide_full(self) -> None:
        self.full.hide()

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
        _set_ex_style(int(self.full.winId()), _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE, True)
        self.full.setOpacity(0.0)
        self.full.setGeometry(QRect(-20000, -20000, size.width(), size.height()))
        self.full.show()
        watcher = _FrameWatcher(self.full, 3, self._finish_prewarm)
        self._watchers.append(watcher)
        QTimer.singleShot(1500, watcher.finish)

    def _finish_prewarm(self) -> None:
        if not self._prewarming:
            return
        self._watchers = [w for w in self._watchers if not w.done]
        self.full.hide()
        self.full.setOpacity(1.0)
        _set_ex_style(int(self.full.winId()), _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE, False)
        self._prewarming = False
        done, self._prewarm_done = self._prewarm_done, None
        if done is not None:
            done()

    # ── Reminders / toast ────────────────────────────────────────────
    def on_reminder_fired(self, reminder: object) -> None:
        """A reminder came due (the Backend's scheduler says so)."""
        data = reminder if isinstance(reminder, dict) else {}
        title = str(data.get("title", "Reminder"))
        if not bool(self.store.property("reminderAlerts")):
            return  # Settings: reminder alerts are off
        if self._state != "hidden":
            return  # the overlay's Reminders panel already shows it as due
        self._notify(str(data.get("id", "")), title, "Now", "reminder", "Reminder")

    def on_focus_completed(self, message: str) -> None:
        """A Pomodoro phase ran out: tell the user with the same toast as reminders."""
        self.focus_toasts += 1
        self.last_focus_message = message
        _LOGGER.info("focus: %s", message)
        if self._state != "hidden":
            return  # the overlay's Timers panel already shows the new phase
        self._notify("", message, "Focus timer", "focus", "Focus timer")

    def on_timer_finished(self, name: str) -> None:
        """A countdown timer reached 0: the same toast path as reminders."""
        message = f"{name} timer done" if not name.lower().endswith("timer") else f"{name} done"
        self.timer_toasts += 1
        self.last_timer_message = message
        _LOGGER.info("timer: %s", message)
        if self._state != "hidden":
            return  # the overlay's Timers panel already shows it
        self._notify("", message, "Timer", "focus", "Timer")

    def on_backup_restored(self, text: str) -> None:
        """Settings > Backups > Restore went through."""
        self._notify("", text, "Backups", "focus", "Backup restored")

    def on_notice(self, message: str) -> None:
        """Something the user should know (a change that could not be saved)."""
        self._notify("", message, "Panelo", "focus", "Panelo")

    def _notify(self, rid: str, title: str, when: str, kind: str, native_title: str) -> None:
        """Show an alert the way Settings says: the glass toast, a Windows notification, or both."""
        style = str(self.store.property("notificationStyle") or "toast")
        windows = style in ("windows", "both")
        if style in ("toast", "both"):
            try:
                area = QGuiApplication.primaryScreen().availableGeometry()
                self.toast.setX(area.right() + 1 - self.toast.width() - 16)
                self.toast.setY(area.bottom() + 1 - self.toast.height())
                self.toast.present(rid, title, when, kind)
                self.notify_log.append(("toast", title))
                sound = str(self.store.property("notificationSound") or "")
                if sound and self.sounds is not None:
                    self.sounds.play(sound)
            except Exception as error:
                _LOGGER.warning("toast failed (%s); using a Windows notification", error)
                windows = True
        if windows:
            self.notify_log.append(("windows", title))
            if self.tray is not None:
                self.tray.showMessage(native_title, title, QSystemTrayIcon.MessageIcon.Information, 8000)

    # ── Tray ─────────────────────────────────────────────────────────
    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            _LOGGER.warning("no system tray available")
            return
        self.tray = QSystemTrayIcon(make_tray_icon(), self)
        self.tray.setToolTip("Panelo")
        menu = QMenu()
        for label, handler in (
            ("Open overlay", self.open_overlay),
            ("Open full app", self.open_full_from_tray),
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
