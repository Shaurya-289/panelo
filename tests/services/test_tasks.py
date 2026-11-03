from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from productivity_app.core.exceptions import (
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.tags import CreateTag
from productivity_app.schemas.tasks import (
    CreateTask,
    TaskCalendarQuery,
    TaskPageQuery,
    TaskQuery,
    TaskSearchQuery,
    UpdateTask,
)


def test_task_crud_tags_and_optimistic_concurrency(application) -> None:
    folder = application.folders.create_folder(CreateFolder(name="Inbox"))
    tag = application.tags.create_tag(CreateTag(name="Work"))
    task = application.tasks.create_task(
        CreateTask(title="  Write plan  ", description="body", folder_id=folder.id, tag_ids=(tag.id, tag.id))
    )
    assert task.title == "Write plan"
    assert task.status == "TODO"
    assert task.priority == "NORMAL"
    assert task.tag_ids == (tag.id,)

    changed = application.tasks.update_task(
        task.id, UpdateTask(expected_version=task.version, priority="URGENT", folder_id=None)
    )
    assert changed.folder_id is None
    assert changed.priority == "URGENT"
    assert changed.version == task.version + 1
    with pytest.raises(StaleVersionError):
        application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="lost"))
    assert application.tasks.list_tasks(TaskQuery(priorities=("URGENT",)))[0].id == task.id


def test_task_lifecycle_delete_and_restore(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Lifecycle"))
    started = application.tasks.start_task(task.id, task.version)
    assert started.status == "IN_PROGRESS"
    returned = application.tasks.return_task_to_todo(started.id, started.version)
    completed = application.tasks.complete_task(returned.id, returned.version)
    assert completed.status == "COMPLETED"
    assert completed.completed_at_utc == clock.now
    with pytest.raises(InvalidStateTransitionError):
        application.tasks.start_task(completed.id, completed.version)
    reopened = application.tasks.reopen_task(completed.id, completed.version)
    assert reopened.status == "TODO"
    assert reopened.completed_at_utc is None
    deleted = application.tasks.delete_task(reopened.id, reopened.version)
    with pytest.raises(NotFoundError):
        application.tasks.get_task(task.id)
    assert application.tasks.get_task(task.id, include_deleted=True).deleted_at_utc is not None
    restored = application.tasks.restore_task(deleted.id, deleted.version)
    assert restored.deleted_at_utc is None


def test_task_tag_replacement_and_restore_after_folder_delete(application) -> None:
    first = application.tags.create_tag(CreateTag(name="First"))
    second = application.tags.create_tag(CreateTag(name="Second"))
    folder = application.folders.create_folder(CreateFolder(name="Temporary"))
    task = application.tasks.create_task(CreateTask(title="Tagged", folder_id=folder.id, tag_ids=(first.id,)))
    changed = application.tasks.set_task_tags(task.id, (second.id, second.id), task.version)
    assert changed.tag_ids == (second.id,)
    with pytest.raises(ValidationError):
        application.tasks.set_task_tags(changed.id, (second.id,), changed.version)
    deleted_task = application.tasks.delete_task(changed.id, changed.version)
    application.folders.delete_folder(folder.id, folder.version)
    restored = application.tasks.restore_task(deleted_task.id, deleted_task.version + 1)
    assert restored.folder_id is None
    assert restored.tag_ids == (second.id,)


def test_task_date_validation_and_folder_deletion_relocates_task(application, clock) -> None:
    with pytest.raises(ValidationError):
        application.tasks.update_task("missing", UpdateTask(expected_version=1))
    with pytest.raises(ValueError):
        CreateTask(
            title="bad",
            start_at_utc=datetime(2026, 9, 25, tzinfo=UTC),
            due_at_utc=datetime(2026, 9, 24, tzinfo=UTC),
        )
    folder = application.folders.create_folder(CreateFolder(name="Parent"))
    task = application.tasks.create_task(CreateTask(title="Move with folder", folder_id=folder.id))
    deleted = application.folders.delete_folder(folder.id, folder.version)
    assert deleted.deleted_at_utc == clock.now
    moved = application.tasks.get_task(task.id)
    assert moved.folder_id is None
    assert moved.version == task.version + 1


def test_task_search_escapes_wildcards_and_excludes_terminal_by_default(application) -> None:
    task = application.tasks.create_task(CreateTask(title="100%_done", description="needle"))
    finished = application.tasks.complete_task(task.id, task.version)
    assert application.tasks.search_tasks(TaskSearchQuery(query="100%_done")) == ()
    results = application.tasks.search_tasks(
        TaskSearchQuery(query="100%_done", include_terminal=True)
    )
    assert results[0].id == finished.id


def test_task_calendar_views(application, clock) -> None:
    now = clock.now
    today = application.tasks.create_task(CreateTask(title="Today", due_at_utc=now + timedelta(hours=1)))
    application.tasks.create_task(CreateTask(title="Future", start_at_utc=now + timedelta(days=2)))
    overdue = application.tasks.create_task(CreateTask(title="Late", due_at_utc=now - timedelta(microseconds=1)))
    assert [row.id for row in application.tasks.list_today(TaskCalendarQuery(timezone="Asia/Kolkata"))] == [overdue.id, today.id]
    assert {row.id for row in application.tasks.list_upcoming(TaskCalendarQuery(timezone="America/New_York"))} == {today.id, application.tasks.list_upcoming(TaskCalendarQuery(timezone="America/New_York"))[1].id}
    assert [row.id for row in application.tasks.list_overdue(TaskPageQuery())] == [overdue.id]
