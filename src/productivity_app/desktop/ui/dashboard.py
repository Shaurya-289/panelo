"""Stage 1 placement owner, replaceable without rewriting panel contents."""

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from productivity_app.desktop.ui import prompts
from productivity_app.desktop.ui.files import FilesPanel
from productivity_app.desktop.ui.layout_store import LayoutStore
from productivity_app.desktop.ui.panel import DashboardPanel
from productivity_app.desktop.ui.panels import NotesPanel, RemindersPanel, TimersPanel, TodoPanel
from productivity_app.desktop.ui.theme import (
    COMPACT_ENTER_WIDTH,
    PANEL_SPACING,
    WIDE_RETURN_WIDTH,
    WORKSPACE_MARGIN,
    apply_dashboard_theme,
)
from productivity_app.desktop.ui.workspace_geometry import (
    resize_sweep,
    snap,
    snap_resize,
    swapped,
    valid,
)
from productivity_app.desktop.ui.workspace_state import MINIMA, Rect, default_layout

PANEL_ORDER = ("notes", "todo", "files", "timers", "reminders")
WIDE_PLACEMENTS = {
    "notes": (0, 0, 2, 1),
    "todo": (0, 1, 1, 1),
    "files": (1, 1, 1, 1),
    "timers": (0, 2, 1, 1),
    "reminders": (1, 2, 1, 1),
}


class DashboardWorkspace(QWidget):
    layout_changed = Signal()
    presentation_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.is_compact = False
        self.freeform = False
        self.editing = False
        self.maximized: str | None = None
        self.logical = default_layout()
        self.saved_logical = self.logical.copy()
        self.store: LayoutStore | None = None
        self.gesture: tuple[str, QPoint, Rect, str] | None = None
        self.candidate: Rect | None = None
        self.gesture_snapshot: dict[str, Rect] | None = None
        self.held: tuple[int | None, int | None] = (None, None)
        self.scale = 1.0
        self.setAccessibleName("Productivity dashboard")
        apply_dashboard_theme(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.canvas = QWidget()
        self.canvas.setObjectName("dashboardCanvas")
        self.grid = QGridLayout(self.canvas)
        self.grid.setContentsMargins(
            WORKSPACE_MARGIN, WORKSPACE_MARGIN, WORKSPACE_MARGIN, WORKSPACE_MARGIN
        )
        self.grid.setSpacing(PANEL_SPACING)
        self.panels: dict[str, DashboardPanel] = {
            "notes": NotesPanel(self.canvas),
            "todo": TodoPanel(self.canvas),
            "files": FilesPanel(self.canvas),
            "timers": TimersPanel(self.canvas),
            "reminders": RemindersPanel(self.canvas),
        }
        for panel_id in PANEL_ORDER:
            self.grid.addWidget(self.panels[panel_id], *WIDE_PLACEMENTS[panel_id])
        for column, stretch in enumerate((44, 32, 24)):
            self.grid.setColumnStretch(column, stretch)
        self.grid.setRowStretch(0, 1)
        self.grid.setRowStretch(1, 1)
        self.scroll_area.setWidget(self.canvas)
        layout.addWidget(self.scroll_area)
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            lambda _value: self.presentation_changed.emit()
        )

    def enable_freeform(self, store: LayoutStore | None = None) -> None:
        self.freeform = True
        self.store = store
        if store:
            self.logical = store.load()
        self.saved_logical = self.logical.copy()
        while self.grid.count():
            self.grid.takeAt(0)
        # A detached, empty layout no longer owns canvas geometry.
        self._retired_layout_owner = QWidget(self)
        self._retired_layout_owner.hide()
        self._retired_layout_owner.setLayout(self.grid)
        self.canvas.setMinimumSize(0, 0)
        for key, panel in self.panels.items():
            button = QToolButton(panel.controls)
            button.setText("↗")
            button.setAccessibleName(f"Expand {key}")
            button.clicked.connect(lambda _checked=False, key=key: self.maximize(key))
            panel.control_layout.addWidget(button)
            options = QToolButton(panel.controls)
            options.setText("⋯")
            options.setAccessibleName(f"Swap {key}")
            options.clicked.connect(lambda _checked=False, key=key: self.choose_swap(key))
            options.setVisible(self.editing)
            panel.swap_control = options
            panel.control_layout.addWidget(options)
            for target in (panel, panel.header, panel.title_label):
                target.installEventFilter(self)
                target.setMouseTracking(True)
        self._project()

    def choose_swap(self, key: str) -> None:
        if not self.editing:
            return
        prompts.choose(
            self,
            "Swap positions",
            "Panel",
            [p for p in PANEL_ORDER if p != key],
            lambda other: self.swap(key, other),
        )

    def swap(self, first: str, second: str) -> None:
        if not self.editing or first not in self.panels or second not in self.panels:
            return
        self.cancel_gesture()
        candidate = swapped(self.logical, first, second)
        if candidate == self.logical:
            return
        self.logical = candidate
        self._project()
        self._save()

    def set_editing(self, enabled: bool) -> None:
        was_editing = self.editing
        self.cancel_gesture()
        self.editing = enabled and not self.is_compact and self.maximized is None
        for panel in self.panels.values():
            panel.content.setEnabled(not self.editing)
            if panel.swap_control is not None:
                panel.swap_control.setVisible(self.editing)
            panel.header.setCursor(
                Qt.CursorShape.SizeAllCursor if self.editing else Qt.CursorShape.ArrowCursor
            )
        if was_editing and not self.editing and self.logical != self.saved_logical:
            self._save()
        self.presentation_changed.emit()

    def reset_layout(self) -> None:
        self.cancel_gesture()
        self.logical = default_layout()
        self._project()
        self._save()

    def maximize(self, key: str | None) -> None:
        self.cancel_gesture()
        if key is not None and key not in self.panels:
            return
        self.set_editing(False)
        self.maximized = None if key == self.maximized else key
        self._project()

    def _save(self) -> None:
        if self.store:
            try:
                self.store.save(self.logical)
                self.saved_logical = self.logical.copy()
                self.store.diagnostic = ""
            except (OSError, ValueError):
                self.store.diagnostic = "Layout not saved."
        self.layout_changed.emit()

    def cancel_gesture(self) -> bool:
        if self.gesture is None:
            return False
        self.gesture = None
        if self.gesture_snapshot is not None:
            self.logical = self.gesture_snapshot
            self.gesture_snapshot = None
        self.candidate = None
        self.held = (None, None)
        for panel in self.panels.values():
            if panel.property("invalidDrop"):
                panel.setProperty("invalidDrop", False)
                panel.style().unpolish(panel)
                panel.style().polish(panel)
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self._project()
        return True

    def _project(self) -> None:
        if not self.freeform:
            return
        width = max(1, self.scroll_area.viewport().width() - 2 * WORKSPACE_MARGIN)
        self.scale = width / 1180
        if (
            not self.is_compact
            and self.maximized is None
            and any(
                r.w * self.scale < MINIMA[key][0] or r.h * self.scale < MINIMA[key][1]
                for key, r in self.logical.items()
            )
        ):
            self.is_compact = True
        for row, key in enumerate(PANEL_ORDER):
            panel = self.panels[key]
            panel.setMinimumSize(0, 0)
            panel.set_compact(self.is_compact)
            panel.setVisible(self.maximized is None or key == self.maximized)
            if self.maximized:
                panel.setGeometry(
                    WORKSPACE_MARGIN, WORKSPACE_MARGIN, width, max(400, self.height() - 50)
                )
            elif self.is_compact:
                panel.setGeometry(WORKSPACE_MARGIN, WORKSPACE_MARGIN + row * 294, width, 280)
            else:
                r = (
                    self.candidate
                    if self.gesture and self.gesture[0] == key and self.candidate
                    else self.logical[key]
                )
                panel.setGeometry(
                    WORKSPACE_MARGIN + round(r.x * self.scale),
                    WORKSPACE_MARGIN + round(r.y * self.scale),
                    round(r.w * self.scale),
                    round(r.h * self.scale),
                )
        height = (
            max(450, self.height())
            if self.maximized
            else (5 * 294 + 40 if self.is_compact else round(660 * self.scale) + 40)
        )
        self.canvas.setMinimumHeight(height)
        self.presentation_changed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self.freeform and isinstance(event, QMouseEvent) and self.editing:
            panel = next(
                (p for p in self.panels.values() if watched in (p, p.header, p.title_label)), None
            )
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and panel
            ):
                pos = panel.mapFromGlobal(event.globalPosition().toPoint())
                edges = ""
                if pos.x() < 8:
                    edges += "l"
                if pos.x() >= panel.width() - 8:
                    edges += "r"
                if pos.y() < 8:
                    edges += "t"
                if pos.y() >= panel.height() - 8:
                    edges += "b"
                if not edges and watched not in (panel.header, panel.title_label):
                    return False
                self.gesture = (
                    panel.panel_id,
                    event.globalPosition().toPoint(),
                    self.logical[panel.panel_id],
                    edges,
                )
                self.gesture_snapshot = dict(self.logical)
                self.candidate = self.logical[panel.panel_id]
                panel.raise_()
                self.grabMouse()
                return True
        return super().eventFilter(watched, event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.gesture:
            return super().mouseMoveEvent(event)
        key, start, r, edges = self.gesture
        delta = event.globalPosition().toPoint() - start
        dx, dy = round(delta.x() / self.scale), round(delta.y() / self.scale)
        if not edges:
            self.candidate, self.held = snap(
                self.logical, key, Rect(r.x + dx, r.y + dy, r.w, r.h), self.scale, self.held
            )
        else:
            candidate = Rect(
                r.x + (dx if "l" in edges else 0),
                r.y + (dy if "t" in edges else 0),
                r.w + (dx if "r" in edges else -dx if "l" in edges else 0),
                r.h + (dy if "b" in edges else -dy if "t" in edges else 0),
            )
            candidate, self.held = snap_resize(
                self.logical, key, candidate, edges, self.scale, self.held
            )
            self.candidate = resize_sweep(self.logical, key, candidate)
        self.panels[key].setProperty(
            "invalidDrop", not valid({**self.logical, key: self.candidate})
        )
        self.panels[key].style().unpolish(self.panels[key])
        self.panels[key].style().polish(self.panels[key])
        self._project()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.gesture:
            key = self.gesture[0]
            if self.candidate and valid({**self.logical, key: self.candidate}):
                self.logical[key] = self.candidate
                self.gesture_snapshot = None
                self._save()
            self.cancel_gesture()
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent) -> bool:
        if hasattr(self, "gesture") and event.type() in {
            QEvent.Type.Hide,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.UngrabMouse,
        }:
            self.cancel_gesture()
        return super().event(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.freeform:
            self.cancel_gesture()
        width = event.size().width()
        if not self.is_compact and width < COMPACT_ENTER_WIDTH:
            self.set_compact(True)
        elif self.is_compact and width >= WIDE_RETURN_WIDTH:
            self.set_compact(False)
        if self.freeform:
            self._project()

    def set_compact(self, enabled: bool) -> None:
        if self.freeform:
            self.cancel_gesture()
            self.is_compact = enabled
            if enabled:
                self.set_editing(False)
            self._project()
            return
        if self.is_compact == enabled:
            return
        focused = QApplication.focusWidget()
        restore_focus = focused is not None and self.isAncestorOf(focused)
        self.is_compact = enabled
        # Removing layout items does not delete or reparent the panel widgets.
        while self.grid.count():
            self.grid.takeAt(0)
        for row in range(max(5, self.grid.rowCount())):
            self.grid.setRowStretch(row, 0)
        for column in range(3):
            self.grid.setColumnStretch(column, 0)
        for row, panel_id in enumerate(PANEL_ORDER):
            panel = self.panels[panel_id]
            panel.set_compact(enabled)
            panel.setMinimumHeight(240 if enabled else 160)
            placement = (row, 0, 1, 1) if enabled else WIDE_PLACEMENTS[panel_id]
            self.grid.addWidget(panel, *placement)
        if enabled:
            self.grid.setColumnStretch(0, 1)
        else:
            for column, stretch in enumerate((44, 32, 24)):
                self.grid.setColumnStretch(column, stretch)
            self.grid.setRowStretch(0, 1)
            self.grid.setRowStretch(1, 1)
        if restore_focus and focused is not None:
            focused.setFocus()
