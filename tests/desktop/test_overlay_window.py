from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from productivity_app.desktop.ui.files import FilesPanel
from productivity_app.desktop.ui.panels import NotesPanel
from productivity_app.desktop.window import ShellWindow
from productivity_app.windows import presentation


@pytest.fixture
def overlay(qapplication: QApplication) -> Iterator[ShellWindow]:
    window = ShellWindow(lambda: False, lambda: None)
    window.resize(1240, 760)
    window.show()
    window.activateWindow()
    qapplication.processEvents()
    yield window
    window.close()
    window.deleteLater()
    qapplication.processEvents()


def test_overlay_flags_and_escape_from_child(overlay: ShellWindow, qapplication: QApplication) -> None:
    assert overlay.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert overlay.windowOpacity() == 1.0
    spy = QSignalSpy(overlay.hide_requested)
    notes = overlay.dashboard.panels["notes"]
    assert isinstance(notes, NotesPanel)
    notes.search.setFocus()
    QTest.keyClick(notes.search, Qt.Key.Key_Escape)
    assert spy.count() == 1
    overlay.hide_button.click()
    assert spy.count() == 2


def test_hide_show_preserves_hwnd_files_state_and_focus(overlay: ShellWindow, qapplication: QApplication) -> None:
    files = overlay.dashboard.panels["files"]
    assert isinstance(files, FilesPanel)
    files.enter_folder("study")
    files.parent_tab.setFocus()
    hwnd = int(overlay.winId())
    overlay.hide()
    overlay.show()
    overlay.activateWindow()
    # The offscreen plugin has no OS foreground manager. Simulate its grant;
    # real Windows foreground acceptance remains a manual gate.
    qapplication.setActiveWindow(overlay)
    overlay.focus_dashboard()
    qapplication.processEvents()
    assert int(overlay.winId()) == hwnd
    assert files.current_folder_id == "study"
    assert qapplication.focusWidget() is files.parent_tab
    overlay.set_opaque_fallback(True)
    assert "rgb(13, 17, 23)" in overlay.surface.styleSheet()
    overlay.set_opaque_fallback(False)
    assert int(overlay.winId()) == hwnd


def test_background_moves_window_but_panels_do_not(overlay: ShellWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    moves: list[bool] = []
    resizes: list[Qt.Edge] = []
    monkeypatch.setattr(overlay, "_start_system_move", lambda: moves.append(True) or True)
    monkeypatch.setattr(overlay, "_start_system_resize", lambda edges: resizes.append(edges) or True)
    QTest.mouseClick(overlay.dashboard.canvas, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert moves == [True]
    panel = overlay.dashboard.panels["notes"]
    QTest.mouseClick(panel.header, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert moves == [True]
    QTest.mouseClick(overlay.surface, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert resizes == [Qt.Edge.LeftEdge | Qt.Edge.TopEdge]
    assert overlay.resize_edges(QPoint(overlay.width() - 1, overlay.height() - 1)) == Qt.Edge.RightEdge | Qt.Edge.BottomEdge


def test_window_backdrop_is_once_per_handle_and_failure_is_nonfatal(overlay: ShellWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    hwnd = int(overlay.winId())

    def apply(handle: int) -> presentation.BackdropResult:
        calls.append(handle)
        return presentation.BackdropResult(presentation.BackdropMode.OPAQUE, "High contrast")

    monkeypatch.setattr(QApplication, "platformName", lambda: "windows")
    monkeypatch.setattr(presentation, "apply_backdrop", apply)
    overlay.showEvent(QShowEvent())
    overlay.showEvent(QShowEvent())
    assert calls == [hwnd]
    assert overlay.backdrop_result.mode == presentation.BackdropMode.OPAQUE
    assert "rgb(13, 17, 23)" in overlay.surface.styleSheet()
    assert int(overlay.winId()) == hwnd

    def fail(handle: int) -> presentation.BackdropResult:
        raise RuntimeError("Cosmetic adapter failure")

    overlay._backdrop_hwnd = None
    monkeypatch.setattr(presentation, "apply_backdrop", fail)
    overlay.showEvent(QShowEvent())
    assert overlay.backdrop_result.mode == presentation.BackdropMode.ALPHA_ONLY
    assert "Cosmetic adapter failure" in overlay.backdrop_result.reason
    assert "rgba(13, 17, 23, 115)" in overlay.surface.styleSheet()
    monkeypatch.setattr(presentation, "request_foreground", lambda handle: handle == hwnd)
    assert overlay.request_foreground()
    assert int(overlay.winId()) == hwnd
