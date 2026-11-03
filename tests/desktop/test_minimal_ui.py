from types import SimpleNamespace

from PySide6.QtWidgets import QPushButton

from productivity_app.desktop.ui.editors import InlineStatus, RecordView
from productivity_app.schemas.notes import CreateNote
from tests.desktop.test_final_ui_integration import settle


def record_view():
    view = RecordView(("title", "content", "folder_id"), ("All", "Pinned", "Archived"))
    for name, label in (
        ("new", "New"),
        ("save", "Save"),
        ("refresh", "Refresh"),
        ("more", "Actions…"),
    ):
        view.add_button(name, label)
    return view


def test_quiet_default_has_plus_and_menu_not_a_grid_of_actions(qapplication):
    view = record_view()
    view.show()
    qapplication.processEvents()
    assert view.buttons["new"].text() == "+"
    assert view.buttons["new"].accessibleName()
    assert view.menu_button.isVisible()
    assert view.search_bar.isHidden() and view.editor_area.isHidden()
    assert all(view.buttons[name].isHidden() for name in ("save", "refresh", "more"))
    assert [b.text() for b in view.button_area.findChildren(QPushButton) if b.isVisible()] == ["+"]
    view.hide()
    view.deleteLater()


def test_dropdown_preserves_existing_dispatch_and_view_modes(qapplication):
    view = record_view()
    intents, queries = [], []
    view.intent.connect(intents.append)
    view.search_requested.connect(lambda: queries.append(view.mode.currentText()))
    view.menu_actions["refresh"].trigger()
    view.views_menu.actions()[1].trigger()
    view.sync_menu()
    assert intents == ["refresh"] and queries == ["Pinned"]
    assert view.mode.currentText() == "Pinned" and view.mode.isHidden()
    assert view.views_menu.actions()[1].isChecked()
    view.buttons["refresh"].setEnabled(False)
    view.sync_menu()
    assert not view.menu_actions["refresh"].isEnabled()
    view.deleteLater()


def test_search_opens_on_request_and_closing_it_removes_hidden_filter(qapplication):
    view = record_view()
    requested = []
    view.search_requested.connect(lambda: requested.append(view.search.text()))
    view.toggle_search()
    assert not view.search_bar.isHidden()
    view.search.setText("a filter")
    view.toggle_search(False)
    assert view.search_bar.isHidden() and view.search.text() == ""
    assert requested == [""]
    view.deleteLater()


def test_details_are_opt_in_and_dirty_save_stays_available(qapplication):
    view = record_view()
    row = SimpleNamespace(
        id="note", deleted_at_utc=None, model_dump=lambda: {"title": "Saved", "content": "Body"}
    )
    view.load(row)
    view.set_expanded(True)
    assert view.details_area.isHidden() and view.buttons["save"].isHidden()
    view.fields["content"].insertPlainText(" draft")
    assert view.dirty and not view.buttons["save"].isHidden()
    view.details_action.trigger()
    assert not view.details_area.isHidden()
    view.set_expanded(False)
    view.set_expanded(True)
    assert view.dirty and view.text("content") == " draftBody"
    assert not view.details_area.isHidden()
    view.deleteLater()


def test_quiet_status_never_hides_an_error(qapplication):
    status = InlineStatus()
    status.setText("")
    assert status.isHidden()
    status.setText("Could not save; draft kept")
    assert not status.isHidden()
    status.deleteLater()


def test_minimal_dashboard_and_notes_editor(final_ui, qapplication, tmp_path):
    ui, app = final_ui, final_ui.session.application
    for title, content in (
        ("This week", "A little room to think."),
        ("Ideas", "Keep it simple."),
        ("Reading", "One thing at a time."),
    ):
        app.notes.create_note(CreateNote(title=title, content=content))
    settle(ui)
    ui.window.resize(1440, 850)
    ui.window.show()
    qapplication.processEvents()
    ui.presentation_changed()
    for presenter in ui.presenters.values():
        assert presenter.view.editor_area.isHidden()
        assert presenter.view.search_bar.isHidden()
        assert presenter.view.buttons["refresh"].isHidden()
        assert presenter.view.buttons["more"].isHidden()
        assert presenter.view.button_area.parentWidget() is presenter.panel.controls
        assert presenter.panel.swap_control.isHidden()
    assert ui.window.grab().save(str(tmp_path / "dashboard.png"))
    notes = ui.presenters["notes"]
    notes.view.buttons["new"].click()
    assert ui.workspace.maximized == "notes"
    assert not notes.view.editor_area.isHidden() and notes.view.details_area.isHidden()
    assert notes.view.current is None
    notes.view.fields["title"].setText("A quiet note")
    notes.view.fields["content"].setPlainText("Just a title, a page, and room to write.")
    qapplication.processEvents()
    assert ui.window.grab().save(str(tmp_path / "editor.png"))
    notes.save()
    settle(ui)
    assert (
        app.notes.get_note(notes.view.current.id).content
        == "Just a title, a page, and room to write."
    )


def test_note_dropdown_uses_contextual_actions_and_layout_edit_locks_header(final_ui):
    ui = final_ui
    row = ui.session.application.notes.create_note(CreateNote(title="Context"))
    settle(ui)
    notes = ui.presenters["notes"]
    notes.view.load(row)
    notes.populate_note_menu()
    assert [a.text() for a in notes.note_menu.actions()] == [
        "Pin",
        "Archive",
        "Delete",
        "Reload",
        "Review external changes…",
    ]
    notes.note_menu.actions()[0].trigger()
    settle(ui)
    assert ui.session.application.notes.get_note(row.id).is_pinned
    notes.populate_note_menu()
    assert notes.note_menu.actions()[0].text() == "Unpin"
    ui.workspace.is_compact = False
    ui.workspace.set_editing(True)
    assert not notes.view.button_area.isEnabled()
    assert not ui.files.controls.isEnabled()
    assert not notes.panel.swap_control.isHidden()
    ui.workspace.set_editing(False)
    assert notes.view.button_area.isEnabled() and ui.files.controls.isEnabled()
