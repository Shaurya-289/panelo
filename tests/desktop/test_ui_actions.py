from types import SimpleNamespace

import pytest

from productivity_app.desktop.ui.actions import Action, dispatch, save_relationships
from productivity_app.schemas.notes import UpdateNote


def test_dispatch_rejects_unregistered_endpoint():
    with pytest.raises(ValueError):
        dispatch(object(), Action("__dict__.clear"))


def test_command_snapshot_is_immutable_and_preserves_partial_fields():
    command = UpdateNote(expected_version=3, title="Original input")
    action = Action("notes.update_note", ("note-id", command))
    command.title = "Changed after queueing"
    captured = []
    facade = SimpleNamespace(
        notes=SimpleNamespace(update_note=lambda identifier, value: captured.append(value))
    )
    dispatch(facade, action)
    assert captured[0].title == "Original input"
    assert captured[0].model_fields_set == {"expected_version", "title"}
    assert captured[0] is not command


def test_partial_relationship_failure_never_claims_unperformed_text_commit():
    row = SimpleNamespace(id="row", folder_id=None, tag_ids=(), version=2)

    def fail(*args):
        raise ValueError("Rejected tags")

    app = SimpleNamespace(notes=SimpleNamespace(set_note_tags=fail))
    result = save_relationships(app, "notes", row, None, ("tag",), text_saved=False)
    assert result.completed == () and result.error is not None and result.view is row
