from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from productivity_app.desktop.ui.dashboard import DashboardWorkspace
from productivity_app.desktop.ui.workspace_state import Rect


def begin(workspace, key, edges=""):
    workspace.gesture = (key, QPoint(), workspace.logical[key], edges)
    workspace.gesture_snapshot = workspace.logical.copy()
    workspace.candidate = workspace.logical[key]


def release(workspace):
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(),
        QPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    workspace.mouseReleaseEvent(event)


def test_normal_mode_no_movement_invalid_release_exact_rollback(qapplication):
    workspace = DashboardWorkspace()
    workspace.resize(1240, 760)
    workspace.enable_freeform()
    before = workspace.logical.copy()
    header = workspace.panels["notes"].header
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(20, 20),
        QPointF(header.mapToGlobal(QPoint(20, 20))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert not workspace.eventFilter(header, press)
    assert workspace.gesture is None and workspace.logical == before
    workspace.is_compact = False
    workspace.set_editing(True)
    assert all(not panel.content.isEnabled() for panel in workspace.panels.values())
    begin(workspace, "notes")
    workspace.candidate = Rect(250, 0, 507, 660)
    workspace._project()
    assert workspace.logical == before
    release(workspace)
    assert workspace.logical == before and workspace.gesture is None
    workspace.set_editing(False)
    assert all(panel.content.isEnabled() for panel in workspace.panels.values())
    workspace.deleteLater()


def test_pointer_hide_focus_loss_and_swap_cancellation(qapplication):
    workspace = DashboardWorkspace()
    workspace.resize(1240, 760)
    workspace.enable_freeform()
    before = workspace.logical.copy()
    for kind in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
        begin(workspace, "todo")
        workspace.candidate = Rect(530, 0, 361, 323)
        QApplication.sendEvent(workspace, QEvent(kind))
        assert workspace.gesture is None and workspace.logical == before
    workspace.is_compact = False
    workspace.set_editing(True)
    workspace.swap("notes", "timers")
    assert workspace.logical == before
    workspace.swap("todo", "files")
    assert (
        workspace.logical["todo"] == before["files"]
        and workspace.logical["files"] == before["todo"]
    )
    assert workspace.logical["notes"] == before["notes"]
    workspace.deleteLater()


def test_escape_arbitrates_palette_gesture_edit_maximize_hide(final_ui):
    ui = final_ui
    ui.palette.show()
    ui.escape()
    assert ui.palette.isHidden()
    ui.workspace.is_compact = False
    ui.workspace.set_editing(True)
    begin(ui.workspace, "todo")
    ui.escape()
    assert ui.workspace.gesture is None and ui.workspace.editing
    ui.escape()
    assert not ui.workspace.editing
    ui.workspace.maximize("notes")
    ui.escape()
    assert ui.workspace.maximized is None
