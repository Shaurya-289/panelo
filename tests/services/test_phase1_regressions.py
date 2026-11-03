"""Regressions for note ordering, concurrency, and read contracts."""

from __future__ import annotations

import pytest

from productivity_app.core.events import InProcessEventBus
from productivity_app.core.exceptions import StaleVersionError, ValidationError
from productivity_app.db.unit_of_work import create_unit_of_work_factory
from productivity_app.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery, UpdateNote
from productivity_app.services.notes import NoteService


def test_stale_claims_cover_every_note_mutator(application) -> None:
    note = application.notes.create_note(CreateNote(title="Note"))
    updated = application.notes.update_note(
        note.id, UpdateNote(expected_version=note.version, content="v2")
    )
    with pytest.raises(StaleVersionError):
        application.notes.pin_note(note.id, note.version)
    pinned = application.notes.pin_note(note.id, updated.version)
    with pytest.raises(StaleVersionError):
        application.notes.unpin_note(note.id, updated.version)
    unpinned = application.notes.unpin_note(note.id, pinned.version)
    archived = application.notes.archive_note(note.id, unpinned.version)
    with pytest.raises(StaleVersionError):
        application.notes.restore_from_archive(note.id, unpinned.version)
    unarchived = application.notes.restore_from_archive(note.id, archived.version)
    with pytest.raises(StaleVersionError):
        application.notes.delete_note(note.id, archived.version)
    deleted_note = application.notes.delete_note(note.id, unarchived.version)
    with pytest.raises(StaleVersionError):
        application.notes.restore_note(note.id, unarchived.version)
    restored_note = application.notes.restore_note(note.id, deleted_note.version)
    with pytest.raises(StaleVersionError):
        application.notes.update_note(
            note.id, UpdateNote(expected_version=deleted_note.version, title="stale")
        )
    assert restored_note.version == deleted_note.version + 1


def test_update_note_rejects_explicit_null_and_keeps_omitted_fields(application) -> None:
    note = application.notes.create_note(CreateNote(title="Title", content="Content"))
    pinned = application.notes.update_note(
        note.id, UpdateNote(expected_version=note.version, is_pinned=True)
    )
    assert (pinned.title, pinned.content, pinned.is_pinned) == ("Title", "Content", True)
    for field in ("title", "content", "is_pinned", "is_archived", "color"):
        with pytest.raises(ValidationError):
            application.notes.update_note(
                note.id, UpdateNote(expected_version=pinned.version, **{field: None})
            )
    with pytest.raises(ValidationError):
        application.notes.update_note(
            note.id,
            UpdateNote(expected_version=pinned.version, title=None, is_archived=True),
        )


@pytest.mark.parametrize("term", ["%", "_", "\\"])
def test_literal_metacharacter_exact_titles_rank_before_prefixes(application, term: str) -> None:
    application.notes.create_note(CreateNote(title=f"{term} suffix"))
    exact = application.notes.create_note(CreateNote(title=term))
    assert application.notes.search_notes(NoteSearchQuery(query=term))[0].id == exact.id


@pytest.mark.parametrize(
    "sort",
    ["updated_desc", "updated_asc", "created_desc", "created_asc", "title", "pinned_first"],
)
def test_every_note_sort_has_stable_tied_pages(application, sort: str) -> None:
    for index in range(4):
        application.notes.create_note(CreateNote(title="same", is_pinned=index < 2))
    full = application.notes.list_notes(NoteQuery(sort=sort))
    first_page = application.notes.list_notes(NoteQuery(sort=sort, limit=2, offset=0))
    second_page = application.notes.list_notes(NoteQuery(sort=sort, limit=2, offset=2))
    assert tuple((*first_page, *second_page)) == full
    assert application.notes.list_notes(NoteQuery(sort=sort)) == full


def test_failed_note_mutations_emit_no_events(application, clock) -> None:
    bus = InProcessEventBus()
    observed = []
    for event_type in ("NOTE_CREATED", "NOTE_PINNED", "NOTE_UPDATED"):
        bus.subscribe(event_type, observed.append)
    notes = NoteService(create_unit_of_work_factory(application.session_factory, bus), clock)

    note = notes.create_note(CreateNote(title="Note"))
    pinned = notes.pin_note(note.id, note.version)
    event_count = len(observed)

    with pytest.raises(StaleVersionError):
        notes.pin_note(note.id, note.version)
    with pytest.raises(ValidationError):
        notes.update_note(note.id, UpdateNote(expected_version=pinned.version, title=None))
    assert len(observed) == event_count == 2
    assert all(event.occurred_at_utc == clock.now and event.payload == {} for event in observed)
