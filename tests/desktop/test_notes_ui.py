from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from productivity_app.desktop.ui.editors import RecordView
from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.notes import CreateNote, UpdateNote
from productivity_app.schemas.tags import CreateTag
from tests.desktop.test_final_ui_integration import settle


def test_dirty_editor_load_and_plain_text(qapplication):
    view = RecordView(("title", "content", "folder_id"), ("All",))
    view.load(values={"content": "<b>not HTML</b>"})
    assert view.text("content") == "<b>not HTML</b>" and not view.dirty
    view.fields["content"].insertPlainText(" edit")
    assert view.dirty


def test_returned_version_chain_and_partial_save_preserves_input(final_ui, monkeypatch):
    app = final_ui.session.application
    first = app.folders.create_folder(CreateFolder(name="First"))
    second = app.folders.create_folder(CreateFolder(name="Second"))
    tag = app.tags.create_tag(CreateTag(name="Tag"))
    row = app.notes.create_note(CreateNote(title="Before", content="before", folder_id=first.id))
    settle(final_ui)
    presenter = final_ui.presenters["notes"]
    presenter.view.load(row)
    presenter.view.fields["title"].setText("After")
    presenter.view.fields["content"].setPlainText("after")
    control = presenter.view.fields["folder_id"]
    control.setCurrentIndex(control.findData(second.id))
    for i in range(presenter.view.tags.count()):
        if presenter.view.tags.item(i).data(Qt.ItemDataRole.UserRole) == tag.id:
            presenter.view.tags.item(i).setCheckState(Qt.CheckState.Checked)
    original = app.notes.set_note_tags
    monkeypatch.setattr(
        app.notes,
        "set_note_tags",
        lambda *args: (_ for _ in ()).throw(ValueError("Injected tags failure")),
    )
    presenter.save()
    settle(final_ui)
    partial = app.notes.get_note(row.id)
    assert partial.version == 3 and partial.title == "After" and partial.folder_id == second.id
    assert partial.tag_ids == () and presenter.view.dirty
    assert presenter.view.text("content") == "after" and presenter.view.current.version == 3
    assert "remaining changes kept" in presenter.view.status.text()
    monkeypatch.setattr(app.notes, "set_note_tags", original)
    presenter.save()
    settle(final_ui)
    assert app.notes.get_note(row.id).version == 4
    assert app.notes.get_note(row.id).tag_ids == (tag.id,)
    assert not presenter.view.dirty


def test_note_lifecycle_readonly_and_dirty_refresh(final_ui, monkeypatch):
    app = final_ui.session.application
    row = app.notes.create_note(CreateNote(title="Lifecycle"))
    settle(final_ui)
    presenter = final_ui.presenters["notes"]
    presenter.view.load(row)
    for action, field, expected in (
        ("pin_note", "is_pinned", True),
        ("unpin_note", "is_pinned", False),
        ("archive_note", "is_archived", True),
        ("restore_from_archive", "is_archived", False),
        ("delete_note", "deleted_at_utc", True),
        ("restore_note", "deleted_at_utc", False),
    ):
        presenter.mutate(action)
        settle(final_ui)
        value = getattr(presenter.view.current, field)
        assert bool(value) == expected
        if action == "delete_note":
            assert not presenter.view.editor_area.isEnabled()
    presenter.view.fields["content"].setPlainText("unsaved")
    presenter.refresh()
    settle(final_ui)
    assert presenter.view.text("content") == "unsaved" and presenter.view.dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    other = app.notes.create_note(CreateNote(title="Other"))
    presenter.select(other.id)
    assert presenter.view.current.id == row.id
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    presenter.select(other.id)
    settle(final_ui)
    assert app.notes.get_note(row.id).content == "unsaved"
    assert presenter.view.current.id == other.id


def test_stale_save_requires_explicit_review_then_explicit_reapply(final_ui, monkeypatch):
    app = final_ui.session.application
    row = app.notes.create_note(CreateNote(title="Original", content="Original body"))
    settle(final_ui)
    presenter = final_ui.presenters["notes"]
    presenter.view.load(row)
    presenter.view.fields["content"].setPlainText("My kept draft")
    changed = app.notes.update_note(
        row.id, UpdateNote(expected_version=row.version, content="External edit")
    )
    settle(final_ui)
    presenter.save()
    settle(final_ui)
    assert presenter.view.dirty and presenter.view.text("content") == "My kept draft"
    assert app.notes.get_note(row.id).content == "External edit"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    presenter.review_latest()
    settle(final_ui)
    assert presenter.view.current.version == changed.version
    assert app.notes.get_note(row.id).content == "External edit"
    presenter.save()
    settle(final_ui)
    assert app.notes.get_note(row.id).content == "My kept draft"
    assert app.notes.get_note(row.id).version == changed.version + 1


def test_lifecycle_action_cannot_silently_discard_dirty_note(final_ui, monkeypatch):
    app = final_ui.session.application
    row = app.notes.create_note(CreateNote(title="Kept"))
    settle(final_ui)
    p = final_ui.presenters["notes"]
    p.view.load(row)
    p.view.fields["content"].setPlainText("Dirty draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    p.mutate("pin_note")
    settle(final_ui)
    assert not app.notes.get_note(row.id).is_pinned and p.view.dirty
    assert p.view.text("content") == "Dirty draft"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    p.mutate("pin_note")
    settle(final_ui)
    saved = app.notes.get_note(row.id)
    assert saved.is_pinned and saved.content == "Dirty draft"


def test_new_draft_invalidates_in_flight_selection(final_ui, monkeypatch):
    import threading

    ui, app = final_ui, final_ui.session.application
    row = app.notes.create_note(CreateNote(title="Old selection"))
    settle(ui)
    presenter = ui.presenters["notes"]
    original = app.notes.get_note
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.notes, "get_note", slow)
    presenter.select(row.id)
    assert entered.wait(10)
    presenter.intent("new")
    release.set()
    settle(ui)
    assert presenter.view.current is None
    assert presenter.view.text("title") == ""


def test_deleted_inventory_can_page_past_full_active_source_page(final_ui):
    from productivity_app.schemas.notes import NoteQuery

    app = final_ui.session.application
    for index in range(53):
        app.notes.create_note(CreateNote(title=f"Paged {index}"))
    ordered = app.notes.list_notes(NoteQuery(include_deleted=True, limit=200))
    victim = ordered[-1]
    app.notes.delete_note(victim.id, victim.version)
    settle(final_ui)
    p = final_ui.presenters["notes"]
    p.view.mode.setCurrentText("Deleted")
    settle(final_ui)
    assert p.rows == ()
    assert p.view.buttons["next"].isEnabled()
    p.intent("next")
    settle(final_ui)
    assert [row.id for row in p.rows] == [victim.id]
    p.select(victim.id)
    settle(final_ui)
    p.mutate("restore_note")
    settle(final_ui)
    assert app.notes.get_note(victim.id).deleted_at_utc is None
