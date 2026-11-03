"""Adversarial Phase 2 regression coverage using real file-backed application flows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from productivity_app.core.events import InProcessEventBus
from productivity_app.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.schemas.tasks import (
    CreateTask,
    TaskCalendarQuery,
    TaskPageQuery,
    TaskQuery,
    TaskSearchQuery,
    UpdateTask,
)
from productivity_app.services.tasks import _translate_task_integrity_error

_TASK_EVENT_TYPES = (
    "TASK_CREATED",
    "TASK_UPDATED",
    "TASK_STARTED",
    "TASK_RETURNED_TO_TODO",
    "TASK_COMPLETED",
    "TASK_CANCELLED",
    "TASK_REOPENED",
    "TASK_DELETED",
    "TASK_RESTORED",
)


def _bus(application) -> InProcessEventBus:
    return application.unit_of_work_factory()._event_bus


def _task_events(application, event_type: str) -> list:
    observed: list = []
    _bus(application).subscribe(event_type, observed.append)
    return observed


def _prepare_status(application, status: str):
    task = application.tasks.create_task(
        CreateTask(
            title="state",
            start_at_utc=datetime(2026, 9, 25, 10, tzinfo=UTC),
            due_at_utc=datetime(2026, 9, 25, 14, tzinfo=UTC),
        )
    )
    if status == "IN_PROGRESS":
        return application.tasks.start_task(task.id, task.version)
    if status == "COMPLETED":
        return application.tasks.complete_task(task.id, task.version)
    if status == "CANCELLED":
        return application.tasks.cancel_task(task.id, task.version)
    return task


@pytest.mark.parametrize(
    ("source", "method", "event_type", "target"),
    [
        ("TODO", "start_task", "TASK_STARTED", "IN_PROGRESS"),
        ("TODO", "complete_task", "TASK_COMPLETED", "COMPLETED"),
        ("TODO", "cancel_task", "TASK_CANCELLED", "CANCELLED"),
        ("IN_PROGRESS", "return_task_to_todo", "TASK_RETURNED_TO_TODO", "TODO"),
        ("IN_PROGRESS", "complete_task", "TASK_COMPLETED", "COMPLETED"),
        ("IN_PROGRESS", "cancel_task", "TASK_CANCELLED", "CANCELLED"),
        ("COMPLETED", "reopen_task", "TASK_REOPENED", "TODO"),
        ("CANCELLED", "reopen_task", "TASK_REOPENED", "TODO"),
    ],
)
def test_lifecycle_matrix_emits_one_post_commit_event(
    application, clock, source, method, event_type, target
) -> None:
    task = _prepare_status(application, source)
    observed = _task_events(application, event_type)
    result = getattr(application.tasks, method)(task.id, task.version)
    assert result.status == target
    assert result.version == task.version + 1
    assert result.updated_at_utc == clock.now
    assert result.start_at_utc == task.start_at_utc and result.due_at_utc == task.due_at_utc
    if target == "COMPLETED":
        assert result.completed_at_utc == clock.now and result.cancelled_at_utc is None
    elif target == "CANCELLED":
        assert result.cancelled_at_utc == clock.now and result.completed_at_utc is None
    else:
        assert result.completed_at_utc is None and result.cancelled_at_utc is None
    assert [(event.event_type, event.entity_id, event.occurred_at_utc, event.payload) for event in observed] == [
        (event_type, task.id, clock.now, {})
    ]
    assert observed[0].event_id.endswith(f":{result.version}")


@pytest.mark.parametrize(
    ("source", "method"),
    [
        ("TODO", "return_task_to_todo"),
        ("TODO", "reopen_task"),
        ("IN_PROGRESS", "start_task"),
        ("IN_PROGRESS", "reopen_task"),
        ("COMPLETED", "start_task"),
        ("COMPLETED", "complete_task"),
        ("COMPLETED", "cancel_task"),
        ("COMPLETED", "return_task_to_todo"),
        ("CANCELLED", "start_task"),
        ("CANCELLED", "complete_task"),
        ("CANCELLED", "cancel_task"),
        ("CANCELLED", "return_task_to_todo"),
    ],
)
def test_forbidden_lifecycle_transitions_do_not_mutate_or_publish(application, source, method) -> None:
    task = _prepare_status(application, source)
    observed: list = []
    bus = _bus(application)
    for event_type in _TASK_EVENT_TYPES:
        bus.subscribe(event_type, observed.append)
    with pytest.raises(InvalidStateTransitionError):
        getattr(application.tasks, method)(task.id, task.version)
    assert application.tasks.get_task(task.id) == task
    assert observed == []


@pytest.mark.parametrize("kind", ["update", "complete_cancel", "complete_update", "cancel_update", "delete_complete", "delete_cancel"])
def test_concurrent_task_mutations_are_serializable(application, kind) -> None:
    task = application.tasks.create_task(CreateTask(title=kind))
    def left(version: int):
        if kind in {"cancel_update", "complete_cancel"}:
            return (
                application.tasks.cancel_task(task.id, version)
                if kind == "cancel_update"
                else application.tasks.complete_task(task.id, version)
            )
        if kind.startswith("delete"):
            return application.tasks.delete_task(task.id, version)
        if kind == "complete_update":
            return application.tasks.complete_task(task.id, version)
        return application.tasks.update_task(task.id, UpdateTask(expected_version=version, title="left"))

    def right(version: int):
        if kind == "complete_cancel":
            return application.tasks.cancel_task(task.id, version)
        if kind == "delete_complete":
            return application.tasks.complete_task(task.id, version)
        if kind == "delete_cancel":
            return application.tasks.cancel_task(task.id, version)
        return application.tasks.update_task(task.id, UpdateTask(expected_version=version, title="right"))
    barrier = Barrier(2)

    def run(operation):
        version = application.tasks.get_task(task.id).version
        barrier.wait()
        try:
            return "ok", operation(version)
        except (StaleVersionError, InvalidStateTransitionError, NotFoundError) as exc:
            return type(exc).__name__, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, (left, right)))
    assert sum(result[0] == "ok" for result in outcomes) == 1
    final = application.tasks.get_task(task.id, include_deleted=True)
    assert final.version == task.version + 1
    if final.status == "COMPLETED":
        assert final.completed_at_utc is not None and final.cancelled_at_utc is None
    if final.status == "CANCELLED":
        assert final.cancelled_at_utc is not None and final.completed_at_utc is None


def test_views_filters_sorts_pagination_and_search_boundaries(application, clock) -> None:
    now = clock.now
    high = application.tasks.create_task(CreateTask(title="Exact 100%_\\", priority="HIGH", due_at_utc=now + timedelta(hours=2)))
    urgent = application.tasks.create_task(CreateTask(title="Prefix task", priority="URGENT", start_at_utc=now + timedelta(days=2)))
    started_seed = application.tasks.create_task(CreateTask(title="started", due_at_utc=now + timedelta(hours=1)))
    started = application.tasks.start_task(started_seed.id, started_seed.version)
    overdue = application.tasks.create_task(CreateTask(title="late", due_at_utc=now - timedelta(microseconds=1)))
    exact_now = application.tasks.create_task(CreateTask(title="now", due_at_utc=now))
    done_seed = application.tasks.create_task(CreateTask(title="done"))
    done = application.tasks.complete_task(done_seed.id, done_seed.version)
    cancelled_seed = application.tasks.create_task(CreateTask(title="cancel"))
    application.tasks.cancel_task(cancelled_seed.id, cancelled_seed.version)
    deleted_seed = application.tasks.create_task(CreateTask(title="deleted"))
    application.tasks.delete_task(deleted_seed.id, deleted_seed.version)
    assert overdue.id in {row.id for row in application.tasks.list_overdue(TaskPageQuery())}
    assert exact_now.id not in {row.id for row in application.tasks.list_overdue(TaskPageQuery())}
    assert exact_now.id not in {row.id for row in application.tasks.list_upcoming(TaskCalendarQuery(timezone="America/New_York"))}
    assert urgent.id in {row.id for row in application.tasks.list_next_seven_days(TaskCalendarQuery(timezone="America/New_York"))}
    assert high.id in {row.id for row in application.tasks.list_important(TaskPageQuery())}
    assert done.id in {row.id for row in application.tasks.list_completed(TaskPageQuery())}
    assert started.id in {row.id for row in application.tasks.list_today(TaskCalendarQuery(timezone="Asia/Kolkata"))}
    assert application.tasks.search_tasks(TaskSearchQuery(query="Exact 100%_\\"))[0].id == high.id
    assert application.tasks.search_tasks(TaskSearchQuery(query="prefix"))[0].id == urgent.id
    ordered = application.tasks.list_tasks(TaskQuery(sort="due_soonest", limit=200))
    first_page = application.tasks.list_tasks(TaskQuery(sort="title", limit=3, offset=0))
    second_page = application.tasks.list_tasks(TaskQuery(sort="title", limit=3, offset=3))
    assert not ({row.id for row in first_page} & {row.id for row in second_page})
    assert len({row.id for row in ordered}) == len(ordered)
    with pytest.raises(ValueError):
        TaskQuery(limit=0)
    with pytest.raises(ValueError):
        TaskQuery(offset=-1)
    with pytest.raises(ValueError):
        TaskQuery(limit=201)


def test_task_events_cover_every_direct_workflow_and_subscriber_failure(application, clock) -> None:
    observed: list = []
    bus = _bus(application)
    event_types = (
        "TASK_CREATED", "TASK_UPDATED", "TASK_STARTED", "TASK_RETURNED_TO_TODO",
        "TASK_COMPLETED", "TASK_CANCELLED", "TASK_REOPENED", "TASK_DELETED", "TASK_RESTORED",
    )
    for event_type in event_types:
        bus.subscribe(event_type, observed.append)
    bus.subscribe("TASK_UPDATED", lambda _: (_ for _ in ()).throw(RuntimeError("subscriber")))
    task = application.tasks.create_task(CreateTask(title="event", description="secret"))
    task = application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="updated"))
    task = application.tasks.start_task(task.id, task.version)
    task = application.tasks.return_task_to_todo(task.id, task.version)
    task = application.tasks.complete_task(task.id, task.version)
    task = application.tasks.reopen_task(task.id, task.version)
    task = application.tasks.cancel_task(task.id, task.version)
    task = application.tasks.reopen_task(task.id, task.version)
    task = application.tasks.delete_task(task.id, task.version)
    task = application.tasks.restore_task(task.id, task.version)
    assert [event.event_type for event in observed] == [
        "TASK_CREATED", "TASK_UPDATED", "TASK_STARTED", "TASK_RETURNED_TO_TODO",
        "TASK_COMPLETED", "TASK_REOPENED", "TASK_CANCELLED", "TASK_REOPENED", "TASK_DELETED",
        "TASK_RESTORED",
    ]
    assert all(event.entity_id == task.id and event.occurred_at_utc == clock.now for event in observed)
    assert all(event.payload == {} and "secret" not in str(event.payload) for event in observed)
    assert application.tasks.get_task(task.id).title == "updated"
    stale_count = len(observed)
    with pytest.raises(StaleVersionError):
        application.tasks.update_task(task.id, UpdateTask(expected_version=task.version - 1, title="lost"))
    with pytest.raises(InvalidStateTransitionError):
        application.tasks.reopen_task(task.id, task.version)
    assert len(observed) == stale_count


def test_calendar_filters_and_all_sorts_are_stable(application, clock) -> None:
    clock.now = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    first = application.tasks.create_task(
        CreateTask(title="alpha", priority="LOW", start_at_utc=clock.now, due_at_utc=clock.now + timedelta(hours=1))
    )
    second = application.tasks.create_task(
        CreateTask(title="beta", priority="URGENT", due_at_utc=clock.now + timedelta(days=8))
    )
    completed_seed = application.tasks.create_task(CreateTask(title="complete", due_at_utc=clock.now))
    completed = application.tasks.complete_task(completed_seed.id, completed_seed.version)
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(statuses=("TODO",), priorities=("LOW",)))] == [first.id]
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(priorities=("URGENT",)))] == [second.id]
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(completed_from_utc=clock.now - timedelta(microseconds=1), completed_before_utc=clock.now + timedelta(microseconds=1)))] == [completed.id]
    assert first.id in {row.id for row in application.tasks.list_today(TaskCalendarQuery(timezone="America/New_York"))}
    assert second.id not in {row.id for row in application.tasks.list_next_seven_days(TaskCalendarQuery(timezone="America/New_York"))}
    # 2026-11-01 is a 25-hour New York day; 04:59:59Z next day is still local today.
    fall_inside = application.tasks.create_task(CreateTask(title="fall inside", due_at_utc=datetime(2026, 11, 2, 4, 59, 59, tzinfo=UTC)))
    fall_outside = application.tasks.create_task(CreateTask(title="fall outside", due_at_utc=datetime(2026, 11, 2, 5, tzinfo=UTC)))
    today_ids = {row.id for row in application.tasks.list_today(TaskCalendarQuery(timezone="America/New_York"))}
    assert fall_inside.id in today_ids and fall_outside.id not in today_ids
    for sort in ("due_soonest", "due_latest", "priority", "status", "created_newest", "created_oldest", "updated_newest", "updated_oldest", "title"):
        first_run = [row.id for row in application.tasks.list_tasks(TaskQuery(sort=sort, include_deleted=True))]
        second_run = [row.id for row in application.tasks.list_tasks(TaskQuery(sort=sort, include_deleted=True))]
        assert first_run == second_run and len(first_run) == len(set(first_run))


def test_normalization_and_task_integrity_translation_boundaries() -> None:
    assert CreateTask(title="x" + " " * 300).title == "x"
    assert UpdateTask(expected_version=1, title="x" + " " * 300).title == "x"
    assert TaskSearchQuery(query="x" * 200 + " ").query == "x" * 200
    with pytest.raises(ValueError):
        CreateTask(title=" " * 301)
    with pytest.raises(ValueError):
        TaskSearchQuery(query=" " * 201)
    known_check = IntegrityError("", {}, RuntimeError("CHECK constraint failed: ck_tasks_status_values"))
    known_fk = IntegrityError("", {}, RuntimeError("FOREIGN KEY constraint failed"))
    known_unique = IntegrityError("", {}, RuntimeError("UNIQUE constraint failed: tasks.id"))
    unknown = IntegrityError("", {}, RuntimeError("CHECK constraint failed: future_constraint"))
    with pytest.raises(ValidationError):
        _translate_task_integrity_error(known_check)
    with pytest.raises(NotFoundError):
        _translate_task_integrity_error(known_fk)
    with pytest.raises(ConflictError):
        _translate_task_integrity_error(known_unique)
    with pytest.raises(IntegrityError):
        _translate_task_integrity_error(unknown)


def test_task_creation_failure_is_atomic_from_fresh_connection(application, tmp_path) -> None:
    class _FixedId:
        def new(self) -> str:
            return "duplicate-id"

    observed = _task_events(application, "TASK_CREATED")
    application.tasks._ids = _FixedId()
    application.tasks.create_task(CreateTask(title="first"))
    with pytest.raises(ConflictError):
        application.tasks.create_task(CreateTask(title="must not persist"))
    application.engine.dispose()
    fresh = create_sqlite_engine(application.engine.url.database and __import__("pathlib").Path(application.engine.url.database))
    try:
        with fresh.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM tasks WHERE title='must not persist'")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM tasks")).scalar_one() == 1
    finally:
        fresh.dispose()
    assert len(observed) == 1
