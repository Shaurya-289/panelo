from productivity_app.desktop.ui.dashboard import DashboardWorkspace
from productivity_app.desktop.ui.workspace_state import default_layout


def test_maximize_compact_wide_keep_instances_and_canonical_geometry(qapplication):
    workspace = DashboardWorkspace()
    workspace.resize(1240, 760)
    workspace.enable_freeform()
    identities = {key: id(panel) for key, panel in workspace.panels.items()}
    logical = workspace.logical.copy()
    workspace.maximize("notes")
    workspace.set_compact(True)
    assert workspace.maximized == "notes"
    assert workspace.panels["todo"].isHidden()
    workspace.set_compact(False)
    workspace.maximize(None)
    assert workspace.logical == logical == default_layout()
    assert {key: id(panel) for key, panel in workspace.panels.items()} == identities
    workspace.resize(900, 600)
    assert workspace.logical == logical
    workspace.deleteLater()


def test_dirty_editor_survives_maximize_and_compact(final_ui):
    notes = final_ui.presenters["notes"].view
    notes.fields["content"].setPlainText("still unsaved")
    final_ui.workspace.maximize("notes")
    final_ui.workspace.set_compact(True)
    final_ui.workspace.set_compact(False)
    final_ui.workspace.maximize(None)
    assert notes.text("content") == "still unsaved" and notes.dirty
