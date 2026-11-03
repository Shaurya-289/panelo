from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from productivity_app.core.exceptions import StaleVersionError
from productivity_app.schemas.notes import CreateNote, NoteQuery, UpdateNote

COLORS = ("none", "plum", "rose", "amber", "moss", "teal", "slate", "sand")


def test_a_note_is_uncolored_by_default(application) -> None:
    note = application.notes.create_note(CreateNote(title="Plain"))
    assert note.color == "none"
    assert application.notes.get_note(note.id).color == "none"


@pytest.mark.parametrize("color", COLORS)
def test_every_palette_color_can_be_set_on_create_and_update(application, color: str) -> None:
    created = application.notes.create_note(CreateNote(title="Tinted", color=color))
    assert created.color == color
    other = application.notes.create_note(CreateNote(title="Other"))
    updated = application.notes.update_note(
        other.id, UpdateNote(expected_version=other.version, color=color)
    )
    assert updated.color == color
    assert application.notes.list_notes(NoteQuery())[0].color in COLORS


def test_changing_the_color_publishes_note_updated_and_bumps_the_version(application) -> None:
    note = application.notes.create_note(CreateNote(title="Event"))
    observed: list = []
    application.subscribe("NOTE_UPDATED", observed.append)
    updated = application.notes.update_note(
        note.id, UpdateNote(expected_version=note.version, color="moss")
    )
    assert updated.version == note.version + 1 and updated.color == "moss"
    assert [(event.event_type, event.entity_id) for event in observed] == [("NOTE_UPDATED", note.id)]
    with pytest.raises(StaleVersionError):
        application.notes.update_note(note.id, UpdateNote(expected_version=note.version, color="rose"))
    assert application.notes.get_note(note.id).color == "moss"


def test_color_survives_archive_pin_delete_and_restore(application) -> None:
    note = application.notes.create_note(CreateNote(title="Keep", color="amber"))
    pinned = application.notes.pin_note(note.id, note.version)
    archived = application.notes.archive_note(note.id, pinned.version)
    deleted = application.notes.delete_note(note.id, archived.version)
    restored = application.notes.restore_note(note.id, deleted.version)
    assert (pinned.color, archived.color, deleted.color, restored.color) == ("amber",) * 4


def test_an_unknown_color_is_rejected(application) -> None:
    with pytest.raises(PydanticValidationError):
        CreateNote(title="Bad", color="chartreuse")  # type: ignore[arg-type]
    with pytest.raises(PydanticValidationError):
        UpdateNote(expected_version=1, color="PLUM")  # type: ignore[arg-type]
    note = application.notes.create_note(CreateNote(title="Fine"))
    assert application.notes.get_note(note.id).color == "none"
