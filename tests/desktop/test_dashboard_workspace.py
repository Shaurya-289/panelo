from PySide6.QtWidgets import QApplication

from productivity_app.desktop.ui.dashboard import PANEL_ORDER, WIDE_PLACEMENTS, DashboardWorkspace
from productivity_app.desktop.ui.files import FilesPanel
from productivity_app.desktop.ui.panel import DashboardPanel
from productivity_app.desktop.ui.theme import COMPACT_ENTER_WIDTH, WIDE_RETURN_WIDTH


def test_wide_dashboard_has_five_panels_in_exact_locations(qapplication: QApplication) -> None:
    workspace = DashboardWorkspace()
    workspace.resize(1240, 760)
    workspace.show()
    qapplication.processEvents()
    assert tuple(workspace.panels) == PANEL_ORDER
    assert len(workspace.findChildren(DashboardPanel)) == 5
    for panel_id, expected in WIDE_PLACEMENTS.items():
        index = workspace.grid.indexOf(workspace.panels[panel_id])
        assert workspace.grid.getItemPosition(index) == expected
    assert [workspace.grid.columnStretch(i) for i in range(3)] == [44, 32, 24]
    assert workspace.grid.getItemPosition(workspace.grid.indexOf(workspace.panels["notes"])) == (0, 0, 2, 1)
    workspace.close()
    workspace.deleteLater()


def test_compact_reflow_preserves_instances_navigation_focus_and_hysteresis(qapplication: QApplication) -> None:
    workspace = DashboardWorkspace()
    workspace.resize(1240, 760)
    workspace.show()
    qapplication.processEvents()
    originals = dict(workspace.panels)
    files = workspace.panels["files"]
    assert isinstance(files, FilesPanel)
    files.enter_folder("study")
    files.folder_buttons["semester1"].setFocus()
    for _ in range(5):
        workspace.resize(COMPACT_ENTER_WIDTH - 1, 600)
        qapplication.processEvents()
        assert workspace.is_compact
        for row, panel_id in enumerate(PANEL_ORDER):
            panel = workspace.panels[panel_id]
            assert panel is originals[panel_id] and panel.is_compact
            assert workspace.grid.getItemPosition(workspace.grid.indexOf(panel)) == (row, 0, 1, 1)
        workspace.resize(WIDE_RETURN_WIDTH - 1, 600)
        qapplication.processEvents()
        assert workspace.is_compact
        workspace.resize(WIDE_RETURN_WIDTH, 600)
        qapplication.processEvents()
        assert not workspace.is_compact
        assert files.current_folder_id == "study"
        assert qapplication.focusWidget() is files.folder_buttons["semester1"]
        assert len(workspace.findChildren(DashboardPanel)) == 5
        assert workspace.grid.count() == 5
    workspace.resize(520, 360)
    qapplication.processEvents()
    assert workspace.scroll_area.verticalScrollBar().maximum() > 0
    assert workspace.canvas.minimumSizeHint().width() <= workspace.scroll_area.viewport().width()
    workspace.close()
    workspace.deleteLater()
