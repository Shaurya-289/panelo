from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text

from panelo.core.exceptions import (
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from panelo.schemas.notes import CreateNote
from panelo.schemas.reminders import CreateReminder, SnoozeReminder, UpdateReminder
from panelo.schemas.tasks import CreateTask


def _events(application, event_type: str) -> list:
    observed: list = []
    application.unit_of_work_factory()._event_bus.subscribe(event_type, observed.append)
    return observed


def test_reminder_create_update_links_history_and_events(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Task"))
    note = application.notes.create_note(CreateNote(title="Note"))
    observed = _events(application, "REMINDER_UPDATED")
    reminder = application.reminders.create_reminder(
        CreateReminder(
            title="  Reminder  ",
            message="keep spacing",
            due_at_utc=clock.now + timedelta(hours=1),
            scheduled_timezone="UTC",
            task_id=task.id,
        )
    )
    assert reminder.title == "Reminder"
    assert reminder.task_id == task.id and reminder.note_id is None
    updated = application.reminders.update_reminder(
        reminder.id,
        UpdateReminder(expected_version=reminder.version, task_id=None, note_id=note.id),
    )
    assert updated.task_id is None and updated.note_id == note.id
    assert updated.version == reminder.version + 1
    assert observed[0].event_id == f"reminder:reminder_updated:{reminder.id}:{updated.version}"
    events = application.reminders.list_reminder_events(reminder.id)
    assert {event.event_type for event in events} == {"REMINDER_UPDATED", "REMINDER_CREATED"}


def test_reminder_lifecycle_snooze_permanent_delete_and_exact_timing(application, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Now", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    assert reminder.timing_state == "DUE"
    snoozed = application.reminders.snooze_reminder(
        reminder.id,
        SnoozeReminder(expected_version=reminder.version, due_at_utc=clock.now + timedelta(minutes=5)),
    )
    assert snoozed.status == "PENDING" and snoozed.snooze_count == 1
    completed = application.reminders.complete_reminder(snoozed.id, snoozed.version)
    assert completed.status == "COMPLETED" and completed.completed_at_utc == clock.now
    reopened = application.reminders.reopen_reminder(completed.id, completed.version)
    assert reopened.status == "PENDING" and reopened.completed_at_utc is None
    deleted = application.reminders.delete_reminder(reopened.id, reopened.version)
    assert deleted.id == reopened.id and deleted.title == "Now"
    # Reminders are deleted for good: there is no Trash entry to read back or restore.
    with pytest.raises(NotFoundError):
        application.reminders.get_reminder(deleted.id)
    with pytest.raises(NotFoundError):
        application.reminders.list_reminder_events(deleted.id)
    assert application.reminders.list_reminders() == ()
    with application.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM reminders")) == 0
        assert connection.scalar(text("SELECT count(*) FROM reminder_events")) == 0
    with pytest.raises(NotFoundError):
        application.reminders.snooze_reminder(
            deleted.id,
            SnoozeReminder(expected_version=deleted.version, due_at_utc=clock.now + timedelta(minutes=6)),
        )


def test_reminder_validation_and_stale_writes(application, clock) -> None:
    with pytest.raises(PydanticValidationError):
        application.reminders.create_reminder(
            CreateReminder(title="Both", due_at_utc=clock.now, scheduled_timezone="UTC", task_id="x", note_id="y")
        )
    reminder = application.reminders.create_reminder(
        CreateReminder(title="One", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC")
    )
    with pytest.raises(ValidationError):
        application.reminders.update_reminder(reminder.id, UpdateReminder(expected_version=reminder.version))
    updated = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, title="Two")
    )
    with pytest.raises(StaleVersionError):
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, title="Three")
        )
    assert application.reminders.get_reminder(updated.id).title == "Two"
