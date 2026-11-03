from __future__ import annotations

import pytest

from productivity_app.core.exceptions import (
    DuplicateNameError,
    FolderCycleError,
    NotFoundError,
    StaleVersionError,
)
from productivity_app.schemas.folders import CreateFolder, MoveFolder, RenameFolder
from productivity_app.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery, UpdateNote
from productivity_app.schemas.tags import CreateTag, RenameTag


def test_two_readers_cannot_overwrite_note_folder_or_tag(application) -> None:
    folder = application.folders.create_folder(CreateFolder(name="One"))
    other = application.folders.create_folder(CreateFolder(name="Two"))
    note = application.notes.create_note(CreateNote(title="Original", folder_id=folder.id))
    first_note = application.notes.get_note(note.id)
    second_note = application.notes.get_note(note.id)
    application.notes.update_note(
        first_note.id, UpdateNote(expected_version=first_note.version, title="A")
    )
    with pytest.raises(StaleVersionError):
        application.notes.move_note(second_note.id, other.id, second_note.version)
    assert application.notes.get_note(note.id).title == "A"
    assert application.notes.get_note(note.id).folder_id == folder.id

    tag = application.tags.create_tag(CreateTag(name="Tag"))
    first_tag = application.tags.get_tag(tag.id)
    second_tag = application.tags.get_tag(tag.id)
    application.tags.rename_tag(
        first_tag.id, RenameTag(name="First", expected_version=first_tag.version)
    )
    with pytest.raises(StaleVersionError):
        application.tags.rename_tag(
            second_tag.id, RenameTag(name="Second", expected_version=second_tag.version)
        )


def test_two_readers_cannot_overwrite_folder(application) -> None:
    folder = application.folders.create_folder(CreateFolder(name="Original"))
    first = application.folders.get_folder(folder.id)
    second = application.folders.get_folder(folder.id)
    application.folders.rename_folder(
        first.id, RenameFolder(name="First", expected_version=first.version)
    )
    with pytest.raises(StaleVersionError):
        application.folders.rename_folder(
            second.id, RenameFolder(name="Second", expected_version=second.version)
        )


def test_folder_hierarchy_order_move_and_cycle_rejection(application) -> None:
    root = application.folders.create_folder(CreateFolder(name="Study"))
    child = application.folders.create_folder(CreateFolder(name="Physics", parent_id=root.id))
    other = application.folders.create_folder(CreateFolder(name="Personal"))
    assert [item.name for item in application.folders.list_children()] == ["Study", "Personal"]
    moved = application.folders.move_folder(
        child.id, MoveFolder(parent_id=other.id, position=0, expected_version=child.version)
    )
    assert moved.parent_id == other.id
    with pytest.raises(FolderCycleError):
        application.folders.move_folder(
            other.id, MoveFolder(parent_id=child.id, position=0, expected_version=other.version)
        )
    with pytest.raises(DuplicateNameError):
        application.folders.create_folder(CreateFolder(name="  study  "))


def test_folder_delete_promotes_direct_content(application) -> None:
    parent = application.folders.create_folder(CreateFolder(name="Parent"))
    child = application.folders.create_folder(CreateFolder(name="Child", parent_id=parent.id))
    note = application.notes.create_note(CreateNote(title="N", folder_id=parent.id))
    deleted = application.folders.delete_folder(parent.id, parent.version)
    assert deleted.deleted_at_utc is not None
    assert application.folders.get_folder(child.id).parent_id is None
    assert application.notes.get_note(note.id).folder_id is None


def test_tag_lifecycle_and_note_tag_policy(application) -> None:
    tag = application.tags.create_tag(CreateTag(name="Work"))
    note = application.notes.create_note(CreateNote(title="Plan", tag_ids=(tag.id, tag.id)))
    assert application.notes.get_note(note.id).tag_ids == (tag.id,)
    renamed = application.tags.rename_tag(
        tag.id, RenameTag(name="Office", expected_version=tag.version)
    )
    with pytest.raises(StaleVersionError):
        application.tags.delete_tag(tag.id, tag.version)
    application.tags.delete_tag(tag.id, renamed.version)
    assert application.notes.get_note(note.id).tag_ids == ()


def test_note_lifecycle_filters_search_and_concurrency(application) -> None:
    folder = application.folders.create_folder(CreateFolder(name="Inbox"))
    note = application.notes.create_note(
        CreateNote(title="100%_done", content="needle", folder_id=folder.id)
    )
    changed = application.notes.update_note(
        note.id, UpdateNote(expected_version=note.version, is_pinned=True)
    )
    with pytest.raises(StaleVersionError):
        application.notes.update_note(
            note.id, UpdateNote(expected_version=note.version, content="lost")
        )
    assert application.notes.search_notes(NoteSearchQuery(query="100%_done"))[0].id == note.id
    application.notes.archive_note(note.id, changed.version)
    assert application.notes.search_notes(NoteSearchQuery(query="needle")) == ()
    assert (
        application.notes.search_notes(NoteSearchQuery(query="needle", include_archived=True))[0].id
        == note.id
    )
    archived = application.notes.list_notes(NoteQuery(is_archived=True))
    assert [item.id for item in archived] == [note.id]
    deleted = application.notes.delete_note(note.id, application.notes.get_note(note.id).version)
    with pytest.raises(NotFoundError):
        application.notes.get_note(note.id)
    restored = application.notes.restore_note(note.id, deleted.version)
    assert restored.deleted_at_utc is None
