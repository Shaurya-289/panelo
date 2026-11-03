"""Phase 5 must preserve the frozen post-commit domain-event contracts."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

import productivity_app.db.unit_of_work as uow_module
from productivity_app.core.events import DomainEvent, DomainEventType
from productivity_app.core.exceptions import ApplicationLifecycleError
from productivity_app.schemas.backups import RestoreBackup
from productivity_app.schemas.notes import CreateNote, UpdateNote
from productivity_app.schemas.reminders import CreateReminder, ReminderPageQuery, UpdateReminder
from productivity_app.schemas.settings import SettingsPatch, Theme
from productivity_app.schemas.tasks import CreateTask, UpdateTask
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    UpdateTimer,
    UpdateTimerPreset,
)


def test_representative_frozen_success_events_keep_exact_identity_contracts(application, clock) -> None:
    observed: list[DomainEvent] = []
    event_types = (
        "NOTE_CREATED", "TASK_CREATED", "REMINDER_CREATED", "TIMER_CREATED",
        "TIMER_PRESET_CREATED", "SETTINGS_CHANGED",
    )
    for event_type in event_types:
        application.subscribe(event_type, observed.append)

    note = application.notes.create_note(CreateNote(title="event note", content="body"))
    task = application.tasks.create_task(CreateTask(title="event task"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="event reminder", due_at_utc=clock.now + timedelta(days=1), scheduled_timezone="UTC")
    )
    timer = application.timers.create_timer(CreateTimer(name="event timer", planned_duration_ms=1_000))
    preset = application.timers.create_preset(CreateTimerPreset(name="event preset", duration_ms=1_000))
    settings = application.settings.get_settings()
    changed_settings = application.settings.update_settings(
        SettingsPatch(expected_version=settings.version, theme=Theme.DARK)
    )

    expected = (
        ("NOTE_CREATED", note.id, f"note:NOTE_CREATED:{note.id}:1"),
        ("TASK_CREATED", task.id, f"task:task_created:{task.id}:1"),
        ("REMINDER_CREATED", reminder.id, f"reminder:reminder_created:{reminder.id}:1"),
        ("TIMER_CREATED", timer.id, f"timer:timer_created:{timer.id}:1"),
        ("TIMER_PRESET_CREATED", preset.id, f"timer_preset:timer_preset_created:{preset.id}:1"),
        ("SETTINGS_CHANGED", "1", "settings:1"),
    )
    assert [(event.event_type, event.entity_id, event.event_id) for event in observed] == list(expected)
    assert all(event.occurred_at_utc == clock.now and event.payload == {} for event in observed)
    assert changed_settings.version == settings.version + 1


def test_note_update_event_keeps_its_exact_post_commit_identity_contract(application, clock) -> None:
    note = application.notes.create_note(CreateNote(title="event note"))
    clock.now += timedelta(microseconds=1)
    observed: list[DomainEvent] = []
    persisted_at_delivery = []

    def capture(event: DomainEvent) -> None:
        observed.append(event)
        persisted_at_delivery.append(application.notes.get_note(event.entity_id))

    application.subscribe("NOTE_UPDATED", capture)
    updated = application.notes.update_note(
        note.id, UpdateNote(expected_version=note.version, title="renamed event note", color="teal")
    )

    assert updated.version == note.version + 1 and updated.color == "teal"
    assert [(event.event_type, event.entity_id, event.event_id) for event in observed] == [
        ("NOTE_UPDATED", note.id, f"note:NOTE_UPDATED:{note.id}:{updated.version}")
    ]
    assert observed[0].occurred_at_utc == clock.now
    assert observed[0].payload == {}
    assert [item.model_dump() for item in persisted_at_delivery] == [updated.model_dump()]


def test_failed_commits_clear_staged_frozen_events_and_do_not_leak_to_later_commit(application, clock, monkeypatch) -> None:
    note = application.notes.create_note(CreateNote(title="before"))
    task = application.tasks.create_task(CreateTask(title="before"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="before", due_at_utc=clock.now + timedelta(days=1), scheduled_timezone="UTC")
    )
    timer = application.timers.create_timer(CreateTimer(name="before", planned_duration_ms=1_000))
    preset = application.timers.create_preset(CreateTimerPreset(name="before", duration_ms=1_000))
    observed: list[DomainEvent] = []
    for event_type in ("NOTE_UPDATED", "TASK_UPDATED", "REMINDER_UPDATED", "TIMER_UPDATED", "TIMER_PRESET_UPDATED"):
        application.subscribe(event_type, observed.append)
    original_commit = uow_module.SqlAlchemyUnitOfWork.commit

    def fail_after_collection(self) -> None:  # type: ignore[no-untyped-def]
        assert self.session is not None
        self.session.rollback()
        self._events.clear()
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(uow_module.SqlAlchemyUnitOfWork, "commit", fail_after_collection)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        application.notes.update_note(note.id, UpdateNote(expected_version=note.version, title="changed"))
    with pytest.raises(RuntimeError, match="injected commit failure"):
        application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="changed"))
    with pytest.raises(RuntimeError, match="injected commit failure"):
        application.reminders.update_reminder(reminder.id, UpdateReminder(expected_version=reminder.version, title="changed"))
    with pytest.raises(RuntimeError, match="injected commit failure"):
        application.timers.update_timer(timer.id, UpdateTimer(expected_version=timer.version, name="changed"))
    with pytest.raises(RuntimeError, match="injected commit failure"):
        application.timers.update_preset(preset.id, UpdateTimerPreset(expected_version=preset.version, name="changed"))
    assert observed == []
    assert application.notes.get_note(note.id).title == "before"
    assert application.tasks.get_task(task.id).title == "before"
    assert application.reminders.get_reminder(reminder.id).title == "before"
    assert application.timers.get_timer(timer.id).name == "before"
    assert application.timers.get_preset(preset.id).name == "before"

    monkeypatch.setattr(uow_module.SqlAlchemyUnitOfWork, "commit", original_commit)
    changed = application.notes.update_note(note.id, UpdateNote(expected_version=note.version, title="after"))
    assert [(event.event_type, event.entity_id, event.event_id) for event in observed] == [
        ("NOTE_UPDATED", note.id, f"note:NOTE_UPDATED:{note.id}:{changed.version}")
    ]


def test_primary_failure_after_event_collection_rolls_back_frozen_domain_mutations(application, clock, monkeypatch) -> None:
    """A command that has staged an event but exits exceptionally publishes nothing."""
    note = application.notes.create_note(CreateNote(title="note before"))
    task = application.tasks.create_task(CreateTask(title="task before"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="reminder before", due_at_utc=clock.now + timedelta(days=1), scheduled_timezone="UTC")
    )
    timer = application.timers.create_timer(CreateTimer(name="timer before", planned_duration_ms=1_000))
    observed: list[DomainEvent] = []
    for event_type in ("NOTE_UPDATED", "TASK_UPDATED", "REMINDER_UPDATED", "TIMER_UPDATED"):
        application.subscribe(event_type, observed.append)
    original_collect = uow_module.SqlAlchemyUnitOfWork.collect_event

    def collect_then_abort(self, event: DomainEvent) -> None:  # type: ignore[no-untyped-def]
        original_collect(self, event)
        raise RuntimeError("abort after staging event")

    monkeypatch.setattr(uow_module.SqlAlchemyUnitOfWork, "collect_event", collect_then_abort)
    actions = (
        lambda: application.notes.update_note(note.id, UpdateNote(expected_version=note.version, title="note after")),
        lambda: application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="task after")),
        lambda: application.reminders.update_reminder(reminder.id, UpdateReminder(expected_version=reminder.version, title="reminder after")),
        lambda: application.timers.update_timer(timer.id, UpdateTimer(expected_version=timer.version, name="timer after")),
    )
    for action in actions:
        with pytest.raises(RuntimeError, match="abort after staging event"):
            action()
    assert observed == []
    assert application.notes.get_note(note.id).title == "note before"
    assert application.tasks.get_task(task.id).title == "task before"
    assert application.reminders.get_reminder(reminder.id).title == "reminder before"
    assert application.timers.get_timer(timer.id).name == "timer before"


def test_subscription_closure_duplicates_and_failing_subscribers_preserve_durable_commit(application) -> None:
    deliveries: list[str] = []

    def duplicate(event: DomainEvent) -> None:
        deliveries.append(event.entity_id)

    first = application.subscribe("TASK_CREATED", duplicate)
    second = application.subscribe("TASK_CREATED", duplicate)
    one = application.tasks.create_task(CreateTask(title="one"))
    assert deliveries == [one.id, one.id]
    first.close()
    first.close()
    two = application.tasks.create_task(CreateTask(title="two"))
    assert deliveries == [one.id, one.id, two.id]
    second.close()
    application.tasks.create_task(CreateTask(title="three"))
    assert deliveries == [one.id, one.id, two.id]

    note = application.notes.create_note(CreateNote(title="old"))
    delivered: list[DomainEvent] = []
    application.subscribe("NOTE_UPDATED", lambda _event: (_ for _ in ()).throw(RuntimeError("subscriber failure")))
    application.subscribe("NOTE_UPDATED", delivered.append)
    updated = application.notes.update_note(note.id, UpdateNote(expected_version=note.version, title="new"))
    assert [(event.event_type, event.entity_id) for event in delivered] == [("NOTE_UPDATED", note.id)]
    connection = sqlite3.connect(application._config.database.path)
    try:
        row = connection.execute("SELECT title, version FROM notes WHERE id = ?", (note.id,)).fetchone()
    finally:
        connection.close()
    assert row == ("new", updated.version)


def test_event_order_registry_and_runtime_swap_do_not_mix_generations(application, clock) -> None:
    expected_registry = {
        "NOTE_CREATED", "NOTE_UPDATED", "NOTE_PINNED", "NOTE_UNPINNED", "NOTE_ARCHIVED",
        "NOTE_UNARCHIVED", "NOTE_DELETED", "NOTE_RESTORED", "NOTE_PURGED", "TASK_CREATED",
        "TASK_UPDATED", "TASK_STARTED", "TASK_RETURNED_TO_TODO", "TASK_COMPLETED", "TASK_CANCELLED",
        "TASK_REOPENED", "TASK_DELETED", "TASK_RESTORED", "TASK_PURGED", "REMINDER_CREATED",
        "REMINDER_UPDATED", "REMINDER_SNOOZED", "REMINDER_COMPLETED", "REMINDER_DISMISSED",
        "REMINDER_CANCELLED", "REMINDER_REOPENED", "REMINDER_DELETED", "TIMER_CREATED",
        "TIMER_UPDATED", "TIMER_STARTED", "TIMER_PAUSED", "TIMER_RESUMED", "TIMER_RESET", "TIMER_COMPLETED",
        "TIMER_CANCELLED", "TIMER_DELETED", "TIMER_RESTORED", "TIMER_PRESET_CREATED", "TIMER_PRESET_UPDATED",
        "TIMER_PRESET_MOVED", "TIMER_PRESET_DELETED", "TIMER_PRESET_RESTORED", "SETTINGS_CHANGED",
        "FOCUS_SEQUENCE_STARTED", "FOCUS_PHASE_ADVANCED", "FOCUS_SEQUENCE_COMPLETED",
        "FOCUS_SEQUENCE_CANCELLED",
    }
    assert {item.value for item in DomainEventType} == expected_registry
    assert {"SEARCH_COMPLETED", "BACKUP_CREATED", "BACKUP_DELETED", "RESTORE_COMPLETED", "NOTIFICATION_SENT"}.isdisjoint(expected_registry)
    with pytest.raises(ValueError, match="Unknown domain event type"):
        application.subscribe("BACKUP_CREATED", lambda _event: None)

    old_deliveries: list[DomainEvent] = []
    application.subscribe("TASK_CREATED", old_deliveries.append)
    old_tasks, old_bus = application.tasks, application._event_bus
    backup = application.backups.create_backup()
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    new_deliveries: list[DomainEvent] = []
    application.subscribe("TASK_CREATED", new_deliveries.append)
    old_bus.publish(DomainEvent("TASK_CREATED", "old", clock.now, "task:task_created:old:1"))
    assert [event.entity_id for event in old_deliveries] == ["old"]
    assert new_deliveries == []
    with pytest.raises(ApplicationLifecycleError):
        old_tasks.create_task(CreateTask(title="stale"))
    fresh = application.tasks.create_task(CreateTask(title="fresh"))
    assert [event.entity_id for event in new_deliveries] == [fresh.id]
    assert [event.entity_id for event in old_deliveries] == ["old"]


def test_timer_completion_and_reminder_time_passage_keep_frozen_event_semantics(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="natural", planned_duration_ms=1))
    timer = application.timers.start_timer(timer.id, timer.version)
    deadline = timer.deadline_at_utc
    reminder = application.reminders.create_reminder(
        CreateReminder(title="time-only", due_at_utc=clock.now + timedelta(milliseconds=1), scheduled_timezone="UTC")
    )
    observed: list[DomainEvent] = []
    application.subscribe("TIMER_COMPLETED", observed.append)
    application.subscribe("REMINDER_UPDATED", observed.append)
    initial_history = application.reminders.list_reminder_events(reminder.id, ReminderPageQuery())
    clock.now += timedelta(seconds=1)
    assert application.reminders.get_reminder(reminder.id).timing_state == "OVERDUE"
    assert application.reminders.list_reminder_events(reminder.id, ReminderPageQuery()) == initial_history
    (completed,) = application.timers.reconcile_expired_timers()
    assert completed.ended_at_utc == deadline
    assert [(event.event_type, event.entity_id, event.event_id, event.occurred_at_utc, event.payload) for event in observed] == [
        ("TIMER_COMPLETED", timer.id, f"timer:timer_completed:{timer.id}:{completed.version}", clock.now, {})
    ]
    assert application.timers.reconcile_expired_timers() == ()
    assert len(observed) == 1
    updated = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, title="mutated")
    )
    assert [(event.event_type, event.entity_id, event.event_id) for event in observed] == [
        ("TIMER_COMPLETED", timer.id, f"timer:timer_completed:{timer.id}:{completed.version}"),
        ("REMINDER_UPDATED", reminder.id, f"reminder:reminder_updated:{reminder.id}:{updated.version}"),
    ]
