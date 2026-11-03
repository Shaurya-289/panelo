import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.tasks import CreateTask
from tests.desktop.test_final_ui_integration import settle

pytestmark = pytest.mark.usefixtures("scripted_inputs")


def test_folder_promotion_preserves_dirty_selected_note(final_ui):
    app = final_ui.session.application
    folder = app.folders.create_folder(CreateFolder(name="Removed parent"))
    note = app.notes.create_note(CreateNote(title="Kept draft", folder_id=folder.id))
    settle(final_ui)
    p = final_ui.presenters["notes"]
    p.view.load(note)
    p.view.fields["content"].setPlainText("Draft after folder deletion")
    app.folders.delete_folder(folder.id, folder.version)
    settle(final_ui)
    assert p.view.dirty and p.view.current.version == note.version
    assert p.view.text("content") == "Draft after folder deletion"
    assert app.notes.get_note(note.id).folder_id is None
    assert app.notes.get_note(note.id).version == note.version + 1


def test_real_hierarchy_parent_and_folder_deletion_invalidation(final_ui, monkeypatch):
    app = final_ui.session.application
    parent = app.folders.create_folder(CreateFolder(name="Parent"))
    child = app.folders.create_folder(CreateFolder(name="Child", parent_id=parent.id))
    note = app.notes.create_note(CreateNote(title="Nested note", folder_id=child.id))
    task = app.tasks.create_task(CreateTask(title="Nested task", folder_id=child.id))
    settle(final_ui)
    files = final_ui.files
    assert parent.id in files.panel.folder_buttons
    files.enter(parent.id)
    settle(final_ui)
    assert child.id in files.panel.folder_buttons
    files.enter(child.id)
    settle(final_ui)
    assert files.panel.parent_tab.text() == "Parent"
    assert files.current.id == child.id
    final_ui.presenters["notes"].view.load(note)
    final_ui.presenters["tasks"].view.load(task)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Delete", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    files.actions()
    settle(final_ui)
    assert child.id in files.known_deleted
    assert files.panel.current_folder_id == parent.id
    assert app.notes.get_note(note.id).folder_id == parent.id
    assert app.tasks.get_task(task.id).folder_id == parent.id
    assert final_ui.presenters["notes"].view.current == app.notes.get_note(note.id)
    assert final_ui.presenters["tasks"].view.current == app.tasks.get_task(task.id)
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: ("Restore known folder" if args[2] == "Action" else child.id, True),
    )
    files.actions()
    settle(final_ui)
    assert app.folders.get_folder(child.id).deleted_at_utc is None


def test_folder_create_rename_move_reorder_and_tags_through_ui(final_ui, monkeypatch):
    app, files = final_ui.session.application, final_ui.files
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("New folder", True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("From UI", True))
    files.actions()
    settle(final_ui)
    row = next(value for value in app.folders.list_children() if value.name == "From UI")
    files.enter(row.id)
    settle(final_ui)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Rename", True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Renamed", True))
    files.actions()
    settle(final_ui)
    assert app.folders.get_folder(row.id).name == "Renamed"
    parent = app.folders.create_folder(CreateFolder(name="Parent target"))
    settle(final_ui)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Move", True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: (parent.id, True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *args, **kwargs: (0, True))
    files.actions()
    settle(final_ui)
    assert app.folders.get_folder(row.id).parent_id == parent.id
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Reorder", True))
    files.actions()
    settle(final_ui)
    assert app.folders.get_folder(row.id).sort_order == 0
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Create", True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("UI tag", True))
    files.tags()
    settle(final_ui)
    tag = app.tags.list_tags()[0]
    for verb in ("Rename", "Delete", "Restore"):

        def choose(*args, verb=verb, **kwargs):
            if args[2] == "Tag":
                return next(value for value in args[3] if tag.id in value), True
            return verb, True

        monkeypatch.setattr(QInputDialog, "getItem", choose)
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Edited tag", True))
        files.tags()
        settle(final_ui)
    assert app.tags.get_tag(tag.id).name == "Edited tag"


def test_folder_scoped_notes_and_deleted_folder_fallback(final_ui, monkeypatch):
    app, files = final_ui.session.application, final_ui.files
    folder = app.folders.create_folder(CreateFolder(name="Scope"))
    note = app.notes.create_note(CreateNote(title="Scoped", folder_id=folder.id))
    app.notes.create_note(CreateNote(title="Outside"))
    settle(final_ui)
    files.enter(folder.id)
    settle(final_ui)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Notes here", True))
    files.actions()
    settle(final_ui)
    p = final_ui.presenters["notes"]
    assert p.folder_filter == folder.id and [row.id for row in p.rows] == [note.id]
    app.folders.delete_folder(folder.id, folder.version)
    settle(final_ui)
    assert p.folder_filter is None and files.panel.current_folder_id is None
    assert len(p.rows) == 2
