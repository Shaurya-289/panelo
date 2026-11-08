from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from panelo.core.exceptions import (
    InvalidStateTransitionError,
    NotFoundError,
    ValidationError,
)
from panelo.db.engine import create_sqlite_engine
from panelo.repositories.reminders import ReminderRepository
from panelo.schemas.reminders import (
    CreateReminder,
    ReminderQuery,
    SnoozeReminder,
    UpdateReminder,
)
from panelo.schemas.tasks import CreateTask


def _bus(application):
    return application.unit_of_work_factory()._event_bus


def test_due_views_filters_history_and_terminal_lifecycle(application, clock) -> None:
    overdue = application.reminders.create_reminder(
        CreateReminder(title="Overdue", due_at_utc=clock.now - timedelta(microseconds=1), scheduled_timezone="UTC")
    )
    due = application.reminders.create_reminder(
        CreateReminder(title="Due", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    upcoming = application.reminders.create_reminder(
        CreateReminder(title="Upcoming", due_at_utc=clock.now + timedelta(microseconds=1), scheduled_timezone="UTC")
    )
    dismissed = application.reminders.dismiss_reminder(due.id, due.version)
    cancelled = application.reminders.cancel_reminder(overdue.id, overdue.version)

    assert [item.id for item in application.reminders.list_due()] == []
    assert [item.id for item in application.reminders.list_overdue()] == []
    assert [item.id for item in application.reminders.list_upcoming()] == [upcoming.id]
    assert {item.id for item in application.reminders.list_history()} == {dismissed.id, cancelled.id}
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(statuses=("DISMISSED",)))] == [dismissed.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(timing_states=("UPCOMING",)))] == [upcoming.id]


def test_parent_soft_delete_preserves_historical_link_and_restore(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Linked"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Reminder", due_at_utc=clock.now, scheduled_timezone="UTC", task_id=task.id)
    )
    deleted_task = application.tasks.delete_task(task.id, task.version)
    assert application.reminders.get_reminder(reminder.id).task_id == task.id
    updated = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, title="Still independent")
    )
    assert updated.task_id == task.id
    restored_task = application.tasks.restore_task(deleted_task.id, deleted_task.version)
    assert restored_task.deleted_at_utc is None
    assert application.reminders.get_reminder(reminder.id).task_id == task.id
    with pytest.raises(NotFoundError):
        application.reminders.create_reminder(
            CreateReminder(title="No deleted parent", due_at_utc=clock.now, scheduled_timezone="UTC", task_id="missing")
        )


def test_update_patch_nulls_query_validation_and_snooze_rules(application, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="One", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    with pytest.raises(ValidationError):
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, due_at_utc=None)
        )
    with pytest.raises(ValidationError):
        application.reminders.snooze_reminder(
            reminder.id, SnoozeReminder(expected_version=reminder.version, due_at_utc=clock.now)
        )
    with pytest.raises(PydanticValidationError):
        ReminderQuery(due_from_utc=clock.now + timedelta(seconds=1), due_before_utc=clock.now)
    with pytest.raises(PydanticValidationError):
        ReminderQuery(link_type="ANY", linked_id="unexpected")
    with pytest.raises(PydanticValidationError):
        ReminderQuery(statuses=("COMPLETED",), timing_states=("OVERDUE",))


def test_history_failure_after_claim_rolls_back_row_and_publishes_nothing(
    application, app_config, clock, monkeypatch
) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Before", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC")
    )
    observed: list = []
    _bus(application).subscribe("REMINDER_UPDATED", observed.append)
    original = ReminderRepository.add_event

    def add_then_fail(self, event) -> None:
        original(self, event)
        raise IntegrityError("injected after reminder history write", {}, RuntimeError("unexpected"))

    monkeypatch.setattr(ReminderRepository, "add_event", add_then_fail)
    with pytest.raises(IntegrityError, match="injected after reminder history write"):
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, title="After")
        )

    fresh = create_sqlite_engine(app_config.database.path)
    try:
        with fresh.connect() as connection:
            row = connection.execute(
                text("SELECT title, version, updated_at_utc FROM reminders WHERE id=:id"), {"id": reminder.id}
            ).one()
            assert row == ("Before", reminder.version, reminder.updated_at_utc.timestamp() * 1_000_000)
            assert connection.execute(
                text("SELECT count(*) FROM reminder_events WHERE reminder_id=:id"), {"id": reminder.id}
            ).scalar_one() == 1
    finally:
        fresh.dispose()
    assert observed == []


def test_terminal_reminders_require_reopen_before_editing(application, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Terminal", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    completed = application.reminders.complete_reminder(reminder.id, reminder.version)
    with pytest.raises(InvalidStateTransitionError):
        application.reminders.update_reminder(
            completed.id, UpdateReminder(expected_version=completed.version, title="No direct edit")
        )
