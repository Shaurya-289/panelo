"""The Trash: listing, restoring, deleting forever, emptying and the 30-day purge."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from productivity_app.core.exceptions import NotFoundError
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.reminders import CreateReminder
from productivity_app.schemas.tasks import CreateTask
from productivity_app.schemas.timers import CreateTimer
from productivity_app.services.trash import TRASH_RETENTION_DAYS


def _trashed_note(application, title: str = "Note", content: str = "", clock=None, **extra):
    note = application.notes.create_note(CreateNote(title=title, content=content, **extra))
    return application.notes.delete_note(note.id, note.version)


def _trashed_task(application, title: str = "Task", **extra):
    task = application.tasks.create_task(CreateTask(title=title, **extra))
    return application.tasks.delete_task(task.id, task.version)


def _count(application, table: str) -> int:
    with application.engine.connect() as connection:
        return int(connection.scalar(text(f"SELECT count(*) FROM {table}")))


def test_the_retention_period_is_thirty_days() -> None:
    assert TRASH_RETENTION_DAYS == 30


def test_list_trash_shows_deleted_notes_and_tasks_newest_first(application, clock) -> None:
    assert application.trash.list_trash() == ()
    kept = application.notes.create_note(CreateNote(title="Not deleted"))
    first = _trashed_note(application, "Older note", "first line\nsecond line")
    clock.now += timedelta(hours=1)
    task = _trashed_task(application, "Newer task", description="\n  \n  details here  ")
    clock.now += timedelta(hours=1)
    untitled = _trashed_note(application, "", "  Body of an untitled note\nmore")

    items = application.trash.list_trash()

    assert [(item.kind, item.id) for item in items] == [
        ("NOTE", untitled.id),
        ("TASK", task.id),
        ("NOTE", first.id),
    ]
    assert kept.id not in {item.id for item in items}
    assert items[0].title == "" and items[0].preview == "Body of an untitled note"
    assert items[1].title == "Newer task" and items[1].preview == "details here"
    assert items[2].preview == "first line"
    assert items[0].deleted_at_utc == clock.now
    assert items[2].deleted_at_utc == clock.now - timedelta(hours=2)
    assert [item.version for item in items] == [untitled.version, task.version, first.version]


def test_trash_items_carry_the_version_that_restore_expects(application) -> None:
    note = _trashed_note(application, "Restore me")
    task = _trashed_task(application, "Restore me too")
    by_kind = {item.kind: item for item in application.trash.list_trash()}
    restored_note = application.notes.restore_note(by_kind["NOTE"].id, by_kind["NOTE"].version)
    restored_task = application.tasks.restore_task(by_kind["TASK"].id, by_kind["TASK"].version)
    assert restored_note.deleted_at_utc is None and restored_task.deleted_at_utc is None
    assert restored_note.id == note.id and restored_task.id == task.id
    assert application.trash.list_trash() == ()


def test_delete_forever_removes_the_row_and_its_search_documents(application) -> None:
    note = _trashed_note(application, "Gone note")
    task = _trashed_task(application, "Gone task")
    application.trash.delete_forever("NOTE", note.id)
    assert [item.id for item in application.trash.list_trash()] == [task.id]
    application.trash.delete_forever("TASK", task.id)
    assert application.trash.list_trash() == ()
    assert _count(application, "notes") == _count(application, "tasks") == 0
    assert _count(application, "search_documents") == 0
    with pytest.raises(NotFoundError):
        application.notes.get_note(note.id, include_deleted=True)


def test_only_items_in_the_trash_can_be_deleted_forever(application) -> None:
    live_note = application.notes.create_note(CreateNote(title="Alive"))
    live_task = application.tasks.create_task(CreateTask(title="Alive"))
    with pytest.raises(NotFoundError):
        application.trash.delete_forever("NOTE", live_note.id)
    with pytest.raises(NotFoundError):
        application.trash.delete_forever("TASK", live_task.id)
    with pytest.raises(NotFoundError):
        application.trash.delete_forever("NOTE", "missing")
    with pytest.raises(NotFoundError):  # a note id is not a task
        application.trash.delete_forever("TASK", live_note.id)
    assert application.notes.get_note(live_note.id).title == "Alive"
    assert application.tasks.get_task(live_task.id).title == "Alive"


def test_purging_a_task_unlinks_its_reminders_and_nulls_other_references(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Linked"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="R", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC", task_id=task.id)
    )
    unrelated = application.reminders.create_reminder(
        CreateReminder(title="Other", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC")
    )
    timer = application.timers.create_timer(CreateTimer(name="T", planned_duration_ms=5_000, task_id=task.id))
    deleted = application.tasks.delete_task(task.id, task.version)
    observed: list = []
    application.subscribe("REMINDER_UPDATED", observed.append)
    application.subscribe("TASK_PURGED", observed.append)
    clock.now += timedelta(seconds=1)

    application.trash.delete_forever("TASK", deleted.id)

    after = application.reminders.get_reminder(reminder.id)
    assert after.task_id is None and after.note_id is None
    assert after.version == reminder.version + 1 and after.updated_at_utc == clock.now
    assert application.reminders.get_reminder(unrelated.id).version == unrelated.version
    assert application.timers.get_timer(timer.id).task_id is None
    assert sorted((event.event_type, event.entity_id) for event in observed) == sorted(
        [("TASK_PURGED", task.id), ("REMINDER_UPDATED", reminder.id)]
    )


def test_purging_a_note_unlinks_its_reminders(application, clock) -> None:
    note = application.notes.create_note(CreateNote(title="Linked note"))
    reminder = application.reminders.create_reminder(
        CreateReminder(title="R", due_at_utc=clock.now, scheduled_timezone="UTC", note_id=note.id)
    )
    deleted = application.notes.delete_note(note.id, note.version)
    application.trash.delete_forever("NOTE", deleted.id)
    after = application.reminders.get_reminder(reminder.id)
    assert after.note_id is None and after.version == reminder.version + 1
    assert after.title == "R" and after.status == "PENDING"


def test_empty_trash_removes_everything_in_it_and_nothing_else(application) -> None:
    kept_note = application.notes.create_note(CreateNote(title="Kept"))
    kept_task = application.tasks.create_task(CreateTask(title="Kept"))
    _trashed_note(application, "a")
    _trashed_note(application, "b")
    _trashed_task(application, "c")
    observed: list = []
    application.subscribe("NOTE_PURGED", observed.append)
    application.subscribe("TASK_PURGED", observed.append)

    assert application.trash.empty_trash() == 3

    assert application.trash.list_trash() == ()
    assert [note.id for note in application.notes.list_notes()] == [kept_note.id]
    assert [task.id for task in application.tasks.list_tasks()] == [kept_task.id]
    assert sorted(event.event_type for event in observed) == ["NOTE_PURGED", "NOTE_PURGED", "TASK_PURGED"]
    assert application.trash.empty_trash() == 0


def test_purge_expired_deletes_only_items_older_than_the_retention_period(application, clock) -> None:
    start = clock.now
    ancient_note = _trashed_note(application, "ancient")
    clock.now = start + timedelta(days=10)
    middle_task = _trashed_task(application, "middle")
    clock.now = start + timedelta(days=20)
    recent_note = _trashed_note(application, "recent")
    live = application.tasks.create_task(CreateTask(title="live"))

    # 29 days 23 h 59 m 59 s after the first deletion: nothing is old enough
    now = start + timedelta(days=30) - timedelta(seconds=1)
    assert application.trash.purge_expired(now) == 0
    assert len(application.trash.list_trash()) == 3

    # exactly thirty days: the first item goes
    assert application.trash.purge_expired(start + timedelta(days=30)) == 1
    assert {item.id for item in application.trash.list_trash()} == {middle_task.id, recent_note.id}
    assert ancient_note.id not in {item.id for item in application.trash.list_trash()}

    # forty days after the start: the middle one (deleted on day 10) goes, the recent one stays
    assert application.trash.purge_expired(start + timedelta(days=40)) == 1
    assert [item.id for item in application.trash.list_trash()] == [recent_note.id]
    assert application.tasks.get_task(live.id).title == "live"
    assert application.trash.purge_expired(start + timedelta(days=40)) == 0


def test_purge_expired_uses_the_clock_by_default_and_a_custom_period(application, clock) -> None:
    _trashed_note(application, "old")
    clock.now += timedelta(days=31)
    recent = _trashed_task(application, "recent")
    assert application.trash.purge_expired() == 1
    assert [item.id for item in application.trash.list_trash()] == [recent.id]
    clock.now += timedelta(days=2)
    assert application.trash.purge_expired(days=3) == 0
    assert application.trash.purge_expired(days=1) == 1
    assert application.trash.list_trash() == ()
    with pytest.raises(ValueError):
        application.trash.purge_expired(days=-1)


def test_purging_publishes_events_only_after_the_commit(application, clock) -> None:
    note = _trashed_note(application, "Event")
    seen: list = []

    def check(event) -> None:
        # by the time subscribers run, the row is already gone
        seen.append((event.event_type, event.entity_id, _count(application, "notes")))

    application.subscribe("NOTE_PURGED", check)
    application.trash.delete_forever("NOTE", note.id)
    assert seen == [("NOTE_PURGED", note.id, 0)]
    assert application.trash.purge_expired(clock.now + timedelta(days=60)) == 0  # nothing left, no events
    assert len(seen) == 1


def test_a_failed_purge_changes_nothing(application, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    from productivity_app.repositories.tasks import TaskRepository

    note = _trashed_note(application, "Note")
    task = _trashed_task(application, "Task")
    monkeypatch.setattr(TaskRepository, "delete", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        application.trash.empty_trash()
    assert {item.id for item in application.trash.list_trash()} == {note.id, task.id}
    assert _count(application, "notes") == 1
