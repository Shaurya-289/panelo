"""File-backed regressions for Phase 1 ordering, concurrency, and read contracts."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy.exc import IntegrityError

from productivity_app.core.events import InProcessEventBus
from productivity_app.core.exceptions import (
    DuplicateNameError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.db.unit_of_work import create_unit_of_work_factory
from productivity_app.repositories.folders import FolderRepository
from productivity_app.repositories.tags import TagRepository
from productivity_app.schemas.folders import CreateFolder, MoveFolder, RenameFolder
from productivity_app.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery, UpdateNote
from productivity_app.schemas.tags import CreateTag, RenameTag
from productivity_app.services.folders import FolderService
from productivity_app.services.notes import NoteService
from productivity_app.services.tags import TagService


def _fresh_folder_rows(application, app_config):
    application.engine.dispose()
    connection = sqlite3.connect(app_config.database.path)
    try:
        return connection.execute(
            "SELECT id, parent_id, sort_order, version FROM folders "
            "WHERE deleted_at_utc IS NULL ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("source_index", "destination_count", "destination_position"),
    [(0, 0, 0), (1, 2, 1), (2, 2, 2)],
)
def test_cross_parent_move_is_dense_for_every_source_position(
    application, source_index: int, destination_count: int, destination_position: int
) -> None:
    source = [
        application.folders.create_folder(CreateFolder(name=f"Source {index}"))
        for index in range(3)
    ]
    destination = application.folders.create_folder(CreateFolder(name="Destination"))
    destination_children = [
        application.folders.create_folder(CreateFolder(name=f"Destination {index}", parent_id=destination.id))
        for index in range(destination_count)
    ]

    moving = source[source_index]
    moved = application.folders.move_folder(
        moving.id,
        MoveFolder(
            parent_id=destination.id,
            position=destination_position,
            expected_version=moving.version,
        ),
    )

    roots = application.folders.list_children()
    children = application.folders.list_children(destination.id)
    assert moved.version == moving.version + 1
    assert [item.sort_order for item in roots] == list(range(len(roots)))
    assert [item.sort_order for item in children] == list(range(len(children)))
    assert children[destination_position].id == moving.id
    assert {item.id for item in children} == {moving.id, *(item.id for item in destination_children)}


def test_cross_parent_move_failure_rolls_back_on_a_fresh_connection(
    application, app_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = application.folders.create_folder(CreateFolder(name="First"))
    moving = application.folders.create_folder(CreateFolder(name="Moving"))
    application.folders.create_folder(CreateFolder(name="Last"))
    destination = application.folders.create_folder(CreateFolder(name="Destination"))
    before = _fresh_folder_rows(application, app_config)

    def fail_reorder(self: FolderRepository, *_: object, **__: object) -> None:
        raise IntegrityError("synthetic ordering failure", {}, RuntimeError("synthetic"))

    monkeypatch.setattr(FolderRepository, "reorder", fail_reorder)
    with pytest.raises(IntegrityError):
        application.folders.move_folder(
            moving.id,
            MoveFolder(parent_id=destination.id, position=0, expected_version=moving.version),
        )

    assert _fresh_folder_rows(application, app_config) == before
    assert application.folders.get_folder(first.id).parent_id is None


def test_folder_create_and_delete_serialize_without_an_orphaned_child(
    application, app_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = application.folders.create_folder(CreateFolder(name="Parent"))
    creator_ready, creator_resume = Event(), Event()
    original = FolderRepository.name_exists

    def pause_after_validation(self: FolderRepository, *args: object, **kwargs: object) -> bool:
        result = original(self, *args, **kwargs)
        creator_ready.set()
        assert creator_resume.wait(10)
        return result

    monkeypatch.setattr(FolderRepository, "name_exists", pause_after_validation)
    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(
            application.folders.create_folder, CreateFolder(name="Child", parent_id=parent.id)
        )
        assert creator_ready.wait(10)
        delete_future = executor.submit(application.folders.delete_folder, parent.id, parent.version)
        assert not delete_future.done()
        creator_resume.set()
        child = create_future.result(timeout=10)
        delete_future.result(timeout=10)

    application.engine.dispose()
    connection = sqlite3.connect(app_config.database.path)
    try:
        parent_id, version = connection.execute(
            "SELECT parent_id, version FROM folders WHERE id = ?", (child.id,)
        ).fetchone()
    finally:
        connection.close()
    assert parent_id is None
    assert version == child.version + 1


def test_note_create_and_folder_delete_serialize_without_a_deleted_folder_link(
    application, app_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = application.folders.create_folder(CreateFolder(name="Parent"))
    creator_ready, creator_resume = Event(), Event()
    original = NoteService._active_tags

    def pause_after_validation(
        self: NoteService, *args: object, **kwargs: object
    ) -> tuple[str, ...]:
        result = original(self, *args, **kwargs)
        creator_ready.set()
        assert creator_resume.wait(10)
        return result

    monkeypatch.setattr(NoteService, "_active_tags", pause_after_validation)
    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(
            application.notes.create_note, CreateNote(title="Child", folder_id=parent.id)
        )
        assert creator_ready.wait(10)
        delete_future = executor.submit(application.folders.delete_folder, parent.id, parent.version)
        assert not delete_future.done()
        creator_resume.set()
        note = create_future.result(timeout=10)
        delete_future.result(timeout=10)

    application.engine.dispose()
    connection = sqlite3.connect(app_config.database.path)
    try:
        folder_id, version = connection.execute(
            "SELECT folder_id, version FROM notes WHERE id = ?", (note.id,)
        ).fetchone()
    finally:
        connection.close()
    assert folder_id is None
    assert version == note.version + 1


def test_reorder_and_promotion_version_every_changed_entity_once(application, clock) -> None:
    first = application.folders.create_folder(CreateFolder(name="First"))
    second = application.folders.create_folder(CreateFolder(name="Second"))
    third = application.folders.create_folder(CreateFolder(name="Third"))
    untouched = application.folders.create_folder(CreateFolder(name="Untouched"))
    clock.now += timedelta(seconds=1)
    application.folders.reorder_folder(third.id, 0, third.version)

    first_after = application.folders.get_folder(first.id)
    second_after = application.folders.get_folder(second.id)
    third_after = application.folders.get_folder(third.id)
    untouched_after = application.folders.get_folder(untouched.id)
    assert [first_after.version, second_after.version, third_after.version] == [2, 2, 2]
    assert untouched_after.version == untouched.version
    assert first_after.updated_at_utc == clock.now
    assert untouched_after.updated_at_utc == untouched.updated_at_utc

    parent = application.folders.create_folder(CreateFolder(name="Parent"))
    child = application.folders.create_folder(CreateFolder(name="Child", parent_id=parent.id))
    moved_note = application.notes.create_note(CreateNote(title="Moved", folder_id=parent.id))
    stable_note = application.notes.create_note(CreateNote(title="Stable"))
    clock.now += timedelta(seconds=1)
    application.folders.delete_folder(parent.id, parent.version)

    assert application.folders.get_folder(child.id).version == child.version + 1
    assert application.notes.get_note(moved_note.id).version == moved_note.version + 1
    assert application.notes.get_note(stable_note.id).version == stable_note.version


def test_cross_parent_move_versions_every_changed_folder_once(application, clock) -> None:
    first = application.folders.create_folder(CreateFolder(name="First"))
    moving = application.folders.create_folder(CreateFolder(name="Moving"))
    shifted_source = application.folders.create_folder(CreateFolder(name="Shifted source"))
    destination = application.folders.create_folder(CreateFolder(name="Destination"))
    stable_destination = application.folders.create_folder(
        CreateFolder(name="Stable destination", parent_id=destination.id)
    )
    shifted_destination = application.folders.create_folder(
        CreateFolder(name="Shifted destination", parent_id=destination.id)
    )
    clock.now += timedelta(seconds=1)
    moved = application.folders.move_folder(
        moving.id, MoveFolder(parent_id=destination.id, position=1, expected_version=moving.version)
    )

    changed = (moving, shifted_source, destination, shifted_destination)
    for item in changed:
        assert application.folders.get_folder(item.id).version == item.version + 1
        assert application.folders.get_folder(item.id).updated_at_utc == clock.now
    assert moved.version == moving.version + 1
    assert application.folders.get_folder(first.id).version == first.version
    assert application.folders.get_folder(stable_destination.id).version == stable_destination.version


def test_duplicate_folder_races_translate_narrowly(application, monkeypatch: pytest.MonkeyPatch) -> None:
    root = application.folders.create_folder(CreateFolder(name="Root"))
    other = application.folders.create_folder(CreateFolder(name="Other"))
    application.folders.create_folder(CreateFolder(name="Same", parent_id=root.id))
    monkeypatch.setattr(FolderRepository, "name_exists", lambda *_args, **_kwargs: False)

    with pytest.raises(DuplicateNameError):
        application.folders.create_folder(CreateFolder(name="root"))
    with pytest.raises(DuplicateNameError):
        application.folders.create_folder(CreateFolder(name="same", parent_id=root.id))
    assert application.folders.create_folder(CreateFolder(name="same", parent_id=other.id)).parent_id == other.id


def test_duplicate_tag_race_translates_to_duplicate_name(
    application, monkeypatch: pytest.MonkeyPatch
) -> None:
    application.tags.create_tag(CreateTag(name="Taken"))
    monkeypatch.setattr(TagRepository, "name_exists", lambda *_args, **_kwargs: False)
    with pytest.raises(DuplicateNameError):
        application.tags.create_tag(CreateTag(name="taken"))


def test_stale_claims_cover_every_phase_one_mutator(application) -> None:
    folder = application.folders.create_folder(CreateFolder(name="Folder"))
    destination = application.folders.create_folder(CreateFolder(name="Destination"))
    renamed = application.folders.rename_folder(
        folder.id, RenameFolder(name="Renamed", expected_version=folder.version)
    )
    with pytest.raises(StaleVersionError):
        application.folders.move_folder(
            folder.id, MoveFolder(parent_id=destination.id, position=0, expected_version=folder.version)
        )
    moved = application.folders.move_folder(
        folder.id, MoveFolder(parent_id=destination.id, position=0, expected_version=renamed.version)
    )
    with pytest.raises(StaleVersionError):
        application.folders.reorder_folder(folder.id, 0, renamed.version)
    deleted_folder = application.folders.delete_folder(folder.id, moved.version)
    with pytest.raises(StaleVersionError):
        application.folders.restore_folder(folder.id, moved.version)
    restored_folder = application.folders.restore_folder(folder.id, deleted_folder.version)
    with pytest.raises(StaleVersionError):
        application.folders.delete_folder(folder.id, deleted_folder.version)

    tag = application.tags.create_tag(CreateTag(name="Tag"))
    renamed_tag = application.tags.rename_tag(
        tag.id, RenameTag(name="Renamed tag", expected_version=tag.version)
    )
    with pytest.raises(StaleVersionError):
        application.tags.delete_tag(tag.id, tag.version)
    deleted_tag = application.tags.delete_tag(tag.id, renamed_tag.version)
    with pytest.raises(StaleVersionError):
        application.tags.restore_tag(tag.id, renamed_tag.version)
    restored_tag = application.tags.restore_tag(tag.id, deleted_tag.version)
    with pytest.raises(StaleVersionError):
        application.tags.rename_tag(tag.id, RenameTag(name="Again", expected_version=deleted_tag.version))

    note = application.notes.create_note(CreateNote(title="Note"))
    updated = application.notes.update_note(note.id, UpdateNote(expected_version=note.version, content="v2"))
    with pytest.raises(StaleVersionError):
        application.notes.move_note(note.id, restored_folder.id, note.version)
    moved_note = application.notes.move_note(note.id, restored_folder.id, updated.version)
    with pytest.raises(StaleVersionError):
        application.notes.set_note_tags(note.id, (restored_tag.id,), updated.version)
    tagged = application.notes.set_note_tags(note.id, (restored_tag.id,), moved_note.version)
    with pytest.raises(StaleVersionError):
        application.notes.pin_note(note.id, moved_note.version)
    pinned = application.notes.pin_note(note.id, tagged.version)
    with pytest.raises(StaleVersionError):
        application.notes.unpin_note(note.id, tagged.version)
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
    for field in ("title", "content", "is_pinned", "is_archived"):
        with pytest.raises(ValidationError):
            application.notes.update_note(
                note.id, UpdateNote(expected_version=pinned.version, **{field: None})
            )
    with pytest.raises(ValidationError):
        application.notes.update_note(
            note.id,
            UpdateNote(expected_version=pinned.version, title=None, is_archived=True),
        )


def test_deleted_tag_filter_uses_active_tags_only(application) -> None:
    tag = application.tags.create_tag(CreateTag(name="Tag"))
    note = application.notes.create_note(CreateNote(title="Note", tag_ids=(tag.id,)))
    assert [item.id for item in application.notes.list_notes(NoteQuery(tag_id=tag.id))] == [note.id]
    deleted = application.tags.delete_tag(tag.id, tag.version)
    assert application.notes.list_notes(NoteQuery(tag_id=tag.id)) == ()
    assert application.notes.get_note(note.id).tag_ids == ()
    application.tags.restore_tag(tag.id, deleted.version)
    assert [item.id for item in application.notes.list_notes(NoteQuery(tag_id=tag.id))] == [note.id]


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


def test_representative_folder_tag_and_note_failures_emit_no_events(application, clock) -> None:
    bus = InProcessEventBus()
    observed = []
    for event_type in (
        "FOLDER_CREATED",
        "FOLDER_MOVED",
        "FOLDER_DELETED",
        "TAG_CREATED",
        "TAG_DELETED",
        "NOTE_PINNED",
    ):
        bus.subscribe(event_type, observed.append)
    factory = create_unit_of_work_factory(application.session_factory, bus)
    folders = FolderService(factory, clock)
    tags = TagService(factory, clock)
    notes = NoteService(factory, clock)

    folder = folders.create_folder(CreateFolder(name="Folder"))
    destination = folders.create_folder(CreateFolder(name="Destination"))
    moved = folders.move_folder(
        folder.id, MoveFolder(parent_id=destination.id, position=0, expected_version=folder.version)
    )
    folders.delete_folder(moved.id, moved.version)
    tag = tags.create_tag(CreateTag(name="Tag"))
    with pytest.raises(DuplicateNameError):
        tags.create_tag(CreateTag(name="tag"))
    tags.delete_tag(tag.id, tag.version)
    note = notes.create_note(CreateNote(title="Note"))
    notes.pin_note(note.id, note.version)
    failed_folder = folders.create_folder(CreateFolder(name="Failed"))
    event_count = len(observed)

    with pytest.raises(NotFoundError):
        folders.move_folder(
            failed_folder.id,
            MoveFolder(parent_id="missing", position=0, expected_version=failed_folder.version),
        )
    with pytest.raises(StaleVersionError):
        folders.delete_folder(failed_folder.id, failed_folder.version + 1)
    with pytest.raises(StaleVersionError):
        notes.pin_note(note.id, note.version)
    assert len(observed) == event_count
    assert all(event.occurred_at_utc == clock.now and event.payload == {} for event in observed)
