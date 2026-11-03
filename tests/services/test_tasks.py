from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from productivity_app.core.exceptions import (
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.schemas.tasks import (
    CreateTask,
    TaskCalendarQuery,
    TaskPageQuery,
    TaskQuery,
    TaskSearchQuery,
    UpdateTask,
)


def test_task_crud_and_optimistic_concurrency(application) -> None:
    task = application.tasks.create_task(
        CreateTask(title="  Write plan  ", description="body")
    )
    assert task.title == "Write plan"
    assert task.status == "TODO"
    assert task.priority == "NORMAL"

    changed = application.tasks.update_task(
        task.id, UpdateTask(expected_version=task.version, priority="URGENT")
    )
    assert changed.priority == "URGENT"
    assert changed.version == task.version + 1
    with pytest.raises(StaleVersionError):
        application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="lost"))
    assert application.tasks.list_tasks(TaskQuery(priorities=("URGENT",)))[0].id == task.id


def test_task_date_only_flag_defaults_to_a_timed_due_date_and_round_trips(application, clock) -> None:
    midnight = datetime(2026, 9, 25, tzinfo=UTC)
    timed = application.tasks.create_task(CreateTask(title="Timed", due_at_utc=clock.now))
    dated = application.tasks.create_task(
        CreateTask(title="Dated", due_at_utc=midnight, due_has_time=False)
    )
    assert timed.due_has_time is True
    assert dated.due_has_time is False and dated.due_at_utc == midnight
    summary = {row.id: row for row in application.tasks.list_tasks()}
    assert summary[dated.id].due_has_time is False and summary[timed.id].due_has_time is True

    flipped = application.tasks.update_task(
        dated.id,
        UpdateTask(expected_version=dated.version, due_at_utc=clock.now, due_has_time=True),
    )
    assert flipped.due_has_time is True and flipped.due_at_utc == clock.now
    with pytest.raises(ValidationError):
        application.tasks.update_task(
            flipped.id, UpdateTask(expected_version=flipped.version, due_has_time=None)
        )


def test_task_summaries_expose_completion_time(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Done today"))
    done = application.tasks.complete_task(task.id, task.version)
    summary = application.tasks.list_tasks(TaskQuery(statuses=("COMPLETED",)))[0]
    assert summary.completed_at_utc == clock.now == done.completed_at_utc


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


def test_task_date_validation(application) -> None:
    with pytest.raises(ValidationError):
        application.tasks.update_task("missing", UpdateTask(expected_version=1))
    with pytest.raises(ValueError):
        CreateTask(
            title="bad",
            start_at_utc=datetime(2026, 9, 25, tzinfo=UTC),
            due_at_utc=datetime(2026, 9, 24, tzinfo=UTC),
        )


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
