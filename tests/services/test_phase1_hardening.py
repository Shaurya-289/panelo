from __future__ import annotations

from datetime import UTC, datetime

import pytest

from productivity_app.core.events import InProcessEventBus
from productivity_app.core.exceptions import ValidationError
from productivity_app.db.unit_of_work import create_unit_of_work_factory
from productivity_app.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery
from productivity_app.services.notes import NoteService


def test_note_filters_paging_sorting_and_literal_search(application) -> None:
    one = application.notes.create_note(CreateNote(title="b\\%_", content="body"))
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
    assert [item.id for item in application.notes.list_notes(NoteQuery(sort="pinned_first"))][
        0
    ] == three.id
    assert application.notes.search_notes(NoteSearchQuery(query="\\%_"))[0].id == one.id
    with pytest.raises(ValidationError):
        application.notes.search_notes(NoteSearchQuery(query=" "))


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
