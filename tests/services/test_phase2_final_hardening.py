"""Final adversarial regression coverage for frozen Phase 2 task behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from productivity_app.core.exceptions import NotFoundError, StaleVersionError
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.db.types import datetime_to_epoch_microseconds
from productivity_app.repositories.folders import FolderRepository
from productivity_app.repositories.tasks import TaskRepository
from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.tags import CreateTag
from productivity_app.schemas.tasks import (
    CreateTask,
    TaskCalendarQuery,
    TaskPageQuery,
    TaskQuery,
    TaskSearchQuery,
    UpdateTask,
)

_TASK_EVENT_TYPES = (
    "TASK_CREATED",
    "TASK_UPDATED",
    "TASK_STARTED",
    "TASK_RETURNED_TO_TODO",
    "TASK_COMPLETED",
    "TASK_CANCELLED",
    "TASK_REOPENED",
    "TASK_MOVED",
    "TASK_TAGS_CHANGED",
    "TASK_DELETED",
    "TASK_RESTORED",
)


class _TaskIds:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new(self) -> str:
        return next(self._values)


def _set_task_ids(application, *values: str) -> None:
    application.tasks._ids = _TaskIds(*values)


def _task_events(application) -> list:
    observed: list = []
    bus = application.unit_of_work_factory()._event_bus
    for event_type in _TASK_EVENT_TYPES:
        bus.subscribe(event_type, observed.append)
    return observed


def _fresh_rows(application, statement: str) -> list[tuple[object, ...]]:
    database_path = Path(str(application.engine.url.database))
    fresh = create_sqlite_engine(database_path)
    try:
        with fresh.connect() as connection:
            return [tuple(row) for row in connection.execute(text(statement))]
    finally:
        fresh.dispose()


def _ids(rows) -> set[str]:
    return {row.id for row in rows}


def test_restore_and_update_race_is_serialized_and_durable(application) -> None:
    task = application.tasks.create_task(CreateTask(title="original"))
    deleted = application.tasks.delete_task(task.id, task.version)
    barrier = Barrier(2)

    def restore() -> tuple[str, object]:
        version = application.tasks.get_task(task.id, include_deleted=True).version
        barrier.wait()
        try:
            return "restored", application.tasks.restore_task(task.id, version)
        except (NotFoundError, StaleVersionError) as exc:
            return type(exc).__name__, exc

    def update() -> tuple[str, object]:
        version = application.tasks.get_task(task.id, include_deleted=True).version
        barrier.wait()
        try:
            return "updated", application.tasks.update_task(
                task.id, UpdateTask(expected_version=version, title="lost update")
            )
        except (NotFoundError, StaleVersionError) as exc:
            return type(exc).__name__, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        restore_result, update_result = list(executor.map(lambda fn: fn(), (restore, update)))

    assert restore_result[0] == "restored"
    assert update_result[0] in {"NotFoundError", "StaleVersionError"}
    assert application.tasks.get_task(task.id).model_dump() == {
        **task.model_dump(),
        "version": deleted.version + 1,
        "deleted_at_utc": None,
    }
    assert _fresh_rows(
        application,
        f"SELECT title, deleted_at_utc, version FROM tasks WHERE id = '{task.id}'",
    ) == [("original", None, deleted.version + 1)]


def test_multi_tag_replacement_racing_tag_delete_is_atomic(application) -> None:
    first = application.tags.create_tag(CreateTag(name="First"))
    second = application.tags.create_tag(CreateTag(name="Second"))
    third = application.tags.create_tag(CreateTag(name="Third"))
    task = application.tasks.create_task(CreateTask(title="Tagged", tag_ids=(first.id,)))
    barrier = Barrier(2)

    def replace() -> tuple[str, object]:
        version = application.tasks.get_task(task.id).version
        barrier.wait()
        try:
            return "replaced", application.tasks.set_task_tags(task.id, (second.id, third.id), version)
        except (NotFoundError, StaleVersionError) as exc:
            return type(exc).__name__, exc

    def delete() -> tuple[str, object]:
        version = application.tags.get_tag(second.id).version
        barrier.wait()
        return "deleted", application.tags.delete_tag(second.id, version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        replace_result, delete_result = list(executor.map(lambda fn: fn(), (replace, delete)))

    assert delete_result[0] == "deleted"
    stored = _fresh_rows(
        application,
        f"SELECT tag_id FROM task_tags WHERE task_id = '{task.id}' ORDER BY tag_id",
    )
    stored_ids = tuple(row[0] for row in stored)
    assert stored_ids in {(first.id,), tuple(sorted((second.id, third.id)))}
    assert len(stored_ids) == len(set(stored_ids))
    active = application.tasks.get_task(task.id).tag_ids
    if replace_result[0] == "replaced":
        assert stored_ids == tuple(sorted((second.id, third.id)))
        assert active == (third.id,)
    else:
        assert replace_result[0] == "NotFoundError"
        assert stored_ids == (first.id,)
        assert active == (first.id,)

    restored = application.tags.restore_tag(
        second.id, application.tags.get_tag(second.id, include_deleted=True).version
    )
    assert restored.deleted_at_utc is None
    expected_active = tuple(sorted((second.id, third.id))) if replace_result[0] == "replaced" else (first.id,)
    assert application.tasks.get_task(task.id).tag_ids == expected_active


def test_non_root_folder_deletion_relocates_only_direct_tasks(application, clock) -> None:
    root = application.folders.create_folder(CreateFolder(name="Root"))
    parent = application.folders.create_folder(CreateFolder(name="Parent", parent_id=root.id))
    child = application.folders.create_folder(CreateFolder(name="Child", parent_id=parent.id))
    tag = application.tags.create_tag(CreateTag(name="Keep"))
    direct = application.tasks.create_task(
        CreateTask(
            title="Direct",
            folder_id=parent.id,
            tag_ids=(tag.id,),
            priority="HIGH",
            start_at_utc=clock.now - timedelta(days=1),
            due_at_utc=clock.now + timedelta(days=1),
        )
    )
    deleted_direct = application.tasks.create_task(
        CreateTask(title="Deleted direct", folder_id=parent.id, tag_ids=(tag.id,), priority="LOW")
    )
    deleted_direct = application.tasks.delete_task(deleted_direct.id, deleted_direct.version)
    nested = application.tasks.create_task(
        CreateTask(title="Nested", folder_id=child.id, tag_ids=(tag.id,), priority="URGENT")
    )
    before = {item.id: item for item in (direct, deleted_direct, nested)}
    clock.now += timedelta(seconds=1)
    observed = _task_events(application)
    application.unit_of_work_factory()._event_bus.subscribe("FOLDER_DELETED", observed.append)

    application.folders.delete_folder(parent.id, parent.version)

    current_direct = application.tasks.get_task(direct.id)
    current_deleted = application.tasks.get_task(deleted_direct.id, include_deleted=True)
    current_nested = application.tasks.get_task(nested.id)
    promoted = application.folders.get_folder(child.id)
    assert promoted.parent_id == root.id
    for current in (current_direct, current_deleted):
        previous = before[current.id]
        assert current.folder_id == root.id
        assert current.version == previous.version + 1
        assert current.updated_at_utc == clock.now
        assert (current.status, current.priority, current.start_at_utc, current.due_at_utc) == (
            previous.status,
            previous.priority,
            previous.start_at_utc,
            previous.due_at_utc,
        )
        assert (current.completed_at_utc, current.cancelled_at_utc, current.deleted_at_utc, current.tag_ids) == (
            previous.completed_at_utc,
            previous.cancelled_at_utc,
            previous.deleted_at_utc,
            previous.tag_ids,
        )
    assert current_nested == before[nested.id]
    assert [event.event_type for event in observed] == ["FOLDER_DELETED"]


def test_folder_deletion_rolls_back_relocation_after_work_begins(application, app_config, monkeypatch) -> None:
    parent = application.folders.create_folder(CreateFolder(name="Parent"))
    child = application.folders.create_folder(CreateFolder(name="Child", parent_id=parent.id))
    note = application.notes.create_note(CreateNote(title="Note", folder_id=parent.id))
    task = application.tasks.create_task(CreateTask(title="Task", folder_id=parent.id))
    before_task = application.tasks.get_task(task.id)
    before_child = application.folders.get_folder(child.id)
    observed = _task_events(application)
    application.unit_of_work_factory()._event_bus.subscribe("FOLDER_DELETED", observed.append)

    def fail_after_relocation(*_args, **_kwargs) -> None:
        raise RuntimeError("injected failure after relocation")

    monkeypatch.setattr(FolderRepository, "reorder", fail_after_relocation)
    with pytest.raises(RuntimeError, match="after relocation"):
        application.folders.delete_folder(parent.id, parent.version)

    fresh = create_sqlite_engine(app_config.database.path)
    try:
        with fresh.connect() as connection:
            assert connection.execute(
                text("SELECT deleted_at_utc FROM folders WHERE id=:id"), {"id": parent.id}
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT folder_id, version, updated_at_utc FROM tasks WHERE id=:id"),
                {"id": task.id},
            ).one() == (
                parent.id,
                before_task.version,
                datetime_to_epoch_microseconds(before_task.updated_at_utc),
            )
            assert connection.execute(
                text("SELECT parent_id FROM folders WHERE id=:id"), {"id": child.id}
            ).scalar_one() == before_child.parent_id
            assert connection.execute(
                text("SELECT folder_id FROM notes WHERE id=:id"), {"id": note.id}
            ).scalar_one() == parent.id
    finally:
        fresh.dispose()
    assert observed == []


def test_post_claim_tag_replacement_failure_rolls_back_all_writes(
    application, app_config, monkeypatch
) -> None:
    first = application.tags.create_tag(CreateTag(name="First"))
    second = application.tags.create_tag(CreateTag(name="Second"))
    third = application.tags.create_tag(CreateTag(name="Third"))
    task = application.tasks.create_task(CreateTask(title="Task", tag_ids=(first.id,)))
    task = application.tasks.complete_task(task.id, task.version)
    observed = _task_events(application)
    original_replace = TaskRepository.replace_tags

    def replace_then_fail(self, task_id: str, tag_ids: tuple[str, ...]) -> None:
        original_replace(self, task_id, tag_ids)
        raise IntegrityError("injected after task-tag writes", {}, RuntimeError("unexpected"))

    monkeypatch.setattr(TaskRepository, "replace_tags", replace_then_fail)
    with pytest.raises(IntegrityError, match="injected after task-tag writes"):
        application.tasks.set_task_tags(task.id, (second.id, third.id), task.version)

    fresh = create_sqlite_engine(app_config.database.path)
    try:
        with fresh.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT version, status, updated_at_utc, completed_at_utc, cancelled_at_utc "
                    "FROM tasks WHERE id=:id"
                ),
                {"id": task.id},
            ).one() == (
                task.version,
                "COMPLETED",
                datetime_to_epoch_microseconds(task.updated_at_utc),
                datetime_to_epoch_microseconds(task.completed_at_utc),
                None,
            )
            assert connection.execute(
                text("SELECT tag_id FROM task_tags WHERE task_id=:id ORDER BY tag_id"),
                {"id": task.id},
            ).scalars().all() == [first.id]
    finally:
        fresh.dispose()
    assert observed == []


def test_today_interval_matrix_uses_non_utc_calendar_boundaries(application, clock) -> None:
    clock.now = datetime(2026, 5, 15, 12, tzinfo=UTC)
    start = datetime(2026, 5, 15, 4, tzinfo=UTC)
    end = datetime(2026, 5, 16, 4, tzinfo=UTC)
    included = [
        application.tasks.create_task(CreateTask(title="start midnight", start_at_utc=start)),
        application.tasks.create_task(CreateTask(title="due midnight", due_at_utc=start)),
        application.tasks.create_task(CreateTask(title="due before end", due_at_utc=end - timedelta(microseconds=1))),
        application.tasks.create_task(
            CreateTask(title="before to during", start_at_utc=start - timedelta(hours=1), due_at_utc=start + timedelta(hours=1))
        ),
        application.tasks.create_task(
            CreateTask(title="during to after", start_at_utc=start + timedelta(hours=1), due_at_utc=end + timedelta(hours=1))
        ),
        application.tasks.create_task(
            CreateTask(title="spans day", start_at_utc=start - timedelta(hours=1), due_at_utc=end + timedelta(hours=1))
        ),
        application.tasks.create_task(CreateTask(title="start only", start_at_utc=start + timedelta(hours=2))),
        application.tasks.create_task(CreateTask(title="due only", due_at_utc=start + timedelta(hours=2))),
    ]
    next_midnight = application.tasks.create_task(CreateTask(title="next midnight", start_at_utc=end))
    undated = application.tasks.create_task(CreateTask(title="undated"))
    completed = application.tasks.create_task(CreateTask(title="completed", due_at_utc=start + timedelta(hours=3)))
    completed = application.tasks.complete_task(completed.id, completed.version)
    cancelled = application.tasks.create_task(CreateTask(title="cancelled", due_at_utc=start + timedelta(hours=3)))
    cancelled = application.tasks.cancel_task(cancelled.id, cancelled.version)
    deleted = application.tasks.create_task(CreateTask(title="deleted", due_at_utc=start + timedelta(hours=3)))
    application.tasks.delete_task(deleted.id, deleted.version)

    rows = application.tasks.list_today(TaskCalendarQuery(timezone="America/New_York"))
    assert _ids(rows) == _ids(included)
    assert not _ids(rows) & {next_midnight.id, undated.id, completed.id, cancelled.id, deleted.id}


@pytest.mark.parametrize(
    ("now", "start", "end"),
    [
        (datetime(2026, 3, 8, 5, tzinfo=UTC), datetime(2026, 3, 8, 5, tzinfo=UTC), datetime(2026, 3, 15, 4, tzinfo=UTC)),
        (datetime(2026, 11, 1, 4, tzinfo=UTC), datetime(2026, 11, 1, 4, tzinfo=UTC), datetime(2026, 11, 8, 5, tzinfo=UTC)),
    ],
)
def test_next_seven_days_uses_local_calendar_boundaries(application, clock, now, start, end) -> None:
    clock.now = now
    included = [
        application.tasks.create_task(CreateTask(title="first", due_at_utc=start)),
        application.tasks.create_task(CreateTask(title="inside final", due_at_utc=end - timedelta(minutes=30))),
        application.tasks.create_task(CreateTask(title="start only", start_at_utc=start + timedelta(days=2))),
        application.tasks.create_task(CreateTask(title="due only", due_at_utc=start + timedelta(days=2))),
        application.tasks.create_task(
            CreateTask(title="spanning", start_at_utc=start - timedelta(days=1), due_at_utc=end + timedelta(days=1))
        ),
    ]
    before = application.tasks.create_task(CreateTask(title="before", due_at_utc=start - timedelta(microseconds=1)))
    at_end = application.tasks.create_task(CreateTask(title="end", due_at_utc=end))
    undated = application.tasks.create_task(CreateTask(title="undated"))
    completed = application.tasks.create_task(CreateTask(title="completed", due_at_utc=start + timedelta(days=1)))
    completed = application.tasks.complete_task(completed.id, completed.version)
    cancelled = application.tasks.create_task(CreateTask(title="cancelled", due_at_utc=start + timedelta(days=1)))
    cancelled = application.tasks.cancel_task(cancelled.id, cancelled.version)
    deleted = application.tasks.create_task(CreateTask(title="deleted", due_at_utc=start + timedelta(days=1)))
    application.tasks.delete_task(deleted.id, deleted.version)

    rows = application.tasks.list_next_seven_days(TaskCalendarQuery(timezone="America/New_York"))
    assert _ids(rows) == _ids(included)
    assert not _ids(rows) & {before.id, at_end.id, undated.id, completed.id, cancelled.id, deleted.id}


def test_upcoming_and_overdue_complete_boundary_matrices(application, clock) -> None:
    now = clock.now
    future_start = application.tasks.create_task(CreateTask(title="future start", start_at_utc=now + timedelta(minutes=1)))
    future_due = application.tasks.create_task(CreateTask(title="future due", due_at_utc=now + timedelta(minutes=2)))
    future_both = application.tasks.create_task(
        CreateTask(title="future both", start_at_utc=now + timedelta(minutes=3), due_at_utc=now + timedelta(minutes=4))
    )
    past_start = application.tasks.create_task(
        CreateTask(title="past start", start_at_utc=now - timedelta(minutes=1), due_at_utc=now + timedelta(minutes=5))
    )
    start_now = application.tasks.create_task(
        CreateTask(title="start now", start_at_utc=now, due_at_utc=now + timedelta(minutes=6))
    )
    tie_one = application.tasks.create_task(CreateTask(title="tie one", due_at_utc=now + timedelta(minutes=7)))
    tie_two = application.tasks.create_task(CreateTask(title="tie two", due_at_utc=now + timedelta(minutes=7)))
    due_now = application.tasks.create_task(CreateTask(title="due now", due_at_utc=now))
    overdue = application.tasks.create_task(CreateTask(title="overdue", due_at_utc=now - timedelta(microseconds=1)))
    future_microsecond = application.tasks.create_task(CreateTask(title="future microsecond", due_at_utc=now + timedelta(microseconds=1)))
    in_progress = application.tasks.create_task(CreateTask(title="in progress", due_at_utc=now - timedelta(minutes=2)))
    in_progress = application.tasks.start_task(in_progress.id, in_progress.version)
    completed = application.tasks.create_task(CreateTask(title="completed", due_at_utc=now - timedelta(minutes=3)))
    completed = application.tasks.complete_task(completed.id, completed.version)
    cancelled = application.tasks.create_task(CreateTask(title="cancelled", due_at_utc=now - timedelta(minutes=3)))
    cancelled = application.tasks.cancel_task(cancelled.id, cancelled.version)
    deleted = application.tasks.create_task(CreateTask(title="deleted", due_at_utc=now - timedelta(minutes=3)))
    application.tasks.delete_task(deleted.id, deleted.version)
    undated = application.tasks.create_task(CreateTask(title="undated"))

    upcoming = application.tasks.list_upcoming(TaskCalendarQuery(timezone="Asia/Kolkata"))
    assert [row.id for row in upcoming] == [
        future_microsecond.id,
        future_start.id,
        future_due.id,
        future_both.id,
        past_start.id,
        start_now.id,
        *sorted((tie_one.id, tie_two.id)),
    ]
    overdue_rows = application.tasks.list_overdue(TaskPageQuery())
    assert _ids(overdue_rows) == {overdue.id, in_progress.id}
    assert not _ids(overdue_rows) & {due_now.id, future_microsecond.id, completed.id, cancelled.id, deleted.id, undated.id}


def test_completed_and_important_views_have_exact_membership_and_order(application, clock) -> None:
    _set_task_ids(application, "completed-moved", "completed-a", "completed-b", "reopened", "cancelled", "deleted-completed", "completed-later", "urgent-a", "urgent-b", "urgent-null", "high", "high-null", "normal", "low", "deleted")
    folder = application.folders.create_folder(CreateFolder(name="Temporary"))
    moved = application.tasks.create_task(CreateTask(title="moved", folder_id=folder.id))
    tie_a = application.tasks.create_task(CreateTask(title="tie a"))
    tie_b = application.tasks.create_task(CreateTask(title="tie b"))
    reopened = application.tasks.create_task(CreateTask(title="reopened"))
    cancelled = application.tasks.create_task(CreateTask(title="cancelled"))
    deleted_completed = application.tasks.create_task(CreateTask(title="deleted completed"))
    moved = application.tasks.complete_task(moved.id, moved.version)
    tie_a = application.tasks.complete_task(tie_a.id, tie_a.version)
    tie_b = application.tasks.complete_task(tie_b.id, tie_b.version)
    reopened = application.tasks.complete_task(reopened.id, reopened.version)
    deleted_completed = application.tasks.complete_task(deleted_completed.id, deleted_completed.version)
    cancelled = application.tasks.cancel_task(cancelled.id, cancelled.version)
    clock.now += timedelta(seconds=1)
    application.folders.delete_folder(folder.id, folder.version)
    reopened = application.tasks.reopen_task(reopened.id, reopened.version)
    application.tasks.delete_task(deleted_completed.id, deleted_completed.version)
    clock.now += timedelta(seconds=1)
    later = application.tasks.create_task(CreateTask(title="later completed"))
    later = application.tasks.complete_task(later.id, later.version)
    urgent_a = application.tasks.create_task(CreateTask(title="urgent a", priority="URGENT", due_at_utc=clock.now + timedelta(minutes=1)))
    urgent_b = application.tasks.create_task(CreateTask(title="urgent b", priority="URGENT", due_at_utc=clock.now + timedelta(minutes=1)))
    urgent_null = application.tasks.create_task(CreateTask(title="urgent null", priority="URGENT"))
    high = application.tasks.create_task(CreateTask(title="high", priority="HIGH", due_at_utc=clock.now))
    high_null = application.tasks.create_task(CreateTask(title="high null", priority="HIGH"))
    normal = application.tasks.create_task(CreateTask(title="normal", priority="NORMAL"))
    low = application.tasks.create_task(CreateTask(title="low", priority="LOW"))
    deleted = application.tasks.create_task(CreateTask(title="deleted", priority="URGENT"))
    application.tasks.delete_task(deleted.id, deleted.version)

    completed_ids = [row.id for row in application.tasks.list_completed(TaskPageQuery())]
    assert completed_ids == [later.id, moved.id, tie_a.id, tie_b.id]
    important_ids = [row.id for row in application.tasks.list_important(TaskPageQuery())]
    assert important_ids == [
        *sorted((urgent_a.id, urgent_b.id)),
        urgent_null.id,
        high.id,
        high_null.id,
    ]
    assert not set(completed_ids) & {cancelled.id, reopened.id, deleted_completed.id}
    assert not set(important_ids) & {normal.id, low.id, deleted.id}


def test_search_filter_sort_and_pagination_contracts(application, clock) -> None:
    _set_task_ids(application, "a-exact", "b-prefix", "c-substring", "d-description", "e-tie", "f-tie", "g-percent", "h-under", "i-slash", "j-combined", "k-sql", "l-terminal", "m-deleted", "n-folder", "o-root", "p-completed")
    tag = application.tags.create_tag(CreateTag(name="Filter"))
    folder = application.folders.create_folder(CreateFolder(name="Folder"))
    now = clock.now
    exact = application.tasks.create_task(CreateTask(title="needle", due_at_utc=now + timedelta(minutes=4)))
    prefix = application.tasks.create_task(CreateTask(title="needle prefix", due_at_utc=now + timedelta(minutes=3)))
    substring = application.tasks.create_task(CreateTask(title="a needle inside", due_at_utc=now + timedelta(minutes=2)))
    description = application.tasks.create_task(CreateTask(title="description", description="needle in body", due_at_utc=now + timedelta(minutes=1)))
    tie_one = application.tasks.create_task(CreateTask(title="needle tie", due_at_utc=now + timedelta(minutes=5)))
    tie_two = application.tasks.create_task(CreateTask(title="needle ties", due_at_utc=now + timedelta(minutes=5)))
    percent = application.tasks.create_task(CreateTask(title="has % sign"))
    under = application.tasks.create_task(CreateTask(title="has _ sign"))
    slash = application.tasks.create_task(CreateTask(title=r"has \ sign"))
    combined = application.tasks.create_task(CreateTask(title=r"has %_\ signs"))
    sql = application.tasks.create_task(CreateTask(title="' OR 1=1 --"))
    terminal = application.tasks.create_task(CreateTask(title="needle terminal"))
    terminal = application.tasks.complete_task(terminal.id, terminal.version)
    deleted = application.tasks.create_task(CreateTask(title="needle deleted"))
    application.tasks.delete_task(deleted.id, deleted.version)
    folder_task = application.tasks.create_task(
        CreateTask(title="folder", folder_id=folder.id, tag_ids=(tag.id,), priority="HIGH", start_at_utc=now, due_at_utc=now + timedelta(days=1))
    )
    root_task = application.tasks.create_task(CreateTask(title="root", priority="LOW", start_at_utc=now + timedelta(days=2)))
    completed = application.tasks.create_task(CreateTask(title="complete", priority="NORMAL"))
    completed = application.tasks.complete_task(completed.id, completed.version)

    result = application.tasks.search_tasks(TaskSearchQuery(query="needle", include_terminal=True))
    assert [row.id for row in result[:7]] == [
        exact.id,
        prefix.id,
        tie_one.id,
        tie_two.id,
        terminal.id,
        *sorted((substring.id, description.id)),
    ]
    default_result = application.tasks.search_tasks(TaskSearchQuery(query="needle"))
    assert [row.id for row in default_result] == [
        exact.id,
        prefix.id,
        tie_one.id,
        tie_two.id,
        *sorted((substring.id, description.id)),
    ]
    assert deleted.id not in {row.id for row in default_result}
    assert set(row.id for row in result) >= {tie_one.id, tie_two.id, terminal.id}
    assert [row.id for row in application.tasks.search_tasks(TaskSearchQuery(query="needle", limit=2))] == [exact.id, prefix.id]
    assert application.tasks.search_tasks(TaskSearchQuery(query="needle terminal")) == ()
    assert application.tasks.search_tasks(TaskSearchQuery(query="needle terminal", include_terminal=True))[0].id == terminal.id
    assert application.tasks.search_tasks(TaskSearchQuery(query="%"))[0].id == percent.id
    assert application.tasks.search_tasks(TaskSearchQuery(query="_"))[0].id == under.id
    assert application.tasks.search_tasks(TaskSearchQuery(query="\\"))[0].id == slash.id
    assert application.tasks.search_tasks(TaskSearchQuery(query="%_\\"))[0].id == combined.id
    assert application.tasks.search_tasks(TaskSearchQuery(query="' OR 1=1 --"))[0].id == sql.id
    with pytest.raises(ValueError):
        TaskSearchQuery(query=" " * 201)
    assert TaskSearchQuery(query="x" * 200 + " ").query == "x" * 200
    with pytest.raises(ValueError):
        TaskSearchQuery(query="x" * 201)

    assert _ids(application.tasks.list_tasks(TaskQuery(statuses=("TODO", "IN_PROGRESS")))) == {
        exact.id,
        prefix.id,
        substring.id,
        description.id,
        tie_one.id,
        tie_two.id,
        percent.id,
        under.id,
        slash.id,
        combined.id,
        sql.id,
        folder_task.id,
        root_task.id,
    }
    assert _ids(application.tasks.list_tasks(TaskQuery(priorities=("HIGH", "LOW")))) == {folder_task.id, root_task.id}
    assert _ids(application.tasks.list_tasks(TaskQuery(folder_id=folder.id))) == {folder_task.id}
    assert root_task.id in _ids(application.tasks.list_tasks(TaskQuery(folder_id=None)))
    assert _ids(application.tasks.list_tasks(TaskQuery(tag_id=tag.id))) == {folder_task.id}
    assert _ids(application.tasks.list_tasks(TaskQuery(start_from_utc=now + timedelta(days=1)))) == {root_task.id}
    assert _ids(application.tasks.list_tasks(TaskQuery(start_before_utc=now + timedelta(days=1)))) == {folder_task.id}
    assert _ids(application.tasks.list_tasks(TaskQuery(due_from_utc=now + timedelta(days=1)))) == {folder_task.id}
    assert _ids(application.tasks.list_tasks(TaskQuery(due_before_utc=now + timedelta(days=1)))) == {
        exact.id,
        prefix.id,
        substring.id,
        description.id,
        tie_one.id,
        tie_two.id,
    }
    assert _ids(
        application.tasks.list_tasks(
            TaskQuery(completed_from_utc=now, completed_before_utc=now + timedelta(microseconds=1))
        )
    ) == {terminal.id, completed.id}
    assert deleted.id not in _ids(application.tasks.list_tasks(TaskQuery()))
    assert deleted.id in _ids(application.tasks.list_tasks(TaskQuery(include_deleted=True)))
    application.tags.delete_tag(tag.id, tag.version)
    assert application.tasks.list_tasks(TaskQuery(tag_id=tag.id)) == ()
    for kwargs in (
        {"start_from_utc": now, "start_before_utc": now},
        {"due_from_utc": now, "due_before_utc": now},
        {"completed_from_utc": now, "completed_before_utc": now},
        {"statuses": ("TODO",), "completed_from_utc": now},
    ):
        with pytest.raises(ValueError):
            TaskQuery(**kwargs)
    for kwargs in (
        {"start_from_utc": now + timedelta(seconds=2), "start_before_utc": now + timedelta(seconds=1)},
        {"due_from_utc": now + timedelta(seconds=2), "due_before_utc": now + timedelta(seconds=1)},
        {"completed_from_utc": now + timedelta(seconds=2), "completed_before_utc": now + timedelta(seconds=1)},
    ):
        with pytest.raises(ValidationError):
            TaskQuery(**kwargs)

    sortable = [exact, prefix, substring, description, tie_one, tie_two]
    expected_by_sort = {
        "due_soonest": [description.id, substring.id, prefix.id, exact.id, tie_one.id, tie_two.id],
        "due_latest": [tie_one.id, tie_two.id, exact.id, prefix.id, substring.id, description.id],
        "priority": [description.id, substring.id, prefix.id, exact.id, tie_one.id, tie_two.id],
        "created_newest": [item.id for item in sortable],
        "created_oldest": [item.id for item in sortable],
        "updated_newest": [item.id for item in sortable],
        "updated_oldest": [item.id for item in sortable],
        "title": [substring.id, description.id, exact.id, prefix.id, tie_one.id, tie_two.id],
        "status": [description.id, substring.id, prefix.id, exact.id, tie_one.id, tie_two.id],
    }
    for sort, expected in expected_by_sort.items():
        actual = [row.id for row in application.tasks.list_tasks(TaskQuery(sort=sort, limit=200)) if row.id in set(expected)]
        assert actual == expected
        first_page = application.tasks.list_tasks(TaskQuery(sort=sort, limit=2, offset=0))
        second_page = application.tasks.list_tasks(TaskQuery(sort=sort, limit=2, offset=2))
        full = application.tasks.list_tasks(TaskQuery(sort=sort, limit=200))
        assert [row.id for row in first_page + second_page] == [row.id for row in full[:4]]
        assert not _ids(first_page) & _ids(second_page)
        assert first_page == application.tasks.list_tasks(TaskQuery(sort=sort, limit=2, offset=0))


def test_all_general_sort_modes_use_exact_rank_null_and_id_order(application, clock) -> None:
    base = clock.now
    _set_task_ids(application, "a", "b", "c", "d", "e")
    first = application.tasks.create_task(
        CreateTask(title="alpha", priority="NORMAL", due_at_utc=base + timedelta(minutes=4))
    )
    clock.now += timedelta(seconds=1)
    second = application.tasks.create_task(
        CreateTask(title="bravo", priority="HIGH", due_at_utc=base + timedelta(minutes=2))
    )
    clock.now += timedelta(seconds=1)
    third = application.tasks.create_task(CreateTask(title="charlie", priority="URGENT"))
    clock.now += timedelta(seconds=1)
    fourth = application.tasks.create_task(
        CreateTask(title="delta", priority="LOW", due_at_utc=base + timedelta(minutes=2))
    )
    clock.now += timedelta(seconds=1)
    fifth = application.tasks.create_task(
        CreateTask(title="echo", priority="NORMAL", due_at_utc=base + timedelta(minutes=2))
    )
    clock.now += timedelta(seconds=1)
    second = application.tasks.start_task(second.id, second.version)
    clock.now += timedelta(seconds=1)
    fourth = application.tasks.complete_task(fourth.id, fourth.version)
    clock.now += timedelta(seconds=1)
    fifth = application.tasks.cancel_task(fifth.id, fifth.version)

    expected = {
        "due_soonest": [second.id, fourth.id, fifth.id, first.id, third.id],
        "due_latest": [first.id, second.id, fourth.id, fifth.id, third.id],
        "priority": [third.id, second.id, fifth.id, first.id, fourth.id],
        "created_newest": [fifth.id, fourth.id, third.id, second.id, first.id],
        "created_oldest": [first.id, second.id, third.id, fourth.id, fifth.id],
        "updated_newest": [fifth.id, fourth.id, second.id, third.id, first.id],
        "updated_oldest": [first.id, third.id, second.id, fourth.id, fifth.id],
        "title": [first.id, second.id, third.id, fourth.id, fifth.id],
        "status": [first.id, third.id, second.id, fourth.id, fifth.id],
    }
    for sort, ordered_ids in expected.items():
        assert [row.id for row in application.tasks.list_tasks(TaskQuery(sort=sort))] == ordered_ids


def test_all_task_event_ids_and_repository_boolean_claim_contract(application, clock) -> None:
    events = _task_events(application)
    task = application.tasks.create_task(CreateTask(title="event"))
    task = application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="updated"))
    task = application.tasks.start_task(task.id, task.version)
    task = application.tasks.return_task_to_todo(task.id, task.version)
    task = application.tasks.complete_task(task.id, task.version)
    task = application.tasks.reopen_task(task.id, task.version)
    task = application.tasks.cancel_task(task.id, task.version)
    task = application.tasks.reopen_task(task.id, task.version)
    folder = application.folders.create_folder(CreateFolder(name="Move"))
    task = application.tasks.move_task(task.id, folder.id, task.version)
    tag = application.tags.create_tag(CreateTag(name="Tag"))
    task = application.tasks.set_task_tags(task.id, (tag.id,), task.version)
    task = application.tasks.delete_task(task.id, task.version)
    task = application.tasks.restore_task(task.id, task.version)
    assert len(events) == 12
    assert [event.event_type for event in events] == [
        "TASK_CREATED",
        "TASK_UPDATED",
        "TASK_STARTED",
        "TASK_RETURNED_TO_TODO",
        "TASK_COMPLETED",
        "TASK_REOPENED",
        "TASK_CANCELLED",
        "TASK_REOPENED",
        "TASK_MOVED",
        "TASK_TAGS_CHANGED",
        "TASK_DELETED",
        "TASK_RESTORED",
    ]
    assert {event.event_type for event in events} == set(_TASK_EVENT_TYPES)
    for event in events:
        assert event.entity_id == task.id
        assert event.occurred_at_utc == clock.now
        assert event.payload == {}
        version = int(event.event_id.rsplit(":", 1)[1])
        assert event.event_id == f"task:{event.event_type.lower()}:{task.id}:{version}"
    assert [int(event.event_id.rsplit(":", 1)[1]) for event in events] == list(range(1, 13))

    with application.unit_of_work_factory() as uow:
        assert uow.tasks is not None
        assert uow.tasks.claim_version(task.id, task.version, clock.now, deleted=False) is True
        assert uow.tasks.claim_version(task.id, task.version, clock.now, deleted=False) is False
        uow.commit()
    assert application.tasks.get_task(task.id).version == task.version + 1


def test_repository_filters_and_pagination_return_persistence_rows_only(application) -> None:
    folder = application.folders.create_folder(CreateFolder(name="Repository"))
    first = application.tasks.create_task(CreateTask(title="first", folder_id=folder.id))
    second = application.tasks.create_task(CreateTask(title="second", folder_id=folder.id))
    root = application.tasks.create_task(CreateTask(title="root"))

    with application.unit_of_work_factory() as uow:
        assert uow.tasks is not None
        folder_rows = uow.tasks.list_tasks(TaskQuery(folder_id=folder.id, sort="title"))
        root_rows = uow.tasks.list_tasks(TaskQuery(folder_id=None, sort="title"))
        page = uow.tasks.list_tasks(TaskQuery(folder_id=folder.id, sort="title", limit=1, offset=1))
        assert [row.id for row in folder_rows] == [first.id, second.id]
        assert [row.id for row in root_rows] == [root.id]
        assert [row.id for row in page] == [second.id]
