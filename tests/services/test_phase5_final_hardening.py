"""Adversarial regression proofs for Phase 5 runtime, search, and backup contracts."""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import timedelta
from threading import Barrier, Event, Lock, Thread

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text

import panelo.bootstrap as bootstrap_module
import panelo.db.unit_of_work as uow_module
from panelo.core.events import DomainEventType, InProcessEventBus
from panelo.core.exceptions import (
    ApplicationLifecycleError,
    BackupError,
    BackupRecoveryError,
    BackupRestoreError,
    BackupValidationError,
    NotFoundError,
    UnsupportedBackupVersionError,
    ValidationError,
)
from panelo.core.lifecycle import ApplicationLifecycleGate
from panelo.schemas.backups import (
    BackupPageQuery,
    DeleteBackup,
    RestoreBackup,
    ValidateBackup,
)
from panelo.schemas.notes import CreateNote, UpdateNote
from panelo.schemas.reminders import CreateReminder, ReminderPageQuery
from panelo.schemas.search import UnifiedSearchQuery
from panelo.schemas.tasks import CreateTask, UpdateTask
from panelo.schemas.timers import CreateTimer, CreateTimerPreset


def test_every_captured_runtime_handle_rejects_after_restore(application) -> None:
    task = application.tasks.create_task(CreateTask(title="Backup-era"))
    backup = application.backups.create_backup()
    old_tasks, old_search, old_backups = application.tasks, application.search, application.backups
    old_factory = application.unit_of_work_factory
    with old_factory() as uow:
        assert uow.tasks is not None and uow.session is not None
        repository, session = uow.tasks, uow.session

    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))

    with pytest.raises(ApplicationLifecycleError):
        old_tasks.get_task(task.id)
    with pytest.raises(ApplicationLifecycleError):
        old_search.search(UnifiedSearchQuery(query="Backup-era"))
    with pytest.raises(ApplicationLifecycleError):
        old_backups.list_backups()
    with pytest.raises(ApplicationLifecycleError):
        old_factory().__enter__()
    with pytest.raises(ApplicationLifecycleError):
        repository.get(task.id)
    with pytest.raises(ApplicationLifecycleError):
        session.execute("SELECT 1")
    assert application.tasks.get_task(task.id).title == "Backup-era"


@pytest.mark.parametrize("failure", ["sidecar", "replace"])
def test_post_disposal_failures_recover_original_runtime(application, monkeypatch, failure: str) -> None:
    task = application.tasks.create_task(CreateTask(title="Original"))
    backup = application.backups.create_backup()
    if failure == "sidecar":
        monkeypatch.setattr(bootstrap_module.Application, "_remove_sidecars", staticmethod(lambda _live: (_ for _ in ()).throw(OSError("locked"))))
    else:
        monkeypatch.setattr(bootstrap_module.os, "replace", lambda _a, _b: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(BackupRestoreError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert application.is_closed is False
    assert application.tasks.get_task(task.id).title == "Original"


def test_post_replacement_runtime_failure_recovers_safety_database(application, monkeypatch) -> None:
    original = application.tasks.create_task(CreateTask(title="Original"))
    backup = application.backups.create_backup()
    application.tasks.update_task(original.id, UpdateTask(expected_version=original.version, title="Changed"))
    compose = bootstrap_module._compose_application
    calls = 0

    def fail_once(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("new runtime failed")
        return compose(*args, **kwargs)

    monkeypatch.setattr(bootstrap_module, "_compose_application", fail_once)
    with pytest.raises(BackupRestoreError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert calls == 2
    assert application.tasks.get_task(original.id).title == "Changed"


def test_double_restore_and_recovery_failure_preserves_both_causes(application, monkeypatch) -> None:
    backup = application.backups.create_backup()
    monkeypatch.setattr(bootstrap_module, "_compose_application", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("compose")))
    with pytest.raises(BackupRecoveryError) as raised:
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert isinstance(raised.value.restore_failure, Exception)
    assert isinstance(raised.value.recovery_failure, Exception)
    assert raised.value.__cause__ is raised.value.recovery_failure
    assert application.is_closed is True


@pytest.mark.parametrize(
    "repository_name",
    ["SettingsRepository", "NoteRepository", "TaskRepository", "ReminderRepository", "TimerRepository", "SearchRepository"],
)
def test_uow_initialization_failures_release_lease_and_close_session(application, monkeypatch, repository_name: str) -> None:
    class Broken:
        def __init__(self, _session: object) -> None:
            raise RuntimeError(repository_name)
    monkeypatch.setattr(uow_module, repository_name, Broken)
    with pytest.raises(RuntimeError, match=repository_name):
        application.unit_of_work_factory().__enter__()
    assert application._gate._operations == 0
    application.close()
    assert application.is_closed


def test_search_global_rank_filters_and_pagination_are_exact(application) -> None:
    task = application.tasks.create_task(CreateTask(title="needle"))
    note = application.notes.create_note(CreateNote(title="needle note", content="body"))
    body = application.notes.create_note(CreateNote(title="Other", content="needle"))
    prefix = application.tasks.create_task(CreateTask(title="needle-prefix"))
    application.tasks.delete_task(prefix.id, prefix.version)

    all_rows = application.search.search(UnifiedSearchQuery(query="needle"))
    assert [(row.entity_type, row.entity_id, row.matched_field) for row in all_rows] == [
        ("TASK", task.id, "title"), ("NOTE", note.id, "title"), ("NOTE", body.id, "content"),
    ]
    assert [row.entity_id for row in application.search.search(UnifiedSearchQuery(query="needle", entity_types=("TASK",)))] == [task.id]
    included = application.search.search(UnifiedSearchQuery(query="needle", include_deleted=True))
    assert prefix.id in {row.entity_id for row in included}
    first = application.search.search(UnifiedSearchQuery(query="needle", limit=2))
    second = application.search.search(UnifiedSearchQuery(query="needle", offset=2, limit=2))
    assert first == application.search.search(UnifiedSearchQuery(query="needle", limit=2))
    assert second == application.search.search(UnifiedSearchQuery(query="needle", offset=2, limit=2))
    assert not set(row.entity_id for row in first) & set(row.entity_id for row in second)
    with pytest.raises(PydanticValidationError):
        UnifiedSearchQuery(query=" ")
    with pytest.raises(PydanticValidationError):
        UnifiedSearchQuery(query="x" * 201)


@pytest.mark.parametrize("term", ["%", "_", "\\", "100%", "a_b"])
def test_search_wildcards_are_literal(application, term: str) -> None:
    matching = application.tasks.create_task(CreateTask(title=f"value {term}"))
    application.tasks.create_task(CreateTask(title="unrelated value"))
    assert [row.entity_id for row in application.search.search(UnifiedSearchQuery(query=f"  {term}  "))] == [matching.id]


def test_search_derived_states_are_read_only(application, clock) -> None:
    future = application.reminders.create_reminder(CreateReminder(title="future reminder", due_at_utc=clock.now + timedelta(seconds=2), scheduled_timezone="UTC"))
    overdue = application.reminders.create_reminder(CreateReminder(title="overdue reminder", due_at_utc=clock.now - timedelta(seconds=1), scheduled_timezone="UTC"))
    timer = application.timers.create_timer(CreateTimer(name="expired timer", planned_duration_ms=1))
    application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(seconds=1)
    before_future = application.reminders.get_reminder(future.id)
    before_overdue = application.reminders.get_reminder(overdue.id)
    timer_before = application.timers.get_timer(timer.id)
    rows = application.search.search(UnifiedSearchQuery(query="reminder", entity_types=("REMINDER",)))
    mapping = {row.entity_id: row.derived_state for row in rows}
    assert mapping == {future.id: "UPCOMING", overdue.id: "OVERDUE"}
    expired = application.search.search(UnifiedSearchQuery(query="expired timer", entity_types=("TIMER",)))[0]
    assert expired.persisted_state == "RUNNING" and expired.derived_state == "FINISHED"
    assert application.reminders.get_reminder(future.id).version == before_future.version
    assert application.reminders.get_reminder(overdue.id).version == before_overdue.version
    assert application.timers.get_timer(timer.id).version == timer_before.version


def test_unified_search_covers_all_entities_and_global_tiers(application, clock) -> None:
    """One query ranks all five branches, then filters and pages deterministically."""
    preset = application.timers.create_preset(CreateTimerPreset(name="needle", duration_ms=10))
    task = application.tasks.create_task(CreateTask(title="needle"))
    note = application.notes.create_note(CreateNote(title="needle", content="body"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="needle", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    timer = application.timers.create_timer(CreateTimer(name="needle", planned_duration_ms=10))
    prefix = application.notes.create_note(CreateNote(title="needle prefix", content="body"))
    contains = application.timers.create_timer(CreateTimer(name="x needle x", planned_duration_ms=10))
    body = application.notes.create_note(CreateNote(title="other", content="needle"))

    rows = application.search.search(UnifiedSearchQuery(query="needle"))
    assert [(row.entity_type, row.entity_id, row.matched_field) for row in rows] == [
        ("TASK", task.id, "title"),
        ("NOTE", note.id, "title"),
        ("REMINDER", reminder.id, "title"),
        ("TIMER", timer.id, "name"),
        ("TIMER_PRESET", preset.id, "name"),
        ("NOTE", prefix.id, "title"),
        ("TIMER", contains.id, "name"),
        ("NOTE", body.id, "content"),
    ]
    assert [row.entity_id for row in application.search.search(
        UnifiedSearchQuery(query="needle", entity_types=("TIMER", "TASK", "TIMER"))
    )] == [task.id, timer.id, contains.id]
    first = application.search.search(UnifiedSearchQuery(query="needle", limit=4))
    second = application.search.search(UnifiedSearchQuery(query="needle", limit=4, offset=4))
    assert first + second == tuple(rows[:8])
    assert first == application.search.search(UnifiedSearchQuery(query="needle", limit=4))
    assert second == application.search.search(UnifiedSearchQuery(query="needle", limit=4, offset=4))
    assert not {row.entity_id for row in first} & {row.entity_id for row in second}


def test_search_due_and_timer_deadline_boundaries_are_derived_without_writes(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="deadline boundary", planned_duration_ms=1))
    timer = application.timers.start_timer(timer.id, timer.version)
    due = application.reminders.create_reminder(
        CreateReminder(title="due boundary", due_at_utc=timer.deadline_at_utc, scheduled_timezone="UTC")
    )
    before_due = application.reminders.get_reminder(due.id)
    before_timer = application.timers.get_timer(timer.id)
    clock.now = timer.deadline_at_utc
    observed: list[object] = []
    application.subscribe(DomainEventType.TIMER_COMPLETED, observed.append)
    reminder_result = application.search.search(UnifiedSearchQuery(query="due boundary"))[0]
    timer_result = application.search.search(UnifiedSearchQuery(query="deadline boundary"))[0]
    assert (reminder_result.persisted_state, reminder_result.derived_state) == ("PENDING", "DUE")
    assert (timer_result.persisted_state, timer_result.derived_state) == ("RUNNING", "FINISHED")
    after_due = application.reminders.get_reminder(due.id)
    after_timer = application.timers.get_timer(timer.id)
    assert (after_due.version, after_due.updated_at_utc) == (before_due.version, before_due.updated_at_utc)
    assert (after_timer.version, after_timer.updated_at_utc, after_timer.state) == (
        before_timer.version, before_timer.updated_at_utc, "RUNNING"
    )
    assert observed == []


def test_backup_path_safety_and_list_pagination(application, tmp_path) -> None:
    first = application.backups.create_backup()
    second = application.backups.create_backup()
    page_one = application.backups.list_backups(BackupPageQuery(limit=1))
    page_two = application.backups.list_backups(BackupPageQuery(limit=1, offset=1))
    assert page_one == application.backups.list_backups(BackupPageQuery(limit=1))
    assert {item.filename for item in page_one}.isdisjoint({item.filename for item in page_two})
    assert {first.filename, second.filename} == {item.filename for item in page_one + page_two}
    for unsafe in ("../x.sqlite3", "a/b.sqlite3", str(tmp_path / "x.sqlite3"), "not-a-backup"):
        with pytest.raises(ValidationError):
            application.backups.delete_backup(DeleteBackup(filename=unsafe))


def test_backup_validation_rejects_corrupt_nonregular_and_symlink_sources(application, tmp_path, monkeypatch) -> None:
    """Validation must never accept malformed sources before restore can see them."""
    good = application.backups.create_backup()
    corrupt = tmp_path / "corrupt.sqlite3"
    shutil.copyfile(application.backups.backup_directory / good.filename, corrupt)
    with corrupt.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"not a sqlite header")
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=corrupt))
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=tmp_path))

    source = tmp_path / "symlink-source.sqlite3"
    shutil.copyfile(application.backups.backup_directory / good.filename, source)
    original_lstat = type(source).lstat

    class LinkStat:
        st_mode = 0o120777

    def simulated_symlink(self):  # type: ignore[no-untyped-def]
        if self == source:
            return LinkStat()
        return original_lstat(self)

    monkeypatch.setattr(type(source), "lstat", simulated_symlink)
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=source))


def test_event_subscriptions_remain_post_commit_and_do_not_survive_restore(application) -> None:
    observed = []
    application.subscribe(DomainEventType.TASK_CREATED, observed.append)
    task = application.tasks.create_task(CreateTask(title="Event"))
    assert [(event.event_type, event.entity_id) for event in observed] == [("TASK_CREATED", task.id)]
    backup = application.backups.create_backup()
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    application.tasks.create_task(CreateTask(title="No old subscriber"))
    assert len(observed) == 1


def test_running_paused_and_expired_timers_have_restart_safe_restore_semantics(application, clock) -> None:
    running = application.timers.create_timer(CreateTimer(name="running", planned_duration_ms=10_000))
    running = application.timers.start_timer(running.id, running.version)
    running_start, running_deadline = running.run_started_at_utc, running.deadline_at_utc
    running_backup = application.backups.create_backup()
    clock.now += timedelta(seconds=2)
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / running_backup.filename))
    connection = sqlite3.connect(application._config.database.path)
    try:
        running_row = connection.execute(
            "SELECT state, elapsed_before_run_ms, run_started_at_utc, deadline_at_utc FROM timers WHERE id = ?", (running.id,)
        ).fetchone()
        open_running = connection.execute(
            "SELECT actual_duration_ms, ended_at_utc, outcome FROM timer_runs WHERE timer_id = ?", (running.id,)
        ).fetchone()
    finally:
        connection.close()
    assert running_row[0:2] == ("RUNNING", 0)
    assert open_running[0] == 0 and open_running[1:] == (None, None)
    restored_running = application.timers.get_timer(running.id)
    assert (restored_running.state, restored_running.run_started_at_utc, restored_running.deadline_at_utc) == ("RUNNING", running_start, running_deadline)
    assert restored_running.effective_elapsed_ms == 2_000 and restored_running.remaining_ms == 8_000
    assert application.timers.list_timer_runs(running.id) == ()

    clock.now = running_start
    application.timers.reset_timer(running.id, restored_running.version)
    paused = application.timers.create_timer(CreateTimer(name="paused", planned_duration_ms=10_000))
    paused = application.timers.start_timer(paused.id, paused.version)
    clock.now += timedelta(seconds=2)
    paused = application.timers.pause_timer(paused.id, paused.version)
    connection = sqlite3.connect(application._config.database.path)
    try:
        paused_open_run_id = connection.execute(
            "SELECT id FROM timer_runs WHERE timer_id = ? AND ended_at_utc IS NULL",
            (paused.id,),
        ).fetchone()[0]
    finally:
        connection.close()
    paused_backup = application.backups.create_backup()
    frozen_elapsed = paused.elapsed_before_run_ms
    clock.now += timedelta(seconds=20)
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / paused_backup.filename))
    connection = sqlite3.connect(application._config.database.path)
    try:
        paused_row = connection.execute(
            "SELECT state, elapsed_before_run_ms, run_started_at_utc, deadline_at_utc FROM timers WHERE id = ?", (paused.id,)
        ).fetchone()
        open_paused = connection.execute(
            "SELECT id, actual_duration_ms, ended_at_utc, outcome FROM timer_runs WHERE timer_id = ?", (paused.id,)
        ).fetchone()
    finally:
        connection.close()
    assert paused_row[0:2] == ("PAUSED", frozen_elapsed)
    assert paused_row[2:] == (None, None)
    assert open_paused == (paused_open_run_id, frozen_elapsed, None, None)
    restored_paused = application.timers.get_timer(paused.id)
    assert restored_paused.state == "PAUSED"
    assert restored_paused.effective_elapsed_ms == frozen_elapsed
    assert restored_paused.run_started_at_utc is None and restored_paused.deadline_at_utc is None

    application.timers.reset_timer(paused.id, restored_paused.version)
    expired = application.timers.create_timer(CreateTimer(name="expired", planned_duration_ms=1))
    expired = application.timers.start_timer(expired.id, expired.version)
    expired_deadline = expired.deadline_at_utc
    expired_backup = application.backups.create_backup()
    clock.now += timedelta(seconds=1)
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / expired_backup.filename))
    restored_expired = application.timers.get_timer(expired.id)
    assert restored_expired.state == "FINISHED"
    assert restored_expired.elapsed_before_run_ms == 1 and restored_expired.ended_at_utc == expired_deadline
    run = application.timers.list_timer_runs(expired.id)[0]
    assert (run.outcome, run.actual_duration_ms, run.ended_at_utc) == ("FINISHED", 1, expired_deadline)


def test_restore_expired_timer_reconciles_once_and_reopened_reminder_emits_nothing(application, clock, monkeypatch) -> None:
    expired = application.timers.create_timer(CreateTimer(name="expiring restore", planned_duration_ms=1))
    expired = application.timers.start_timer(expired.id, expired.version)
    reminder = application.reminders.create_reminder(
        CreateReminder(title="quiet restore", due_at_utc=clock.now + timedelta(milliseconds=1), scheduled_timezone="UTC")
    )
    backup = application.backups.create_backup()
    deadline = expired.deadline_at_utc
    clock.now += timedelta(seconds=1)
    published: list[object] = []
    original_publish = InProcessEventBus.publish

    def capture_publish(self, event):  # type: ignore[no-untyped-def]
        published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(InProcessEventBus, "publish", capture_publish)
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    completed = [event for event in published if event.event_type == "TIMER_COMPLETED"]
    assert len(completed) == 1 and completed[0].entity_id == expired.id
    assert application.timers.reconcile_expired_timers() == ()
    assert len([event for event in published if event.event_type == "TIMER_COMPLETED"]) == 1
    restored_timer = application.timers.get_timer(expired.id)
    restored_reminder = application.reminders.get_reminder(reminder.id)
    assert (restored_timer.state, restored_timer.ended_at_utc) == ("FINISHED", deadline)
    assert restored_reminder.timing_state == "OVERDUE"
    assert not [event for event in published if getattr(event, "entity_id", None) == reminder.id]


def test_reminder_restore_derives_overdue_without_mutation(application, clock) -> None:
    reminder = application.reminders.create_reminder(CreateReminder(title="restore reminder", due_at_utc=clock.now + timedelta(seconds=1), scheduled_timezone="UTC"))
    original_events = application.reminders.list_reminder_events(reminder.id, ReminderPageQuery())
    backup = application.backups.create_backup()
    clock.now += timedelta(seconds=2)
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    restored = application.reminders.get_reminder(reminder.id)
    assert restored.status == "PENDING" and restored.timing_state == "OVERDUE" and restored.version == reminder.version
    assert application.reminders.list_reminder_events(reminder.id, ReminderPageQuery()) == original_events


def test_representative_restore_restores_relationships_history_and_removes_later_data(application, clock) -> None:
    note = application.notes.create_note(CreateNote(title="Note", content="body", color="amber"))
    task = application.tasks.create_task(CreateTask(title="Task", due_has_time=False))
    reminder = application.reminders.create_reminder(CreateReminder(title="Reminder", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC", task_id=task.id))
    preset = application.timers.create_preset(CreateTimerPreset(name="Preset", duration_ms=1_000))
    timer = application.timers.create_timer(CreateTimer(name="Timer", preset_id=preset.id, task_id=task.id))
    timer = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=5)
    application.timers.complete_timer(timer.id, timer.version)
    backup = application.backups.create_backup()
    application.notes.update_note(note.id, UpdateNote(expected_version=note.version, title="Changed"))
    later = application.tasks.create_task(CreateTask(title="Later"))
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    restored_note = application.notes.get_note(note.id)
    restored_task = application.tasks.get_task(task.id)
    assert (restored_note.title, restored_note.color) == ("Note", "amber")
    assert restored_task.due_has_time is False
    assert application.reminders.get_reminder(reminder.id).task_id == task.id
    reminder_events = application.reminders.list_reminder_events(reminder.id, ReminderPageQuery())
    assert [(event.event_type, event.previous_status, event.new_status) for event in reminder_events] == [
        ("REMINDER_CREATED", None, "PENDING")
    ]
    assert application.timers.get_preset(preset.id).duration_ms == 1_000
    assert application.timers.get_timer(timer.id).task_id == task.id
    assert application.timers.list_timer_runs(timer.id)[0].outcome == "FINISHED_EARLY"
    with pytest.raises(NotFoundError):
        application.tasks.get_task(later.id)


def test_backup_concurrent_with_mutation_is_valid_and_nonpartial(application) -> None:
    barrier = Barrier(2)
    errors: list[BaseException] = []
    created: list[object] = []
    def backup() -> None:
        try:
            barrier.wait()
            created.append(application.backups.create_backup())
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=backup)
    thread.start()
    barrier.wait()
    task = application.tasks.create_task(CreateTask(title="Concurrent"))
    thread.join(timeout=10)
    assert not errors and not thread.is_alive()
    validated = application.backups.validate_backup(ValidateBackup(source_path=application.backups.backup_directory / created[0].filename))
    assert validated.integrity_status == "OK"
    assert application.tasks.get_task(task.id).title == "Concurrent"


def test_backup_concurrent_update_is_an_exact_before_or_after_snapshot(application) -> None:
    task = application.tasks.create_task(CreateTask(title="before"))
    barrier = Barrier(2)
    created: list[object] = []
    errors: list[BaseException] = []

    def make_backup() -> None:
        try:
            barrier.wait()
            created.append(application.backups.create_backup())
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=make_backup)
    worker.start()
    barrier.wait()
    application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="after"))
    worker.join(timeout=15)
    assert not errors and not worker.is_alive()
    copied = application.backups.backup_directory / created[0].filename
    connection = sqlite3.connect(copied)
    try:
        row = connection.execute("SELECT title, version FROM tasks WHERE id = ?", (task.id,)).fetchone()
    finally:
        connection.close()
    assert row in {("before", 1), ("after", 2)}
    assert application.tasks.get_task(task.id).title == "after"


def test_restore_rejects_held_read_uow_then_succeeds_after_release(application) -> None:
    task = application.tasks.create_task(CreateTask(title="held read"))
    backup = application.backups.create_backup()
    entered = Event()
    release = Event()
    errors: list[BaseException] = []

    def hold_read() -> None:
        try:
            with application.unit_of_work_factory() as uow:
                assert uow.tasks is not None
                assert uow.tasks.get(task.id) is not None
                entered.set()
                assert release.wait(10)
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=hold_read)
    worker.start()
    assert entered.wait(10)
    with pytest.raises(ApplicationLifecycleError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    release.set()
    worker.join(timeout=10)
    assert not errors and not worker.is_alive()
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert application.tasks.get_task(task.id).title == "held read"


def test_lifecycle_leases_and_transition_are_balanced() -> None:
    gate = ApplicationLifecycleGate()
    first, second = gate.operation(), gate.operation()
    with pytest.raises(ApplicationLifecycleError):
        gate.begin_transition()
    first.close()
    first.close()
    assert gate._operations == 1
    second.close()
    gate.begin_transition()
    with pytest.raises(ApplicationLifecycleError):
        gate.operation()
    gate.abort_transition()
    with gate.operation():
        pass
    assert gate._operations == 0


def test_lifecycle_gate_releases_after_exceptions_and_repeated_transitions() -> None:
    gate = ApplicationLifecycleGate()
    for _ in range(3):
        with pytest.raises(RuntimeError), gate.operation():
            raise RuntimeError("operation failed")
        assert gate._operations == 0
        gate.begin_transition()
        with pytest.raises(ApplicationLifecycleError):
            gate.operation()
        gate.abort_transition()
        with gate.operation():
            pass
        assert gate._operations == 0


@pytest.mark.parametrize("phase", ["wal", "shm", "permission", "replace"])
def test_post_disposal_pre_replacement_failures_recover_a_fresh_original_runtime(
    application, monkeypatch, phase: str
) -> None:
    original = application.tasks.create_task(CreateTask(title=f"original-{phase}"))
    backup = application.backups.create_backup()
    old_tasks = application.tasks
    live = application._config.database.path
    original_remove = bootstrap_module.Application._remove_sidecars

    if phase in {"wal", "shm", "permission"}:
        def fail_sidecar(path):  # type: ignore[no-untyped-def]
            assert path == live
            raise PermissionError(f"{phase} sidecar lock")
        monkeypatch.setattr(bootstrap_module.Application, "_remove_sidecars", staticmethod(fail_sidecar))
    else:
        original_replace = bootstrap_module.os.replace
        calls = 0
        def fail_live_replace(source, destination):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if destination == live and calls == 1:
                raise PermissionError("replacement locked")
            return original_replace(source, destination)
        monkeypatch.setattr(bootstrap_module.os, "replace", fail_live_replace)

    with pytest.raises(BackupRestoreError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert live.exists()
    with sqlite3.connect(live) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT title FROM tasks WHERE id = ?", (original.id,)).fetchone()[0] == original.title
    assert application.is_closed is False
    assert application.tasks.get_task(original.id).title == original.title
    fresh = application.tasks.create_task(CreateTask(title=f"fresh-{phase}"))
    assert application.tasks.get_task(fresh.id).title == f"fresh-{phase}"
    with pytest.raises(ApplicationLifecycleError):
        old_tasks.get_task(original.id)
    assert original_remove is not None


@pytest.mark.parametrize("failure", ["runtime", "validation", "settings", "reconcile"])
def test_post_replacement_bootstrap_failures_restore_exact_safety_state(
    application, monkeypatch, failure: str
) -> None:
    task = application.tasks.create_task(CreateTask(title="backup-replacement"))
    replacement_backup = application.backups.create_backup()
    changed = application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="safety-original"))
    old_tasks = application.tasks
    calls = 0

    if failure == "runtime":
        original_compose = bootstrap_module._compose_application
        def fail_once(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("runtime construction")
            return original_compose(*args, **kwargs)
        monkeypatch.setattr(bootstrap_module, "_compose_application", fail_once)
    elif failure == "validation":
        original_validate = bootstrap_module._validate_database_integrity
        def fail_once(engine):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("post-replacement validation")
            return original_validate(engine)
        monkeypatch.setattr(bootstrap_module, "_validate_database_integrity", fail_once)
    elif failure == "settings":
        from panelo.services.settings import SettingsService
        original_settings = SettingsService.get_settings
        def fail_once(self):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("settings initialization")
            return original_settings(self)
        monkeypatch.setattr(SettingsService, "get_settings", fail_once)
    else:
        from panelo.services.timers import TimerService
        original_reconcile = TimerService.reconcile_expired_timers
        def fail_once(self):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("timer reconciliation")
            return original_reconcile(self)
        monkeypatch.setattr(TimerService, "reconcile_expired_timers", fail_once)

    with pytest.raises(BackupRestoreError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / replacement_backup.filename))
    assert application.is_closed is False
    assert application.tasks.get_task(task.id).title == changed.title
    with sqlite3.connect(application._config.database.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT title FROM tasks WHERE id = ?", (task.id,)).fetchone()[0] == changed.title
    with pytest.raises(ApplicationLifecycleError):
        old_tasks.get_task(task.id)


def test_replacement_sidecars_cannot_contaminate_safety_recovery(application, monkeypatch) -> None:
    task = application.tasks.create_task(CreateTask(title="replacement"))
    backup = application.backups.create_backup()
    safety = application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="safety"))
    live = application._config.database.path
    compose = bootstrap_module._compose_application
    calls = 0

    def contaminate_then_fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            replacement = compose(*args, **kwargs)
            replacement.tasks.update_task(
                task.id,
                UpdateTask(expected_version=1, title="replacement-leak"),
            )
            replacement.engine.dispose()
            raise RuntimeError("late initialization failure")
        return compose(*args, **kwargs)

    monkeypatch.setattr(bootstrap_module, "_compose_application", contaminate_then_fail)
    with pytest.raises(BackupRestoreError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert application.tasks.get_task(task.id).title == safety.title
    with sqlite3.connect(live) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT title FROM tasks WHERE id = ?", (task.id,)).fetchone()[0] == safety.title


def test_recovery_failure_is_fatal_and_retains_both_failures(application, monkeypatch) -> None:
    backup = application.backups.create_backup()
    monkeypatch.setattr(bootstrap_module, "_compose_application", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bootstrap failure")))
    with pytest.raises(BackupRecoveryError) as raised:
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert "bootstrap failure" in str(raised.value.restore_failure)
    assert "bootstrap failure" in str(raised.value.recovery_failure)
    assert raised.value.__cause__ is raised.value.recovery_failure
    assert application.is_closed is True
    with pytest.raises(ApplicationLifecycleError):
        application.tasks.list_tasks()


def test_runtime_bundle_swap_is_atomic_and_stales_all_prior_services(application) -> None:
    task = application.tasks.create_task(CreateTask(title="Bundle"))
    backup = application.backups.create_backup()
    old_runtime = application._active_runtime()
    old = (application.tasks, application.search, application.backups, application.unit_of_work_factory)
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    new_runtime = application._active_runtime()
    assert old_runtime is not new_runtime
    assert (new_runtime.tasks, new_runtime.search, new_runtime.backups, new_runtime.unit_of_work_factory) == (
        application.tasks, application.search, application.backups, application.unit_of_work_factory
    )
    for stale, action in (
        (old[0], lambda value: value.get_task(task.id)),
        (old[1], lambda value: value.search(UnifiedSearchQuery(query="Bundle"))),
        (old[2], lambda value: value.list_backups()),
    ):
        with pytest.raises(ApplicationLifecycleError):
            action(stale)
    with pytest.raises(ApplicationLifecycleError):
        old[3]().__enter__()
    assert application.tasks.get_task(task.id).title == "Bundle"
    assert application.search.search(UnifiedSearchQuery(query="Bundle"))
    assert application.backups.create_backup().integrity_status == "OK"


def test_threaded_runtime_bundle_observations_are_complete_generations(application, monkeypatch) -> None:
    task = application.tasks.create_task(CreateTask(title="Threaded"))
    backup = application.backups.create_backup()
    old_runtime = application._active_runtime()
    installed = Event()
    original_install = bootstrap_module.Application._install
    def install(self, runtime):  # type: ignore[no-untyped-def]
        original_install(self, runtime)
        installed.set()
    monkeypatch.setattr(bootstrap_module.Application, "_install", install)
    observations: list[tuple[int, tuple[int, int, int, int, int, int]]] = []
    failures: list[BaseException] = []
    start = Barrier(3)
    def reader() -> None:
        try:
            start.wait()
            for _ in range(50):
                runtime = application._active_runtime()
                observations.append((id(runtime), (id(runtime.tasks), id(runtime.search), id(runtime.backups), id(runtime.unit_of_work_factory), id(runtime.reminders), id(runtime.timers))))
        except BaseException as exc:
            failures.append(exc)
    threads = [Thread(target=reader) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    for thread in threads:
        thread.join(timeout=10)
    assert installed.is_set() and not failures and all(not thread.is_alive() for thread in threads)
    new_runtime = application._active_runtime()
    expected = {
        id(old_runtime): tuple(id(value) for value in (old_runtime.tasks, old_runtime.search, old_runtime.backups, old_runtime.unit_of_work_factory, old_runtime.reminders, old_runtime.timers)),
        id(new_runtime): tuple(id(value) for value in (new_runtime.tasks, new_runtime.search, new_runtime.backups, new_runtime.unit_of_work_factory, new_runtime.reminders, new_runtime.timers)),
    }
    assert observations and all(expected.get(runtime_id) == bundle for runtime_id, bundle in observations)
    assert application.tasks.get_task(task.id).title == "Threaded"


def test_repeated_runtime_swaps_publish_one_complete_new_bundle_each_time(application) -> None:
    task = application.tasks.create_task(CreateTask(title="Cycles"))
    backup = application.backups.create_backup()
    seen = [application._active_runtime()]
    for _ in range(3):
        old = application.tasks
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
        seen.append(application._active_runtime())
        with pytest.raises(ApplicationLifecycleError):
            old.get_task(task.id)
        assert application.tasks.get_task(task.id).title == "Cycles"
    assert len({id(runtime) for runtime in seen}) == 4


def test_backup_collisions_preserve_existing_bytes_and_retry(application, monkeypatch) -> None:
    backup_dir = application.backups.backup_directory
    application.backups._ensure_managed_directory(create=True)
    first_name = "panelo-backup-20260924T120000.000000Z-000000000000-manual.sqlite3"
    sentinel = backup_dir / first_name
    sentinel.write_bytes(b"sentinel-bytes")
    names = iter((first_name, "panelo-backup-20260924T120000.000000Z-111111111111-manual.sqlite3"))
    monkeypatch.setattr(application.backups, "_filename", lambda _now, _kind: next(names))
    created = application.backups.create_backup()
    assert sentinel.read_bytes() == b"sentinel-bytes"
    assert created.filename != first_name
    assert application.backups.validate_backup(ValidateBackup(source_path=backup_dir / created.filename)).integrity_status == "OK"


def test_publication_race_and_failure_never_overwrite_or_list_partial(application, monkeypatch) -> None:
    real_link = os.link
    calls = 0
    sentinel: list[object] = []
    def race_link(source, destination, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            destination.write_bytes(b"publication sentinel")
            sentinel.append(destination)
            raise FileExistsError("external winner")
        return real_link(source, destination, *args, **kwargs)
    monkeypatch.setattr("panelo.services.backups.os.link", race_link)
    created = application.backups.create_backup()
    assert sentinel and sentinel[0].read_bytes() == b"publication sentinel"
    assert created.filename != sentinel[0].name
    assert sentinel[0].name in {item.filename for item in application.backups.list_backups()}

    monkeypatch.setattr("panelo.services.backups.os.link", lambda *_a, **_k: (_ for _ in ()).throw(OSError("publication failure")))
    with pytest.raises(BackupError):
        application.backups.create_backup()
    assert not list(application.backups.backup_directory.glob("*.partial"))


@pytest.mark.parametrize("contents", [b"", b"not sqlite", b"SQLite format 3\x00truncated"])
def test_backup_validation_rejects_invalid_files_without_touching_live_database(application, tmp_path, contents: bytes) -> None:
    source = tmp_path / "invalid.sqlite3"
    source.write_bytes(contents)
    before = application.tasks.create_task(CreateTask(title="live validation guard"))
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=source))
    assert application.tasks.get_task(before.id).title == before.title


def test_backup_of_running_timer_preserves_anchors_and_open_run(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="backup running", planned_duration_ms=10_000))
    timer = application.timers.start_timer(timer.id, timer.version)
    backup = application.backups.create_backup()
    with sqlite3.connect(application.backups.backup_directory / backup.filename) as connection:
        row = connection.execute("SELECT state, run_started_at_utc, deadline_at_utc FROM timers WHERE id = ?", (timer.id,)).fetchone()
        run = connection.execute("SELECT ended_at_utc, outcome FROM timer_runs WHERE timer_id = ?", (timer.id,)).fetchone()
    assert row == ("RUNNING", int(timer.run_started_at_utc.timestamp() * 1_000_000), int(timer.deadline_at_utc.timestamp() * 1_000_000))
    assert run == (None, None)
    assert application.timers.get_timer(timer.id).state == "RUNNING"


def test_frozen_domain_events_keep_identity_order_and_subscription_contract(application, clock) -> None:
    observed = []
    callback = observed.append
    task_subscription = application.subscribe(DomainEventType.TASK_CREATED, callback)
    duplicate_task_subscription = application.subscribe(DomainEventType.TASK_CREATED, callback)
    note_subscription = application.subscribe(DomainEventType.NOTE_CREATED, callback)
    reminder_subscription = application.subscribe(DomainEventType.REMINDER_CREATED, callback)
    timer_subscription = application.subscribe(DomainEventType.TIMER_CREATED, callback)
    preset_subscription = application.subscribe(DomainEventType.TIMER_PRESET_CREATED, callback)
    task = application.tasks.create_task(CreateTask(title="event task"))
    note = application.notes.create_note(CreateNote(title="event note", content="x"))
    reminder = application.reminders.create_reminder(CreateReminder(title="event reminder", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC"))
    timer = application.timers.create_timer(CreateTimer(name="event timer", planned_duration_ms=1_000))
    preset = application.timers.create_preset(CreateTimerPreset(name="event preset", duration_ms=1_000))
    assert [(event.event_type, event.entity_id) for event in observed] == [
        ("TASK_CREATED", task.id), ("TASK_CREATED", task.id), ("NOTE_CREATED", note.id),
        ("REMINDER_CREATED", reminder.id), ("TIMER_CREATED", timer.id), ("TIMER_PRESET_CREATED", preset.id),
    ]
    assert observed[0].event_id == f"task:task_created:{task.id}:1"
    assert observed[2].event_id == f"note:NOTE_CREATED:{note.id}:1"
    assert observed[0].occurred_at_utc == clock.now
    task_subscription.close()
    task_subscription.close()
    duplicate_task_subscription.close()
    note_subscription.close()
    reminder_subscription.close()
    timer_subscription.close()
    preset_subscription.close()
    application.tasks.create_task(CreateTask(title="closed subscription"))
    assert len(observed) == 6


def _copied_valid_backup(application, tmp_path, name: str):
    """Return an independently mutable, fully valid application backup."""
    backup = application.backups.create_backup()
    copy = tmp_path / name
    shutil.copyfile(application.backups.backup_directory / backup.filename, copy)
    return copy


@pytest.mark.parametrize(
    ("mutation", "exception_type"),
    [
        ("drop_alembic", BackupValidationError),
        ("empty_alembic", BackupValidationError),
        ("future_revision", UnsupportedBackupVersionError),
        ("drop_required_table", BackupValidationError),
        ("add_unexpected_table", BackupValidationError),
        ("drop_required_column", BackupValidationError),
        ("missing_settings", BackupValidationError),
    ],
)
def test_backup_validation_rejects_schema_and_revision_variants_without_live_mutation(
    application, tmp_path, mutation: str, exception_type: type[Exception]
) -> None:
    live = application.tasks.create_task(CreateTask(title="validation-live"))
    source = _copied_valid_backup(application, tmp_path, f"{mutation}.sqlite3")
    with sqlite3.connect(source) as connection:
        if mutation == "drop_alembic":
            connection.execute("DROP TABLE alembic_version")
        elif mutation == "empty_alembic":
            connection.execute("DELETE FROM alembic_version")
        elif mutation == "future_revision":
            connection.execute("UPDATE alembic_version SET version_num = '20991231_9999'")
        elif mutation == "drop_required_table":
            connection.execute("DROP TABLE timer_runs")
        elif mutation == "add_unexpected_table":
            connection.execute("CREATE TABLE unexpected_phase5_table (id INTEGER)")
        elif mutation == "drop_required_column":
            connection.execute("ALTER TABLE tasks RENAME TO old_tasks")
            connection.execute("CREATE TABLE tasks AS SELECT id, title FROM old_tasks")
            connection.execute("DROP TABLE old_tasks")
        else:
            connection.execute("DELETE FROM app_settings WHERE id = 1")
    with pytest.raises(exception_type):
        application.backups.validate_backup(ValidateBackup(source_path=source))
    assert application.tasks.get_task(live.id).title == "validation-live"


def test_backup_validation_rejects_foreign_key_violation_and_unrelated_database(application, tmp_path) -> None:
    task = application.tasks.create_task(CreateTask(title="relationship guard"))
    source = _copied_valid_backup(application, tmp_path, "bad-foreign-key.sqlite3")
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO reminder_events (id, reminder_id, event_type, occurred_at_utc) "
            "VALUES ('orphan-event', 'missing-reminder', 'X', 1)"
        )
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=source))

    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as connection:
        connection.execute("CREATE TABLE ordinary (id INTEGER PRIMARY KEY)")
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=unrelated))
    assert application.tasks.get_task(task.id).title == "relationship guard"


def test_backup_validation_accepts_current_revision_without_migration(application) -> None:
    backup = application.backups.create_backup()
    validated = application.backups.validate_backup(
        ValidateBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    assert validated.schema_revision == "20261103_0003"


def test_backup_snapshots_never_contain_half_multi_row_transaction(application) -> None:
    """Native SQLite backup may see either commit boundary, never a half commit."""
    entered = Event()
    release = Event()
    created: list[object] = []
    failures: list[BaseException] = []

    def writer() -> None:
        try:
            with application.unit_of_work_factory() as uow:
                uow.acquire_write_lock()
                assert uow.session is not None
                for suffix in ("a", "b"):
                    uow.session.execute(text(
                        "INSERT INTO tasks (id, title, description, status, priority, start_at_utc, due_at_utc, completed_at_utc, cancelled_at_utc, created_at_utc, updated_at_utc, version, deleted_at_utc) "
                        f"VALUES ('txn-task-{suffix}', 'txn task', '', 'TODO', 'NORMAL', NULL, NULL, NULL, NULL, 1, 1, 1, NULL)"
                    ))
                entered.set()
                assert release.wait(10)
                uow.commit()
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=writer)
    thread.start()
    assert entered.wait(10)
    # A backup during the held transaction must not observe partially inserted rows.
    backup_thread = Thread(target=lambda: created.append(application.backups.create_backup()))
    backup_thread.start()
    release.set()
    thread.join(timeout=10)
    backup_thread.join(timeout=10)
    assert not failures and not thread.is_alive() and not backup_thread.is_alive()
    artifact = application.backups.backup_directory / created[0].filename
    application.backups.validate_backup(ValidateBackup(source_path=artifact))
    with sqlite3.connect(artifact) as connection:
        task_count = connection.execute("SELECT count(*) FROM tasks WHERE id LIKE 'txn-task-%'").fetchone()[0]
        assert task_count in {0, 2}


def test_restore_rejects_while_a_real_uow_is_held_then_succeeds(application) -> None:
    task = application.tasks.create_task(CreateTask(title="restore-uow"))
    backup = application.backups.create_backup()
    entered = Event()
    release = Event()
    failures: list[BaseException] = []

    def hold_uow() -> None:
        try:
            with application.unit_of_work_factory():
                entered.set()
                assert release.wait(10)
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=hold_uow)
    thread.start()
    assert entered.wait(10)
    with pytest.raises(ApplicationLifecycleError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    release.set()
    thread.join(timeout=10)
    assert not failures and not thread.is_alive()
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))
    assert application.tasks.get_task(task.id).title == "restore-uow"


def test_concurrent_restore_attempts_have_one_complete_winner(application) -> None:
    first = application.tasks.create_task(CreateTask(title="first backup"))
    backup_one = application.backups.create_backup()
    second = application.tasks.update_task(first.id, UpdateTask(expected_version=first.version, title="second backup"))
    backup_two = application.backups.create_backup()
    barrier = Barrier(3)
    results: list[str] = []
    failures: list[BaseException] = []

    def restore(path) -> None:  # type: ignore[no-untyped-def]
        try:
            barrier.wait()
            application.restore_backup(RestoreBackup(source_path=path))
            results.append(path.name)
        except BaseException as exc:
            failures.append(exc)

    threads = [
        Thread(target=restore, args=(application.backups.backup_directory / backup_one.filename,)),
        Thread(target=restore, args=(application.backups.backup_directory / backup_two.filename,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1 and len(failures) == 1 and isinstance(failures[0], ApplicationLifecycleError)
    assert application.tasks.get_task(first.id).title in {"first backup", second.title}
    with sqlite3.connect(application._config.database.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_subscriber_failure_is_post_commit_and_events_are_not_emitted_for_rollback(application, monkeypatch) -> None:
    received = []
    application.subscribe(DomainEventType.TASK_CREATED, lambda event: (_ for _ in ()).throw(RuntimeError("subscriber")))
    observed = application.subscribe(DomainEventType.TASK_CREATED, received.append)
    created = application.tasks.create_task(CreateTask(title="durable despite subscriber"))
    assert application.tasks.get_task(created.id).title == "durable despite subscriber"
    assert [event.entity_id for event in received] == [created.id]

    from panelo.db.unit_of_work import SqlAlchemyUnitOfWork

    original_commit = SqlAlchemyUnitOfWork.commit
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("commit failure")))
    with pytest.raises(RuntimeError, match="commit failure"):
        application.tasks.create_task(CreateTask(title="rolled back event"))
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", original_commit)
    assert [event.entity_id for event in received] == [created.id]
    observed.close()


def test_backup_vs_restore_rejects_transition_until_backup_lease_releases(application, monkeypatch) -> None:
    baseline = application.tasks.create_task(CreateTask(title="backup-before-restore"))
    restore_source = application.backups.create_backup()
    entered = Event()
    release = Event()
    original = application.backups._create_backup

    def blocked(kind: str):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(10)
        return original(kind)

    monkeypatch.setattr(application.backups, "_create_backup", blocked)
    outcomes: list[object] = []
    thread = Thread(target=lambda: outcomes.append(application.backups.create_backup()))
    thread.start()
    assert entered.wait(10)
    with pytest.raises(ApplicationLifecycleError):
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / restore_source.filename)
        )
    release.set()
    thread.join(timeout=15)
    assert not thread.is_alive() and len(outcomes) == 1
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / restore_source.filename)
    )
    assert application.tasks.get_task(baseline.id).title == baseline.title


def test_restore_vs_backup_rejects_new_backup_during_transition(application, monkeypatch) -> None:
    task = application.tasks.create_task(CreateTask(title="restore-before-backup"))
    source = application.backups.create_backup()
    entered = Event()
    release = Event()
    original = bootstrap_module.Application._remove_sidecars

    def blocked(path):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(10)
        return original(path)

    monkeypatch.setattr(bootstrap_module.Application, "_remove_sidecars", staticmethod(blocked))
    failures: list[BaseException] = []

    def restore() -> None:
        try:
            application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / source.filename))
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=restore)
    thread.start()
    assert entered.wait(10)
    with pytest.raises(ApplicationLifecycleError):
        application.backups.create_backup()
    release.set()
    thread.join(timeout=20)
    assert not thread.is_alive() and not failures
    assert application.tasks.get_task(task.id).title == task.title


def test_service_write_held_in_a_uow_blocks_restore_without_partial_commit(application) -> None:
    task = application.tasks.create_task(CreateTask(title="before write gate"))
    source = application.backups.create_backup()
    entered = Event()
    release = Event()
    failures: list[BaseException] = []

    def write() -> None:
        try:
            with application.unit_of_work_factory() as uow:
                uow.acquire_write_lock()
                assert uow.session is not None
                uow.session.execute(text("UPDATE tasks SET title = 'committed before restore' WHERE id = :id"), {"id": task.id})
                entered.set()
                assert release.wait(10)
                uow.commit()
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=write)
    thread.start()
    assert entered.wait(10)
    with pytest.raises(ApplicationLifecycleError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / source.filename))
    release.set()
    thread.join(timeout=10)
    assert not failures and not thread.is_alive()
    assert application.tasks.get_task(task.id).title == "committed before restore"
    application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / source.filename))
    assert application.tasks.get_task(task.id).title == "before write gate"


def test_backup_concurrent_with_multi_column_note_commit_is_atomic(application) -> None:
    note = application.notes.create_note(CreateNote(title="before", content="before"))
    entered, release = Event(), Event()
    backups: list[object] = []
    errors: list[BaseException] = []

    def write_all_note_state() -> None:
        try:
            with application.unit_of_work_factory() as uow:
                assert uow.session is not None
                uow.acquire_write_lock()
                uow.session.execute(
                    text("UPDATE notes SET title = :title, content = :content, color = :color WHERE id = :id"),
                    {"title": "after", "content": "after", "color": "teal", "id": note.id},
                )
                entered.set()
                assert release.wait(10)
                uow.commit()
        except BaseException as exc:
            errors.append(exc)

    writer = Thread(target=write_all_note_state)
    writer.start()
    assert entered.wait(10)

    def backup() -> None:
        try:
            backups.append(application.backups.create_backup())
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=backup)
    thread.start()
    release.set()
    writer.join(timeout=15)
    thread.join(timeout=15)
    assert not errors and not writer.is_alive() and not thread.is_alive()
    artifact = application.backups.backup_directory / backups[0].filename
    application.backups.validate_backup(ValidateBackup(source_path=artifact))
    connection = sqlite3.connect(artifact)
    try:
        row = connection.execute(
            "SELECT title, content, color FROM notes WHERE id = ?", (note.id,)
        ).fetchone()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    assert row in {("before", "before", "none"), ("after", "after", "teal")}


def test_captured_task_repository_cannot_write_after_uow_exit(application, clock) -> None:
    """A repository's guarded session must reject a version claim after its lease ends."""
    task = application.tasks.create_task(CreateTask(title="before stale write"))
    with application.unit_of_work_factory() as uow:
        assert uow.tasks is not None
        stale_tasks = uow.tasks

    with pytest.raises(ApplicationLifecycleError):
        stale_tasks.claim_version(task.id, task.version, clock.now, deleted=False)

    fresh = application.tasks.get_task(task.id)
    assert (fresh.title, fresh.version) == ("before stale write", task.version)


def test_captured_task_repository_cannot_write_after_runtime_replacement(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="backup state"))
    backup = application.backups.create_backup()
    with application.unit_of_work_factory() as uow:
        assert uow.tasks is not None
        stale_tasks = uow.tasks

    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    with pytest.raises(ApplicationLifecycleError):
        stale_tasks.claim_version(task.id, task.version, clock.now, deleted=False)

    with sqlite3.connect(application._config.database.path) as connection:
        row = connection.execute("SELECT title, version FROM tasks WHERE id = ?", (task.id,)).fetchone()
    assert row == ("backup state", task.version)
    assert application.tasks.get_task(task.id).title == "backup state"


def test_valid_restore_source_swap_from_backup_a_to_backup_b_never_installs_b(
    application, monkeypatch, tmp_path
) -> None:
    task = application.tasks.create_task(CreateTask(title="state A"))
    backup_a = application.backups.create_backup()
    application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="state B"))
    backup_b = application.backups.create_backup()
    source = tmp_path / "approved-source.sqlite3"
    shutil.copyfile(application.backups.backup_directory / backup_a.filename, source)
    replacement = application.backups.backup_directory / backup_b.filename
    stage = application.backups.stage_approved_restore_source

    def swap_after_approval(original, approved, destination):  # type: ignore[no-untyped-def]
        assert original == source
        shutil.copyfile(replacement, source)
        return stage(original, approved, destination)

    monkeypatch.setattr(application.backups, "stage_approved_restore_source", swap_after_approval)
    with pytest.raises(BackupValidationError):
        application.restore_backup(RestoreBackup(source_path=source))

    # The current runtime contained B before the rejected restore.  A path swap
    # must neither install B as an unapproved source nor disturb this live state.
    assert application.tasks.get_task(task.id).title == "state B"
    with sqlite3.connect(application._config.database.path) as connection:
        assert connection.execute("SELECT title FROM tasks WHERE id = ?", (task.id,)).fetchone()[0] == "state B"


def test_concurrent_backup_creators_with_same_final_name_do_not_overwrite(application, monkeypatch) -> None:
    application.tasks.create_task(CreateTask(title="concurrent backup source"))
    filename = "panelo-backup-20260924T120000.000000Z-aaaaaaaaaaaa-manual.sqlite3"
    monkeypatch.setattr(application.backups, "_filename", lambda _now, _kind: filename)
    original_link = os.link
    publication_barrier = Barrier(2)
    link_lock = Lock()
    link_calls = 0

    def synchronized_link(source, destination, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal link_calls
        with link_lock:
            link_calls += 1
            wait_here = link_calls <= 2
        if wait_here:
            publication_barrier.wait(timeout=10)
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr("panelo.services.backups.os.link", synchronized_link)
    results: list[object] = []
    failures: list[BaseException] = []
    start = Barrier(3)

    def create() -> None:
        try:
            start.wait(timeout=10)
            results.append(application.backups.create_backup())
        except BaseException as exc:
            failures.append(exc)

    threads = [Thread(target=create), Thread(target=create)]
    for thread in threads:
        thread.start()
    start.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1 and len(failures) == 1 and isinstance(failures[0], BackupError)
    artifact = application.backups.backup_directory / filename
    assert artifact.is_file()
    validated = application.backups.validate_backup(ValidateBackup(source_path=artifact))
    assert validated.integrity_status == "OK"
    with sqlite3.connect(artifact) as connection:
        assert connection.execute("SELECT title FROM tasks").fetchone()[0] == "concurrent backup source"
    assert not list(application.backups.backup_directory.glob("*.partial"))


def test_uow_constructor_failure_closes_created_session_and_invalidates_wrapper(
    application, monkeypatch
) -> None:
    from panelo.db.unit_of_work import SqlAlchemyUnitOfWork

    captured_raw_sessions: list[object] = []
    closed_sessions: list[object] = []
    captured_wrappers: list[object] = []
    real_factory = application.session_factory

    def spy_factory():  # type: ignore[no-untyped-def]
        session = real_factory()
        original_close = session.close

        def close():  # type: ignore[no-untyped-def]
            closed_sessions.append(session)
            return original_close()

        session.close = close  # type: ignore[method-assign]
        captured_raw_sessions.append(session)
        return session

    class BrokenTaskRepository:
        def __init__(self, wrapped_session: object) -> None:
            captured_wrappers.append(wrapped_session)
            raise RuntimeError("task repository construction failed")

    monkeypatch.setattr(uow_module, "TaskRepository", BrokenTaskRepository)
    uow = SqlAlchemyUnitOfWork(spy_factory, application._event_bus, application._gate)
    with pytest.raises(RuntimeError, match="task repository construction failed"):
        uow.__enter__()

    assert len(captured_raw_sessions) == 1
    assert closed_sessions == captured_raw_sessions
    assert uow._raw_session is None and uow.session is None
    assert application._gate._operations == 0
    with pytest.raises(ApplicationLifecycleError):
        captured_wrappers[0].execute(text("SELECT 1"))  # type: ignore[union-attr]

    monkeypatch.undo()
    backup = application.backups.create_backup()
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    application.close()
    assert application.is_closed


@pytest.mark.parametrize("failed_suffix", ["-wal", "-shm"])
def test_sidecar_removal_exercises_distinct_wal_and_shm_unlink_paths(
    application, monkeypatch, failed_suffix: str
) -> None:
    """The two sidecar failures are injected at their distinct ``Path.unlink`` calls."""
    live = application._config.database.path.resolve()
    wal = live.with_name(f"{live.name}-wal")
    shm = live.with_name(f"{live.name}-shm")
    wal.write_bytes(b"test wal sidecar")
    shm.write_bytes(b"test shm sidecar")
    target = wal if failed_suffix == "-wal" else shm
    original_unlink = type(target).unlink
    attempted: list[object] = []

    def fail_one_path(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        attempted.append(self)
        if self == target:
            raise PermissionError(f"cannot remove {failed_suffix}")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "unlink", fail_one_path)
    with pytest.raises(BackupRestoreError) as raised:
        bootstrap_module.Application._remove_sidecars(live)

    assert isinstance(raised.value.__cause__, PermissionError)
    assert attempted[-1] == target
    assert target.exists()
    if failed_suffix == "-wal":
        assert shm.exists() and attempted == [wal]
    else:
        assert not wal.exists() and attempted == [wal, shm]
    original_unlink(target)
    if shm.exists():
        original_unlink(shm)


@pytest.mark.parametrize("failed_suffix", ["-wal", "-shm"])
def test_restore_recovers_after_distinct_wal_or_shm_unlink_failure(
    application, monkeypatch, failed_suffix: str
) -> None:
    """Drive restore through each exact sidecar unlink branch, then recover a usable runtime."""
    task = application.tasks.create_task(CreateTask(title=f"sidecar {failed_suffix}"))
    source = application.backups.create_backup()
    live = application._config.database.path.resolve()
    wal = live.with_name(f"{live.name}-wal")
    shm = live.with_name(f"{live.name}-shm")
    target = wal if failed_suffix == "-wal" else shm
    original_exists = type(live).exists
    original_regular = bootstrap_module.BackupService._is_regular_file
    original_unlink = type(live).unlink
    unlinked: list[object] = []

    def sidecars_exist(self):  # type: ignore[no-untyped-def]
        if self in {wal, shm}:
            return True
        return original_exists(self)

    def regular_sidecar(path):  # type: ignore[no-untyped-def]
        if path in {wal, shm}:
            return True
        return original_regular(path)

    def unlink_exact_path(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self in {wal, shm}:
            unlinked.append(self)
            if self == target:
                raise PermissionError(f"cannot unlink {failed_suffix}")
            return None
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(live), "exists", sidecars_exist)
    monkeypatch.setattr(bootstrap_module.BackupService, "_is_regular_file", staticmethod(regular_sidecar))
    monkeypatch.setattr(type(live), "unlink", unlink_exact_path)
    with pytest.raises(BackupRestoreError) as raised:
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / source.filename)
        )

    assert isinstance(raised.value.__cause__, BackupRestoreError)
    assert unlinked[-1] == target
    assert unlinked == ([target] if failed_suffix == "-wal" else [wal, shm])
    assert application.is_closed is False
    assert application.tasks.get_task(task.id).title == f"sidecar {failed_suffix}"


def test_restore_staging_failure_keeps_live_runtime_and_database_usable(application, monkeypatch) -> None:
    """A real restore must abort before safety/replace if controlled staging fails."""
    task = application.tasks.create_task(CreateTask(title="backup state"))
    source = application.backups.create_backup()
    changed = application.tasks.update_task(
        task.id, UpdateTask(expected_version=task.version, title="live state")
    )
    before = tuple(application.backups.list_backups())

    def fail_stage(*_args: object, **_kwargs: object) -> object:
        raise BackupError("injected staging write failure")

    monkeypatch.setattr(application.backups, "stage_approved_restore_source", fail_stage)
    with pytest.raises(BackupRestoreError) as raised:
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / source.filename)
        )
    assert isinstance(raised.value.__cause__, BackupError)
    assert application.is_closed is False
    assert application.tasks.get_task(task.id).model_dump() == changed.model_dump()
    assert tuple(application.backups.list_backups()) == before
    assert not list(application._config.database.path.parent.glob("*.restore.partial"))
    with application.unit_of_work_factory() as uow:
        assert uow.session is not None
        assert uow.session.execute(text("SELECT 1")).scalar_one() == 1
    created = application.tasks.create_task(CreateTask(title="still usable"))
    assert application.tasks.get_task(created.id).title == "still usable"


def test_search_ties_unicode_and_reminder_message_fallback_are_exact(application, clock) -> None:
    """Lock SQLite's native text behavior and the SQL ordering tie-breakers."""
    first = application.tasks.create_task(CreateTask(title="tie search"))
    second = application.tasks.create_task(CreateTask(title="tie search"))
    tied = application.search.search(UnifiedSearchQuery(query="tie search", entity_types=("TASK",)))
    assert [row.entity_id for row in tied] == sorted((first.id, second.id))
    assert {row.updated_at_utc for row in tied} == {clock.now}

    unicode_task = application.tasks.create_task(CreateTask(title="Café"))
    assert [row.entity_id for row in application.search.search(UnifiedSearchQuery(query="Café"))] == [unicode_task.id]
    # SQLite LIKE preserves native non-ASCII behavior: no application Unicode case folding.
    assert unicode_task.id not in {
        row.entity_id for row in application.search.search(UnifiedSearchQuery(query="CAFÉ"))
    }

    reminder = application.reminders.create_reminder(
        CreateReminder(
            title="other title",
            message="message-only needle",
            due_at_utc=clock.now + timedelta(minutes=1),
            scheduled_timezone="UTC",
        )
    )
    row = application.search.search(UnifiedSearchQuery(query="message-only needle"))[0]
    assert (row.entity_type, row.entity_id, row.matched_field) == ("REMINDER", reminder.id, "message")


def test_search_orders_same_tier_by_updated_at_desc_before_entity_id(application, clock) -> None:
    """A newer same-tier result must precede an older result even when its ID sorts later."""
    class FixedIds:
        def __init__(self, values: tuple[str, ...]) -> None:
            self._values = iter(values)

        def new(self) -> str:
            return next(self._values)

    original_ids = application.tasks._ids
    application.tasks._ids = FixedIds(
        (
            "00000000-0000-4000-8000-000000000001",
            "ffffffff-ffff-4fff-8fff-ffffffffffff",
        )
    )
    try:
        older = application.tasks.create_task(CreateTask(title="updated-tier"))
        clock.now += timedelta(microseconds=1)
        newer = application.tasks.create_task(CreateTask(title="updated-tier"))
    finally:
        application.tasks._ids = original_ids

    rows = application.search.search(
        UnifiedSearchQuery(query="updated-tier", entity_types=("TASK",))
    )
    assert [(row.entity_id, row.updated_at_utc) for row in rows] == [
        (newer.id, newer.updated_at_utc),
        (older.id, older.updated_at_utc),
    ]
    assert newer.id > older.id and newer.updated_at_utc > older.updated_at_utc


def test_search_ranks_reminder_message_fallback_after_every_title_tier(application, clock) -> None:
    """Message-only reminders rank after exact, prefix, and contains title/name results."""
    exact = application.tasks.create_task(CreateTask(title="rank needle"))
    prefix = application.tasks.create_task(CreateTask(title="rank needle prefix"))
    contains = application.tasks.create_task(CreateTask(title="x rank needle x"))
    reminder = application.reminders.create_reminder(
        CreateReminder(
            title="unrelated reminder title",
            message="rank needle",
            due_at_utc=clock.now + timedelta(minutes=1),
            scheduled_timezone="UTC",
        )
    )

    rows = application.search.search(UnifiedSearchQuery(query="rank needle"))
    selected = [
        (row.entity_type, row.entity_id, row.matched_field)
        for row in rows
        if row.entity_id in {exact.id, prefix.id, contains.id, reminder.id}
    ]
    assert selected == [
        ("TASK", exact.id, "title"),
        ("TASK", prefix.id, "title"),
        ("TASK", contains.id, "title"),
        ("REMINDER", reminder.id, "message"),
    ]


def test_backup_validation_rejects_non_ok_integrity_and_older_revision(application, monkeypatch, tmp_path) -> None:
    """Exercise the direct integrity branch and the unsupported older-revision branch."""
    artifact = application.backups.create_backup()
    source = application.backups.backup_directory / artifact.filename
    import panelo.services.backups as backups_module

    original_connect = backups_module.sqlite3.connect

    class IntegrityProxy:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if statement == "PRAGMA integrity_check":
                return [("not ok",)]
            return self._connection.execute(statement, *args, **kwargs)

        def close(self) -> None:
            self._connection.close()

    monkeypatch.setattr(backups_module.sqlite3, "connect", lambda *args, **kwargs: IntegrityProxy(original_connect(*args, **kwargs)))
    with pytest.raises(BackupValidationError, match="integrity_check"):
        application.backups.validate_backup(ValidateBackup(source_path=source))
    monkeypatch.undo()

    older = tmp_path / "older.sqlite3"
    shutil.copyfile(source, older)
    connection = sqlite3.connect(older)
    try:
        connection.execute("UPDATE alembic_version SET version_num = ?", ("20250101_0000",))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(UnsupportedBackupVersionError):
        application.backups.validate_backup(ValidateBackup(source_path=older))


def test_restore_expired_timer_increments_once_and_second_startup_is_idempotent(application, clock, monkeypatch) -> None:
    timer = application.timers.create_timer(CreateTimer(name="once only", planned_duration_ms=1))
    running = application.timers.start_timer(timer.id, timer.version)
    backup = application.backups.create_backup()
    clock.now += timedelta(seconds=1)
    published: list[object] = []
    original_publish = InProcessEventBus.publish

    def capture_publish(self, event):  # type: ignore[no-untyped-def]
        published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(InProcessEventBus, "publish", capture_publish)
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    first = application.timers.get_timer(timer.id)
    assert first.version == running.version + 1
    assert first.state == "FINISHED" and first.ended_at_utc == running.deadline_at_utc
    connection = sqlite3.connect(application._config.database.path)
    try:
        before_restart_run = connection.execute(
            "SELECT id, outcome, ended_at_utc FROM timer_runs WHERE timer_id = ?", (timer.id,)
        ).fetchone()
    finally:
        connection.close()
    assert before_restart_run[1] == "FINISHED"
    assert before_restart_run[2] is not None
    assert len([event for event in published if event.event_type == "TIMER_COMPLETED"]) == 1

    published.clear()
    application.close()
    reopened = bootstrap_module.create_application(application._config, clock)
    try:
        after_restart = reopened.timers.get_timer(timer.id)
        assert after_restart.model_dump() == first.model_dump()
        connection = sqlite3.connect(reopened._config.database.path)
        try:
            assert connection.execute(
                "SELECT id, outcome, ended_at_utc FROM timer_runs WHERE timer_id = ?", (timer.id,)
            ).fetchone() == before_restart_run
        finally:
            connection.close()
        assert not [event for event in published if event.event_type == "TIMER_COMPLETED"]
    finally:
        reopened.close()


def test_repeated_restore_contention_cycles_leave_runtime_healthy(application, monkeypatch) -> None:
    """Repeated backup/restore contention never wedges the lifecycle gate or runtime."""
    seed = application.tasks.create_task(CreateTask(title="restore cycle"))

    for _cycle in range(2):
        source = application.backups.create_backup()
        source_path = application.backups.backup_directory / source.filename
        barrier = Barrier(3)
        successes: list[object] = []
        failures: list[BaseException] = []

        def restore_once(
            barrier: Barrier = barrier,
            successes: list[object] = successes,
            failures: list[BaseException] = failures,
            source_path=source_path,
        ) -> None:
            try:
                barrier.wait()
                successes.append(application.restore_backup(RestoreBackup(source_path=source_path)))
            except BaseException as exc:
                failures.append(exc)

        left, right = Thread(target=restore_once), Thread(target=restore_once)
        left.start()
        right.start()
        barrier.wait()
        left.join(timeout=20)
        right.join(timeout=20)
        assert not left.is_alive() and not right.is_alive()
        assert len(successes) == 1
        assert len(failures) == 1 and isinstance(failures[0], ApplicationLifecycleError)
        assert application.tasks.get_task(seed.id).title == "restore cycle"
        with application.unit_of_work_factory() as uow:
            assert uow.session is not None
            assert uow.session.execute(text("SELECT 1")).scalar_one() == 1

        copy_started, release_copy = Event(), Event()
        original_copy = application.backups._copy_sqlite

        def hold_copy(
            source_path_arg,
            destination_path_arg,
            copy_started: Event = copy_started,
            release_copy: Event = release_copy,
            original_copy=original_copy,
        ):  # type: ignore[no-untyped-def]
            copy_started.set()
            assert release_copy.wait(10)
            return original_copy(source_path_arg, destination_path_arg)

        with monkeypatch.context() as scoped:
            scoped.setattr(application.backups, "_copy_sqlite", hold_copy)
            backup_errors: list[BaseException] = []
            worker = Thread(
                target=lambda backup_errors=backup_errors: _record_backup_error(application, backup_errors)
            )
            worker.start()
            assert copy_started.wait(10)
            with pytest.raises(ApplicationLifecycleError):
                application.restore_backup(RestoreBackup(source_path=source_path))
            release_copy.set()
            worker.join(timeout=20)
            assert not worker.is_alive() and not backup_errors

        remove_started, release_remove = Event(), Event()
        original_remove = bootstrap_module.Application._remove_sidecars

        def hold_remove(
            live,
            remove_started: Event = remove_started,
            release_remove: Event = release_remove,
            original_remove=original_remove,
        ):  # type: ignore[no-untyped-def]
            remove_started.set()
            assert release_remove.wait(10)
            return original_remove(live)

        with monkeypatch.context() as scoped:
            scoped.setattr(bootstrap_module.Application, "_remove_sidecars", staticmethod(hold_remove))
            restore_errors: list[BaseException] = []
            worker = Thread(
                target=lambda source_path=source_path, restore_errors=restore_errors: _record_restore_error(
                    application, source_path, restore_errors
                )
            )
            worker.start()
            assert remove_started.wait(20)
            with pytest.raises(ApplicationLifecycleError):
                application.backups.create_backup()
            release_remove.set()
            worker.join(timeout=30)
            assert not worker.is_alive() and not restore_errors

        assert application.tasks.get_task(seed.id).title == "restore cycle"
        with application.unit_of_work_factory() as uow:
            assert uow.session is not None
            assert uow.session.execute(text("SELECT 1")).scalar_one() == 1


def _record_backup_error(application, errors: list[BaseException]) -> None:  # type: ignore[no-untyped-def]
    try:
        application.backups.create_backup()
    except BaseException as exc:
        errors.append(exc)


def _record_restore_error(application, source_path, errors: list[BaseException]) -> None:  # type: ignore[no-untyped-def]
    try:
        application.restore_backup(RestoreBackup(source_path=source_path))
    except BaseException as exc:
        errors.append(exc)
