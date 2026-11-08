from __future__ import annotations

import pytest

from panelo.core.exceptions import NotFoundError, StaleVersionError
from panelo.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery, UpdateNote


def test_two_readers_cannot_overwrite_a_note(application) -> None:
    note = application.notes.create_note(CreateNote(title="Original"))
    first_note = application.notes.get_note(note.id)
    second_note = application.notes.get_note(note.id)
    application.notes.update_note(
        first_note.id, UpdateNote(expected_version=first_note.version, title="A")
    )
    with pytest.raises(StaleVersionError):
        application.notes.update_note(
            second_note.id, UpdateNote(expected_version=second_note.version, title="B")
        )
    assert application.notes.get_note(note.id).title == "A"


def test_note_lifecycle_filters_search_and_concurrency(application) -> None:
    note = application.notes.create_note(CreateNote(title="100%_done", content="needle"))
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
