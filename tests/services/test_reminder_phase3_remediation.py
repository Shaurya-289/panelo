from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork
from productivity_app.repositories.reminders import ReminderRepository
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.reminders import (
    CreateReminder,
    ReminderPageQuery,
    ReminderQuery,
    SnoozeReminder,
    UpdateReminder,
)
from productivity_app.schemas.tasks import CreateTask
from productivity_app.services.reminders import _translate_reminder_integrity_error


def _bus(application):
    return application.unit_of_work_factory()._event_bus


def _events(application, event_types: tuple[str, ...]) -> list[DomainEvent]:
    observed: list[DomainEvent] = []
    for event_type in event_types:
        _bus(application).subscribe(event_type, observed.append)
    return observed


def _history_count(application, reminder_id: str) -> int:
    return len(application.reminders.list_reminder_events(reminder_id, ReminderPageQuery(limit=200)))


def _fresh_row(app_config, reminder_id: str):
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT task_id, note_id, status, due_at_utc, scheduled_timezone, snooze_count, "
                    "version, updated_at_utc, dismissed_at_utc, completed_at_utc, cancelled_at_utc "
                    "FROM reminders WHERE id=:id"
                ),
                {"id": reminder_id},
            ).one()
    finally:
        engine.dispose()


@pytest.mark.parametrize("parent_kind", ("task", "note"))
def test_historical_parent_link_can_be_resubmitted_without_revalidation(
    application, clock, parent_kind: str
) -> None:
    if parent_kind == "task":
        parent = application.tasks.create_task(CreateTask(title="Task parent"))
        reminder = application.reminders.create_reminder(
            CreateReminder(
                title="Reminder",
                due_at_utc=clock.now + timedelta(hours=1),
                scheduled_timezone="UTC",
                task_id=parent.id,
            )
        )
        application.tasks.delete_task(parent.id, parent.version)
        patch = UpdateReminder(expected_version=reminder.version, title="Changed", task_id=parent.id)
    else:
        parent = application.notes.create_note(CreateNote(title="Note parent"))
        reminder = application.reminders.create_reminder(
            CreateReminder(
                title="Reminder",
                due_at_utc=clock.now + timedelta(hours=1),
                scheduled_timezone="UTC",
                note_id=parent.id,
            )
        )
        application.notes.delete_note(parent.id, parent.version)
        patch = UpdateReminder(expected_version=reminder.version, title="Changed", note_id=parent.id)

    observed = _events(application, ("REMINDER_UPDATED",))
    before = reminder.updated_at_utc
    clock.now += timedelta(microseconds=1)
    updated = application.reminders.update_reminder(reminder.id, patch)

    assert updated.version == reminder.version + 1
    assert updated.updated_at_utc > before
    assert getattr(updated, f"{parent_kind}_id") == parent.id
    assert len(observed) == 1
    assert observed[0].event_id == f"reminder:reminder_updated:{reminder.id}:{updated.version}"
    assert application.reminders.list_reminder_events(reminder.id)[0].event_type == "REMINDER_UPDATED"


@pytest.mark.parametrize("parent_kind", ("task", "note"))
def test_changed_link_to_deleted_parent_is_rejected(application, clock, parent_kind: str) -> None:
    if parent_kind == "task":
        parent = application.tasks.create_task(CreateTask(title="Deleted task"))
        application.tasks.delete_task(parent.id, parent.version)
        reminder = application.reminders.create_reminder(
            CreateReminder(title="Unlinked", due_at_utc=clock.now, scheduled_timezone="UTC")
        )
        patch = UpdateReminder(expected_version=reminder.version, task_id=parent.id)
    else:
        parent = application.notes.create_note(CreateNote(title="Deleted note"))
        application.notes.delete_note(parent.id, parent.version)
        reminder = application.reminders.create_reminder(
            CreateReminder(title="Unlinked", due_at_utc=clock.now, scheduled_timezone="UTC")
        )
        patch = UpdateReminder(expected_version=reminder.version, note_id=parent.id)

    before_events = _history_count(application, reminder.id)
    with pytest.raises(NotFoundError):
        application.reminders.update_reminder(reminder.id, patch)
    stored = application.reminders.get_reminder(reminder.id)
    assert stored.version == reminder.version
    assert stored.task_id is None and stored.note_id is None
    assert _history_count(application, reminder.id) == before_events


@pytest.mark.parametrize("parent_kind", ("task", "note"))
def test_note_and_task_parent_deletion_preserve_independent_historical_link(
    application, clock, parent_kind: str
) -> None:
    if parent_kind == "task":
        parent = application.tasks.create_task(CreateTask(title="Task parent"))
        reminder = application.reminders.create_reminder(
            CreateReminder(title="Reminder", due_at_utc=clock.now, scheduled_timezone="UTC", task_id=parent.id)
        )
        application.tasks.delete_task(parent.id, parent.version)
        application.tasks.restore_task(parent.id, parent.version + 1)
    else:
        parent = application.notes.create_note(CreateNote(title="Note parent"))
        reminder = application.reminders.create_reminder(
            CreateReminder(title="Reminder", due_at_utc=clock.now, scheduled_timezone="UTC", note_id=parent.id)
        )
        application.notes.delete_note(parent.id, parent.version)
        application.notes.restore_note(parent.id, parent.version + 1)

    after = application.reminders.get_reminder(reminder.id)
    assert after.version == reminder.version
    assert getattr(after, f"{parent_kind}_id") == parent.id


@pytest.mark.parametrize("parent_kind", ("task", "note"))
@pytest.mark.parametrize("operation", ("create", "relink"))
def test_parent_relationship_races_are_serializable(
    application, app_config, clock, parent_kind: str, operation: str
) -> None:
    if parent_kind == "task":
        parent = application.tasks.create_task(CreateTask(title=f"{operation} task"))

        def delete_parent() -> None:
            application.tasks.delete_task(parent.id, parent.version)

    else:
        parent = application.notes.create_note(CreateNote(title=f"{operation} note"))

        def delete_parent() -> None:
            application.notes.delete_note(parent.id, parent.version)

    if operation == "create":
        reminder_id: str | None = None

        def reminder_write() -> str:
            try:
                command = CreateReminder(
                    title="New link",
                    due_at_utc=clock.now + timedelta(hours=1),
                    scheduled_timezone="UTC",
                    **{f"{parent_kind}_id": parent.id},
                )
                nonlocal reminder_id
                reminder_id = application.reminders.create_reminder(command).id
                return "created"
            except NotFoundError:
                return "not-found"

    else:
        original = application.reminders.create_reminder(
            CreateReminder(title="Existing", due_at_utc=clock.now, scheduled_timezone="UTC")
        )
        reminder_id = original.id

        def reminder_write() -> str:
            try:
                application.reminders.update_reminder(
                    original.id,
                    UpdateReminder(
                        expected_version=original.version, **{f"{parent_kind}_id": parent.id}
                    ),
                )
                return "relinked"
            except NotFoundError:
                return "not-found"

    barrier = Barrier(2)

    def with_barrier(action: Callable[[], str]) -> str:
        barrier.wait()
        return action()

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(with_barrier, reminder_write)
        deleter = executor.submit(with_barrier, lambda: (delete_parent(), "deleted")[1])
        write_result, delete_result = writer.result(), deleter.result()

    assert delete_result == "deleted"
    assert write_result in {"created", "relinked", "not-found"}
    if reminder_id is not None:
        row = _fresh_row(app_config, reminder_id)
        link_position = 0 if parent_kind == "task" else 1
        if write_result != "not-found":
            assert row[link_position] == parent.id


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ("complete", "dismiss"),
        ("complete", "update"),
        ("dismiss", "update"),
        ("cancel", "update"),
        ("delete", "complete"),
        ("delete", "dismiss"),
        ("delete", "cancel"),
        ("delete", "update"),
    ),
)
def test_conflicting_active_lifecycle_writers_have_one_committed_winner(
    application, app_config, clock, left: str, right: str
) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Race", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed = _events(
        application,
        (
            "REMINDER_COMPLETED",
            "REMINDER_DISMISSED",
            "REMINDER_CANCELLED",
            "REMINDER_UPDATED",
            "REMINDER_DELETED",
        ),
    )
    barrier = Barrier(2)

    def invoke(kind: str) -> str:
        barrier.wait()
        try:
            if kind == "complete":
                return application.reminders.complete_reminder(reminder.id, reminder.version).status
            if kind == "dismiss":
                return application.reminders.dismiss_reminder(reminder.id, reminder.version).status
            if kind == "cancel":
                return application.reminders.cancel_reminder(reminder.id, reminder.version).status
            if kind == "delete":
                return "DELETED" if application.reminders.delete_reminder(reminder.id, reminder.version) else ""
            return application.reminders.update_reminder(
                reminder.id, UpdateReminder(expected_version=reminder.version, title="Updated")
            ).status
        except (InvalidStateTransitionError, NotFoundError, StaleVersionError) as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        left_future = executor.submit(invoke, left)
        right_future = executor.submit(invoke, right)
        results = [left_future.result(), right_future.result()]

    assert sum(result not in {"InvalidStateTransitionError", "NotFoundError", "StaleVersionError"} for result in results) == 1
    assert len(observed) == 1
    if "DELETED" in results:
        # Deleted for good: the row and its history are gone.
        with pytest.raises(NotFoundError):
            application.reminders.get_reminder(reminder.id)
        return
    row = _fresh_row(app_config, reminder.id)
    assert row[6] == reminder.version + 1
    terminal_count = sum(value is not None for value in row[8:11])
    assert terminal_count in {0, 1}
    assert _history_count(application, reminder.id) == 2


@pytest.mark.parametrize(
    "command",
    ("update", "relink", "snooze", "complete", "dismiss", "cancel", "delete"),
)
def test_stale_active_commands_do_not_drift_history_or_events(application, clock, command: str) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Stale", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    if command == "snooze":
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, title="Current")
        )
    else:
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, title="Current")
        )
    current = application.reminders.get_reminder(reminder.id)
    task = application.tasks.create_task(CreateTask(title="Relink target"))
    observed = _events(
        application,
        (
            "REMINDER_UPDATED",
            "REMINDER_SNOOZED",
            "REMINDER_COMPLETED",
            "REMINDER_DISMISSED",
            "REMINDER_CANCELLED",
            "REMINDER_DELETED",
        ),
    )
    count = _history_count(application, reminder.id)
    with pytest.raises(StaleVersionError):
        if command == "update":
            application.reminders.update_reminder(
                reminder.id, UpdateReminder(expected_version=reminder.version, title="Stale update")
            )
        elif command == "relink":
            application.reminders.update_reminder(
                reminder.id, UpdateReminder(expected_version=reminder.version, task_id=task.id)
            )
        elif command == "snooze":
            application.reminders.snooze_reminder(
                reminder.id,
                SnoozeReminder(expected_version=reminder.version, due_at_utc=clock.now + timedelta(hours=1)),
            )
        else:
            getattr(application.reminders, f"{command}_reminder")(reminder.id, reminder.version)
    after = application.reminders.get_reminder(reminder.id)
    assert after == current
    assert _history_count(application, reminder.id) == count
    assert observed == []


def test_stale_reopen_does_not_drift_history_or_events(application, clock) -> None:
    completed_source = application.reminders.create_reminder(
        CreateReminder(title="Complete", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    completed = application.reminders.complete_reminder(completed_source.id, completed_source.version)
    current = application.reminders.get_reminder(completed.id)
    count = _history_count(application, completed.id)
    with pytest.raises(StaleVersionError):
        application.reminders.reopen_reminder(completed.id, completed.version - 1)
    assert application.reminders.get_reminder(completed.id) == current
    assert _history_count(application, completed.id) == count


@pytest.mark.parametrize("terminal", ("complete", "dismiss", "cancel"))
def test_every_forbidden_terminal_command_preserves_reminder(application, clock, terminal: str) -> None:
    source = application.reminders.create_reminder(
        CreateReminder(title=terminal, due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    reminder = getattr(application.reminders, f"{terminal}_reminder")(source.id, source.version)
    forbidden = ("complete", "dismiss", "cancel", "snooze", "update")
    for command in forbidden:
        before = application.reminders.get_reminder(reminder.id)
        count = _history_count(application, reminder.id)
        observed = _events(
            application,
            (
                "REMINDER_CREATED",
                "REMINDER_UPDATED",
                "REMINDER_SNOOZED",
                "REMINDER_COMPLETED",
                "REMINDER_DISMISSED",
                "REMINDER_CANCELLED",
                "REMINDER_REOPENED",
                "REMINDER_DELETED",
            ),
        )
        with pytest.raises(InvalidStateTransitionError):
            if command == "snooze":
                application.reminders.snooze_reminder(
                    reminder.id,
                    SnoozeReminder(expected_version=reminder.version, due_at_utc=clock.now + timedelta(hours=1)),
                )
            elif command == "update":
                application.reminders.update_reminder(
                    reminder.id, UpdateReminder(expected_version=reminder.version, title="No")
                )
            else:
                getattr(application.reminders, f"{command}_reminder")(reminder.id, reminder.version)
        assert application.reminders.get_reminder(reminder.id) == before
        assert _history_count(application, reminder.id) == count
        assert observed == []


def test_reopen_is_forbidden_for_active_reminder_without_side_effects(application, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Active", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    count = _history_count(application, reminder.id)
    with pytest.raises(InvalidStateTransitionError):
        application.reminders.reopen_reminder(reminder.id, reminder.version)
    assert application.reminders.get_reminder(reminder.id).version == reminder.version
    assert _history_count(application, reminder.id) == count


def test_create_relink_lifecycle_snooze_and_commit_failures_roll_back_from_fresh_connection(
    application, app_config, clock, monkeypatch
) -> None:
    observed = _events(
        application,
        ("REMINDER_CREATED", "REMINDER_UPDATED", "REMINDER_COMPLETED", "REMINDER_SNOOZED"),
    )
    original_add_event = ReminderRepository.add_event

    def fail_history(self, event) -> None:
        original_add_event(self, event)
        raise RuntimeError("injected history failure")

    monkeypatch.setattr(ReminderRepository, "add_event", fail_history)
    with pytest.raises(RuntimeError, match="injected history failure"):
        application.reminders.create_reminder(
            CreateReminder(title="No row", due_at_utc=clock.now, scheduled_timezone="UTC")
        )
    fresh = create_sqlite_engine(app_config.database.path)
    try:
        with fresh.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM reminders WHERE title='No row'")).scalar_one() == 0
    finally:
        fresh.dispose()
    assert observed == []

    monkeypatch.setattr(ReminderRepository, "add_event", original_add_event)
    task = application.tasks.create_task(CreateTask(title="Link"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Before", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed.clear()
    before = _fresh_row(app_config, reminder.id)
    monkeypatch.setattr(ReminderRepository, "add_event", fail_history)
    with pytest.raises(RuntimeError):
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, task_id=task.id)
        )
    assert _fresh_row(app_config, reminder.id) == before
    assert _history_count(application, reminder.id) == 1
    assert observed == []

    monkeypatch.setattr(ReminderRepository, "add_event", original_add_event)
    due = application.reminders.create_reminder(
        CreateReminder(title="Due", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed.clear()
    before_due = _fresh_row(app_config, due.id)
    monkeypatch.setattr(ReminderRepository, "add_event", fail_history)
    with pytest.raises(RuntimeError):
        application.reminders.snooze_reminder(
            due.id, SnoozeReminder(expected_version=due.version, due_at_utc=clock.now + timedelta(minutes=1))
        )
    assert _fresh_row(app_config, due.id) == before_due
    assert _history_count(application, due.id) == 1
    assert observed == []

    monkeypatch.setattr(ReminderRepository, "add_event", original_add_event)
    lifecycle = application.reminders.create_reminder(
        CreateReminder(title="Lifecycle", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed.clear()
    before_lifecycle = _fresh_row(app_config, lifecycle.id)
    monkeypatch.setattr(ReminderRepository, "add_event", fail_history)
    with pytest.raises(RuntimeError):
        application.reminders.complete_reminder(lifecycle.id, lifecycle.version)
    assert _fresh_row(app_config, lifecycle.id) == before_lifecycle
    assert _history_count(application, lifecycle.id) == 1
    assert observed == []

    monkeypatch.setattr(ReminderRepository, "add_event", original_add_event)
    commit_target = application.reminders.create_reminder(
        CreateReminder(title="Commit", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed.clear()
    before_commit = _fresh_row(app_config, commit_target.id)

    def fail_commit(self) -> None:
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        application.reminders.update_reminder(
            commit_target.id, UpdateReminder(expected_version=commit_target.version, title="Never")
        )
    assert _fresh_row(app_config, commit_target.id) == before_commit
    assert _history_count(application, commit_target.id) == 1
    assert observed == []


def test_subscriber_failure_is_after_reminder_commit(application, app_config, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Before", due_at_utc=clock.now, scheduled_timezone="UTC")
    )

    def fail(_: DomainEvent) -> None:
        raise RuntimeError("subscriber failure")

    _bus(application).subscribe("REMINDER_UPDATED", fail)
    updated = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, title="After")
    )
    row = _fresh_row(app_config, reminder.id)
    assert row[6] == updated.version
    assert _history_count(application, reminder.id) == 2


def test_every_direct_command_records_exact_history_and_domain_event(application, clock) -> None:
    expected_types = (
        "REMINDER_CREATED",
        "REMINDER_UPDATED",
        "REMINDER_SNOOZED",
        "REMINDER_COMPLETED",
        "REMINDER_REOPENED",
        "REMINDER_DISMISSED",
        "REMINDER_REOPENED",
        "REMINDER_CANCELLED",
        "REMINDER_DELETED",
    )
    observed = _events(application, tuple(dict.fromkeys(expected_types)))
    due = application.reminders.create_reminder(
        CreateReminder(title="Due", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    updated = application.reminders.update_reminder(
        due.id, UpdateReminder(expected_version=due.version, title="Updated")
    )
    snoozed = application.reminders.snooze_reminder(
        updated.id,
        SnoozeReminder(expected_version=updated.version, due_at_utc=clock.now + timedelta(minutes=1)),
    )
    completed = application.reminders.complete_reminder(snoozed.id, snoozed.version)
    reopened = application.reminders.reopen_reminder(completed.id, completed.version)
    dismissed = application.reminders.dismiss_reminder(reopened.id, reopened.version)
    reopened_again = application.reminders.reopen_reminder(dismissed.id, dismissed.version)
    cancelled = application.reminders.cancel_reminder(reopened_again.id, reopened_again.version)
    history = application.reminders.list_reminder_events(due.id, ReminderPageQuery(limit=200))
    application.reminders.delete_reminder(cancelled.id, cancelled.version)

    assert [event.event_type for event in observed] == list(expected_types)
    for version, event in enumerate(observed, start=1):
        assert event.entity_id == due.id
        assert event.occurred_at_utc == clock.now
        assert event.payload == {}
        assert event.event_id == f"reminder:{event.event_type.lower()}:{due.id}:{version}"
    # The audit history is the same events minus the delete, which removed it with the row.
    assert len(history) == len(observed) - 1
    assert sorted(item.event_type for item in history) == sorted(expected_types[:-1])
    with pytest.raises(NotFoundError):
        application.reminders.list_reminder_events(due.id, ReminderPageQuery(limit=200))


def test_reminder_integrity_translation_remains_narrow() -> None:
    known_check = IntegrityError(
        "insert", {}, sqlite3.IntegrityError("CHECK constraint failed: ck_reminders_snooze_count")
    )
    with pytest.raises(ValidationError):
        _translate_reminder_integrity_error(known_check)
    duplicate = IntegrityError(
        "insert", {}, sqlite3.IntegrityError("UNIQUE constraint failed: reminders.id")
    )
    with pytest.raises(ConflictError):
        _translate_reminder_integrity_error(duplicate)
    unknown = IntegrityError("insert", {}, sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
    with pytest.raises(IntegrityError) as captured:
        _translate_reminder_integrity_error(unknown)
    assert captured.value is unknown


def test_reminder_filters_sorts_and_pagination_are_exact_and_stable(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Task"))
    note = application.notes.create_note(CreateNote(title="Note"))
    past = application.reminders.create_reminder(
        CreateReminder(title="C past", due_at_utc=clock.now - timedelta(microseconds=1), scheduled_timezone="UTC")
    )
    due = application.reminders.create_reminder(
        CreateReminder(title="B due", due_at_utc=clock.now, scheduled_timezone="UTC", task_id=task.id)
    )
    future = application.reminders.create_reminder(
        CreateReminder(title="A future", due_at_utc=clock.now + timedelta(microseconds=1), scheduled_timezone="UTC", note_id=note.id)
    )
    terminal = application.reminders.complete_reminder(past.id, past.version)
    deleted = application.reminders.delete_reminder(future.id, future.version)

    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(timing_states=("DUE",)))] == [due.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(link_type="TASK"))] == [due.id]
    assert application.reminders.list_reminders(ReminderQuery(link_type="NOTE")) == ()
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(link_type="NONE"))] == [terminal.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(statuses=("COMPLETED",)))] == [terminal.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(due_before_utc=clock.now))] == [terminal.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(due_from_utc=clock.now))] == [due.id]

    clock.now += timedelta(microseconds=1)
    earlier = application.reminders.create_reminder(
        CreateReminder(title="D earlier", due_at_utc=clock.now - timedelta(microseconds=2), scheduled_timezone="UTC")
    )
    clock.now += timedelta(microseconds=1)
    later = application.reminders.create_reminder(
        CreateReminder(title="Z later", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC")
    )
    visible = application.reminders.list_reminders(ReminderQuery(sort="due_soonest", limit=200))

    def epoch_micros(value) -> int:
        return int(value.timestamp() * 1_000_000)

    status_rank = {
        "PENDING": 0,
        "TRIGGERED": 1,
        "OVERDUE": 2,
        "DISMISSED": 3,
        "COMPLETED": 4,
        "CANCELLED": 5,
    }
    expected_keys = {
        "due_soonest": lambda item: (epoch_micros(item.due_at_utc), item.id),
        "due_latest": lambda item: (-epoch_micros(item.due_at_utc), item.id),
        "created_newest": lambda item: (-epoch_micros(item.created_at_utc), item.id),
        "created_oldest": lambda item: (epoch_micros(item.created_at_utc), item.id),
        "updated_newest": lambda item: (-epoch_micros(item.updated_at_utc), item.id),
        "updated_oldest": lambda item: (epoch_micros(item.updated_at_utc), item.id),
        "title": lambda item: (item.title, item.id),
        "status": lambda item: (status_rank[item.status], epoch_micros(item.due_at_utc), item.id),
    }
    for sort in (
        "due_soonest",
        "due_latest",
        "created_newest",
        "created_oldest",
        "updated_newest",
        "updated_oldest",
        "title",
        "status",
    ):
        first = application.reminders.list_reminders(ReminderQuery(sort=sort, limit=1, offset=0))
        second = application.reminders.list_reminders(ReminderQuery(sort=sort, limit=1, offset=1))
        repeated = application.reminders.list_reminders(ReminderQuery(sort=sort, limit=2, offset=0))
        expected = sorted(visible, key=expected_keys[sort])
        actual = application.reminders.list_reminders(ReminderQuery(sort=sort, limit=200))
        assert [item.id for item in actual] == [item.id for item in expected]
        assert [item.id for item in first + second] == [item.id for item in repeated]
    assert {earlier.id, later.id}.issubset({item.id for item in visible})
    assert application.reminders.list_reminders(ReminderQuery(sort="title", limit=200))[0].id == due.id
    with pytest.raises(PydanticValidationError):
        ReminderQuery(due_from_utc=clock.now, due_before_utc=clock.now)
    with pytest.raises(PydanticValidationError):
        ReminderQuery(link_type="ANY", linked_id=task.id)
    with pytest.raises(PydanticValidationError):
        ReminderQuery(statuses=("COMPLETED",), timing_states=("DUE",))
    assert deleted.id == future.id


def test_history_and_audit_event_pages_use_declared_tie_breakers(application, clock) -> None:
    completed = []
    for title in ("A", "B", "C"):
        source = application.reminders.create_reminder(
            CreateReminder(title=title, due_at_utc=clock.now, scheduled_timezone="UTC")
        )
        completed.append(application.reminders.complete_reminder(source.id, source.version))
    expected_history = sorted(item.id for item in completed)
    page_one = application.reminders.list_history(ReminderPageQuery(limit=2, offset=0))
    page_two = application.reminders.list_history(ReminderPageQuery(limit=2, offset=2))
    assert [item.id for item in page_one + page_two] == expected_history

    reminder = completed[0]
    events = application.reminders.list_reminder_events(reminder.id, ReminderPageQuery(limit=200))
    expected_events = sorted(item.id for item in events)
    first = application.reminders.list_reminder_events(reminder.id, ReminderPageQuery(limit=1, offset=0))
    second = application.reminders.list_reminder_events(reminder.id, ReminderPageQuery(limit=1, offset=1))
    assert [item.id for item in first + second] == expected_events
