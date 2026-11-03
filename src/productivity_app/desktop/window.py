"""Dashboard presentation; process lifetime policy stays in ShellController."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QKeySequence, QMouseEvent, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from productivity_app.desktop.ui.dashboard import DashboardWorkspace
from productivity_app.desktop.ui.panels import NotesPanel
from productivity_app.desktop.ui.theme import DASHBOARD_STYLE, OPAQUE_TINT, SHELL_TINT
from productivity_app.windows import presentation

_LOGGER = logging.getLogger("productivity_app.desktop.window")


class ShellWindow(QMainWindow):
    """One reusable overlay HWND; no panel contains backend or timing logic."""

    hide_requested = Signal()

    def __init__(
        self,
        on_close_requested: Callable[[], bool],
        on_minimized: Callable[[], None],
    ) -> None:
        super().__init__()
        self._on_close_requested = on_close_requested
        self._on_minimized = on_minimized
        self._last_focus: QWidget | None = None
        self._backdrop_hwnd: int | None = None
        self.backdrop_result = presentation.BackdropResult(
            presentation.BackdropMode.ALPHA_ONLY, "Native presentation not attempted"
        )
        self.setObjectName("overlayWindow")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Productivity App")
        self.surface = QWidget(self)
        self.surface.setObjectName("overlaySurface")
        self.surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.set_opaque_fallback(False)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        # No title bar: only two small controls in the outer empty margin.
        controls = QHBoxLayout()
        self.shell_controls = controls
        controls.setContentsMargins(0, 0, 8, 0)
        controls.addStretch()
        self.hide_button = QToolButton(self.surface)
        self.hide_button.setText("−")
        self.hide_button.setAccessibleName("Hide dashboard")
        self.hide_button.setToolTip("Hide dashboard (Esc)")
        self.hide_button.clicked.connect(self.hide_requested.emit)
        self.close_button = QToolButton(self.surface)
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Close dashboard")
        self.close_button.setToolTip("Close using the existing shell preference")
        self.close_button.clicked.connect(self.close)
        for button in (self.hide_button, self.close_button):
            button.setFixedSize(28, 24)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.dashboard = DashboardWorkspace(self.surface)
        layout.addWidget(self.dashboard, 1)
        self.setCentralWidget(self.surface)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.escape_shortcut.setAutoRepeat(False)
        self.escape_shortcut.activated.connect(self.hide_requested.emit)
        for target in (
            self.surface,
            self.dashboard,
            self.dashboard.canvas,
            self.dashboard.scroll_area.viewport(),
        ):
            target.installEventFilter(self)
            target.setMouseTracking(True)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.setMinimumSize(min(360, area.width()), min(300, area.height()))
            self.resize(min(1240, area.width() * 9 // 10), min(760, area.height() * 9 // 10))
            self.move(area.center() - self.rect().center())
        else:
            self.resize(1240, 760)
        qt_app = QApplication.instance()
        if isinstance(qt_app, QApplication):
            qt_app.screenRemoved.connect(self.reposition_on_screen_change)

    def reposition_on_screen_change(self, *_: object) -> None:
        """Keep the same HWND reachable after display removal/work-area changes."""
        screens = QApplication.screens()
        if not screens:
            return
        target = next(
            (
                screen
                for screen in screens
                if screen.availableGeometry().intersects(self.frameGeometry())
            ),
            screens[0],
        )
        area = target.availableGeometry()
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        self.move(
            max(area.left(), min(self.x(), area.right() - self.width() + 1)),
            max(area.top(), min(self.y(), area.bottom() - self.height() + 1)),
        )

    def set_opaque_fallback(self, enabled: bool) -> None:
        tint = OPAQUE_TINT if enabled else SHELL_TINT
        self.surface.setStyleSheet(
            DASHBOARD_STYLE
            + f"\nQWidget#overlaySurface {{ background: {tint}; border-radius: 16px; }}"
        )

    def focus_dashboard(self) -> None:
        target = self._last_focus
        try:
            if target is not None and target.isVisible() and target.isEnabled():
                target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                return
        except RuntimeError:
            # Navigation may have deleted the previously focused folder button.
            self._last_focus = None
        notes = self.dashboard.panels["notes"]
        assert isinstance(notes, NotesPanel)
        notes.search.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def request_foreground(self) -> bool:
        if QApplication.platformName() != "windows":
            return False
        return presentation.request_foreground(int(self.winId()))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        if QApplication.platformName() == "windows" and hwnd != self._backdrop_hwnd:
            try:
                self.backdrop_result = presentation.apply_backdrop(hwnd)
            except Exception as exc:
                # A cosmetic adapter fault must not strand startup resources.
                self.backdrop_result = presentation.BackdropResult(
                    presentation.BackdropMode.ALPHA_ONLY, str(exc)
                )
                _LOGGER.warning("Native presentation failed", exc_info=True)
            self.set_opaque_fallback(self.backdrop_result.mode == presentation.BackdropMode.OPAQUE)
            self._backdrop_hwnd = hwnd

    def hideEvent(self, event: QHideEvent) -> None:
        focused = self.focusWidget()
        if focused is not None and self.isAncestorOf(focused):
            self._last_focus = focused
        super().hideEvent(event)

    def resize_edges(self, position: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if position.x() < 6:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - 6:
            edges |= Qt.Edge.RightEdge
        if position.y() < 6:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - 6:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _start_system_move(self) -> bool:
        handle = self.windowHandle()
        return handle.startSystemMove() if handle is not None else False

    def _start_system_resize(self, edges: Qt.Edge) -> bool:
        handle = self.windowHandle()
        return handle.startSystemResize(edges) if handle is not None else False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, QWidget) and isinstance(event, QMouseEvent):
            position = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self.resize_edges(position)
            if event.type() == QEvent.Type.MouseMove:
                cursor = Qt.CursorShape.ArrowCursor
                if edges in (
                    Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
                    Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
                ):
                    cursor = Qt.CursorShape.SizeFDiagCursor
                elif edges in (
                    Qt.Edge.RightEdge | Qt.Edge.TopEdge,
                    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
                ):
                    cursor = Qt.CursorShape.SizeBDiagCursor
                elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
                    cursor = Qt.CursorShape.SizeHorCursor
                elif edges:
                    cursor = Qt.CursorShape.SizeVerCursor
                watched.setCursor(cursor)
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                if edges:
                    return self._start_system_resize(edges)
                if watched.childAt(event.position().toPoint()) is None:
                    return self._start_system_move()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._on_close_requested():
            event.ignore()
            return
        event.accept()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._on_minimized()
        elif event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.focus_dashboard()
