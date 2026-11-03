from __future__ import annotations

from datetime import UTC, datetime

import pytest

from productivity_app.core.events import InProcessEventBus
from productivity_app.core.exceptions import DuplicateNameError, NotFoundError, ValidationError
from productivity_app.db.unit_of_work import create_unit_of_work_factory
from productivity_app.repositories.folders import FolderRepository
from productivity_app.schemas.folders import CreateFolder, MoveFolder
from productivity_app.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery
from productivity_app.schemas.tags import CreateTag
from productivity_app.services.notes import NoteService


def test_folder_ordering_destinations_and_restore_conflicts(application) -> None:
    first = application.folders.create_folder(CreateFolder(name="First"))
    second = application.folders.create_folder(CreateFolder(name="Second"))
    third = application.folders.create_folder(CreateFolder(name="Third"))
    application.folders.reorder_folder(third.id, 0, third.version)
    assert [(item.name, item.sort_order) for item in application.folders.list_children()] == [
        ("Third", 0),
        ("First", 1),
        ("Second", 2),
    ]
    first = application.folders.get_folder(first.id)
    deleted = application.folders.delete_folder(first.id, first.version)
    application.folders.create_folder(CreateFolder(name="First"))
    with pytest.raises(DuplicateNameError):
        application.folders.restore_folder(deleted.id, deleted.version)
    second = application.folders.get_folder(second.id)
    with pytest.raises(NotFoundError):
        application.folders.move_folder(
            second.id, MoveFolder(parent_id=deleted.id, position=0, expected_version=second.version)
        )


def test_note_filters_paging_sorting_and_literal_search(application) -> None:
    tag = application.tags.create_tag(CreateTag(name="tag"))
    one = application.notes.create_note(
        CreateNote(title="b\\%_", content="body", tag_ids=(tag.id,))
    )
    application.notes.create_note(CreateNote(title="a", content="needle"))
    three = application.notes.create_note(CreateNote(title="c", content="needle", is_pinned=True))
    assert [item.title for item in application.notes.list_notes(NoteQuery(sort="title"))] == [
        "a",
        "b\\%_",
        "c",
    ]
    ordered = application.notes.list_notes(NoteQuery())
    assert [item.id for item in application.notes.list_notes(NoteQuery(limit=1, offset=1))] == [
        ordered[1].id
    ]
    assert application.notes.list_notes(NoteQuery(limit=1, offset=10)) == ()
    assert [item.id for item in application.notes.list_notes(NoteQuery(tag_id=tag.id))] == [one.id]
    assert [item.id for item in application.notes.list_notes(NoteQuery(sort="pinned_first"))][
        0
    ] == three.id
    assert application.notes.search_notes(NoteSearchQuery(query="\\%_"))[0].id == one.id
    with pytest.raises(ValidationError):
        application.notes.search_notes(NoteSearchQuery(query=" "))


def test_invalid_note_tag_assignment_is_atomic(application) -> None:
    tag = application.tags.create_tag(CreateTag(name="valid"))
    note = application.notes.create_note(CreateNote(title="note", tag_ids=(tag.id,)))
    before = application.notes.get_note(note.id)
    with pytest.raises(NotFoundError):
        application.notes.set_note_tags(note.id, (tag.id, "missing"), before.version)
    after = application.notes.get_note(note.id)
    assert after.tag_ids == (tag.id,)
    assert after.version == before.version


def test_constraint_race_is_translated_to_domain_error(
    application, monkeypatch: pytest.MonkeyPatch
) -> None:
    application.folders.create_folder(CreateFolder(name="Taken"))
    monkeypatch.setattr(FolderRepository, "name_exists", lambda *_args, **_kwargs: False)
    with pytest.raises(DuplicateNameError):
        application.folders.create_folder(CreateFolder(name="taken"))


def test_events_publish_after_commit_and_never_include_note_content(application, clock) -> None:
    bus = InProcessEventBus()
    observed = []
    bus.subscribe("NOTE_CREATED", observed.append)
    factory = create_unit_of_work_factory(application.session_factory, bus)
    service = NoteService(factory, clock)
    note = service.create_note(CreateNote(title="secret", content="full private body"))
    assert [event.event_type for event in observed] == ["NOTE_CREATED"]
    assert observed[0].occurred_at_utc == datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    assert "full private body" not in repr(observed[0].payload)
    assert application.notes.get_note(note.id).title == "secret"
