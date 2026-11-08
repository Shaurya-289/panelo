"""Notes, tasks, reminders and the Trash through the bridge: each QML concept maps onto the
backend as the plan says, and the lists the pages read follow domain events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtWidgets import QApplication

from panelo.bootstrap import Application
from panelo.schemas.notes import CreateNote, UpdateNote
from panelo.ui.bridge.backend import Backend
from tests.conftest import FakeClock
from tests.ui.conftest import listing, ms

NOON = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


# -- Notes ----------------------------------------------------------------------------------


def test_note_tint_is_color_and_body_is_content(backend: Backend, application: Application) -> None:
    note_id = backend.addNote("Plan", "line 1\nline 2", "plum")
    view = application.notes.get_note(note_id)
    assert (view.title, view.content, view.color) == ("Plan", "line 1\nline 2", "plum")
    assert listing(backend, "notes")[0] == {
        "id": note_id, "title": "Plan", "body": "line 1\nline 2", "tint": "plum", "pinned": False,
        "archived": False, "createdAt": ms(view.created_at_utc), "updatedAt": ms(view.updated_at_utc),
        "deletedAt": None,
    }
    backend.updateNote(note_id, {"body": "new", "tint": "teal", "pinned": True, "archived": True})
    after = application.notes.get_note(note_id)
    assert (after.content, after.color, after.is_pinned, after.is_archived) == ("new", "teal", True, True)
    assert listing(backend, "notes")[0]["tint"] == "teal"


def test_empty_note_is_not_created_and_unknown_tint_is_none(backend: Backend) -> None:
    assert backend.addNote("  ", "", "none") == ""
    note_id = backend.addNote("x", "", "chartreuse")
    assert listing(backend, "notes")[0]["id"] == note_id
    assert listing(backend, "notes")[0]["tint"] == "none"


def test_newest_edit_comes_first(backend: Backend, clock: FakeClock) -> None:
    first = backend.addNote("first", "", "none")
    clock.now += timedelta(minutes=1)
    second = backend.addNote("second", "", "none")
    assert [n["id"] for n in listing(backend, "notes")] == [second, first]
    clock.now += timedelta(minutes=1)
    backend.updateNote(first, {"title": "first, edited"})
    assert [n["id"] for n in listing(backend, "notes")] == [first, second]


def test_deleting_a_note_moves_it_to_the_trash(backend: Backend) -> None:
    note_id = backend.addNote("Gone soon", "text", "none")
    backend.deleteNote(note_id)
    assert listing(backend, "notes") == []
    trash = listing(backend, "trash")
    assert [(t["kind"], t["id"], t["title"]) for t in trash] == [("note", note_id, "Gone soon")]
    backend.restoreItem("note", note_id)
    assert [n["id"] for n in listing(backend, "notes")] == [note_id]
    assert listing(backend, "trash") == []


# -- Tasks ----------------------------------------------------------------------------------


def test_task_fields_map_to_the_backend(backend: Backend, application: Application) -> None:
    due = ms(NOON + timedelta(days=1))
    fields = {"due": due, "hasTime": True, "priority": "urgent", "description": "d"}
    task_id = backend.addTask("Write", fields)
    view = application.tasks.get_task(task_id)
    assert (view.priority, view.due_has_time, view.description) == ("URGENT", True, "d")
    assert ms(view.due_at_utc) == due
    row = listing(backend, "tasks")[0]
    assert (row["priority"], row["hasTime"], row["due"], row["done"]) == ("urgent", True, due, False)


def test_a_date_only_task_has_no_time(backend: Backend, application: Application) -> None:
    midnight = ms(datetime(2026, 11, 4, tzinfo=UTC))
    task_id = backend.addTask("Day", {"due": midnight, "hasTime": False})
    assert application.tasks.get_task(task_id).due_has_time is False
    assert listing(backend, "tasks")[0]["hasTime"] is False
    backend.updateTask(task_id, {"due": midnight + 3600000, "hasTime": True})
    assert listing(backend, "tasks")[0]["hasTime"] is True
    backend.updateTask(task_id, {"due": None, "hasTime": False})
    row = listing(backend, "tasks")[0]
    assert (row["due"], row["hasTime"]) == (None, False)


def test_done_is_completed_and_reopened(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    task_id = backend.addTask("Tick", {})
    clock.now += timedelta(hours=1)
    backend.toggleTask(task_id)
    assert application.tasks.get_task(task_id).status == "COMPLETED"
    row = listing(backend, "tasks")[0]
    assert row["done"] is True and row["doneAt"] == ms(clock.now)
    backend.toggleTask(task_id)
    assert application.tasks.get_task(task_id).status == "TODO"
    row = listing(backend, "tasks")[0]
    assert row["done"] is False and row["doneAt"] is None


def test_priority_title_and_description_updates(backend: Backend, application: Application) -> None:
    task_id = backend.addTask("Old", {})
    backend.updateTask(task_id, {"title": "  New  ", "description": "more", "priority": "low"})
    view = application.tasks.get_task(task_id)
    assert (view.title, view.description, view.priority) == ("New", "more", "LOW")
    assert backend.addTask("   ", {}) == ""


def test_old_completed_tasks_drop_out_of_the_list(backend: Backend, clock: FakeClock) -> None:
    recent = backend.addTask("recent", {})
    old = backend.addTask("old", {})
    clock.now -= timedelta(days=20)
    backend.toggleTask(old)
    clock.now += timedelta(days=20)
    backend.toggleTask(recent)
    assert {t["id"] for t in listing(backend, "tasks")} == {recent}


def test_deleted_task_is_in_the_trash_with_its_version(backend: Backend) -> None:
    task_id = backend.addTask("Bin me", {})
    backend.deleteTask(task_id)
    assert listing(backend, "tasks") == []
    assert listing(backend, "trash")[0]["id"] == task_id
    backend.restoreItem("task", task_id)
    assert listing(backend, "tasks")[0]["id"] == task_id


# -- Reminders ------------------------------------------------------------------------------


def test_reminder_link_repeat_and_past(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    task_id = backend.addTask("Linked", {})
    due = ms(clock.now + timedelta(days=1))
    fields = {"link": {"kind": "task", "id": task_id}, "repeat": {"kind": "weekly", "days": [1, 3, 5]}}
    reminder_id = backend.addReminder("Gym", due, fields)
    row = next(r for r in listing(backend, "reminders") if r["id"] == reminder_id)
    assert row["link"] == {"kind": "task", "id": task_id}
    assert row["repeat"] == {"kind": "weekly", "days": [1, 3, 5]}
    assert row["done"] is False and row["anchor"] == row["due"]
    once = backend.addReminder("Once", ms(clock.now + timedelta(hours=1)), {})
    backend.completeReminder(once)
    done = next(r for r in listing(backend, "reminders") if r["id"] == once)
    assert done["done"] is True and done["doneAt"] == ms(clock.now)  # Past = COMPLETED / DISMISSED
    assert application.reminders.get_reminder(once).status == "COMPLETED"
    assert application.reminders.get_reminder(reminder_id).status == "PENDING"


def test_done_on_a_repeating_reminder_advances_it(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    due = clock.now + timedelta(hours=2)
    rule = {"kind": "every", "n": 2, "unit": "hours"}
    reminder_id = backend.addReminder("Water", ms(due), {"repeat": rule})
    clock.now = due + timedelta(seconds=5)
    backend.completeReminder(reminder_id)
    row = next(r for r in listing(backend, "reminders") if r["id"] == reminder_id)
    assert row["done"] is False
    assert row["due"] == ms(due + timedelta(hours=2)) and row["timesDone"] == 1
    assert application.reminders.get_reminder(reminder_id).status == "PENDING"


def test_snooze_moves_due_not_the_anchor(backend: Backend, clock: FakeClock) -> None:
    due = clock.now + timedelta(hours=1)
    reminder_id = backend.addReminder("Daily", ms(due), {"repeat": {"kind": "daily"}})
    clock.now = due + timedelta(seconds=1)
    backend.snoozeReminder(reminder_id)
    row = listing(backend, "reminders")[0]
    assert row["due"] == ms(clock.now + timedelta(minutes=10))
    assert row["anchor"] == ms(due) and row["snoozeCount"] == 1


def test_setting_a_time_reopens_a_finished_reminder(backend: Backend, clock: FakeClock) -> None:
    reminder_id = backend.addReminder("Once", ms(clock.now + timedelta(hours=1)), {})
    backend.completeReminder(reminder_id)
    later = ms(clock.now + timedelta(days=1))
    backend.setReminderTime(reminder_id, later)
    row = listing(backend, "reminders")[0]
    assert row["done"] is False and row["due"] == later


def test_set_and_clear_a_repeat_rule(backend: Backend, clock: FakeClock) -> None:
    reminder_id = backend.addReminder("Rule", ms(clock.now + timedelta(days=1)), {})
    backend.setReminderRepeat(reminder_id, {"kind": "daily"})
    assert listing(backend, "reminders")[0]["repeat"] == {"kind": "daily"}
    backend.setReminderRepeat(reminder_id, None)
    assert listing(backend, "reminders")[0]["repeat"] is None


def test_update_title_message_and_delete_for_good(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    reminder_id = backend.addReminder("A", ms(clock.now + timedelta(hours=1)), {})
    backend.updateReminder(reminder_id, {"title": "B", "message": "m"})
    row = listing(backend, "reminders")[0]
    assert (row["title"], row["message"]) == ("B", "m")
    backend.updateReminder(reminder_id, {"title": "B", "message": "m"})  # nothing changed: fine
    backend.deleteReminder(reminder_id)
    assert listing(backend, "reminders") == []
    assert application.reminders.list_reminders() == ()  # a hard delete, no trash


def test_a_purged_task_unlinks_its_reminder(backend: Backend, clock: FakeClock) -> None:
    task_id = backend.addTask("T", {})
    link = {"link": {"kind": "task", "id": task_id}}
    backend.addReminder("R", ms(clock.now + timedelta(hours=1)), link)
    backend.deleteTask(task_id)
    backend.deleteForever("task", task_id)
    assert listing(backend, "reminders")[0]["link"] is None


# -- Trash ----------------------------------------------------------------------------------


def test_trash_list_forever_empty_and_purge(backend: Backend, clock: FakeClock) -> None:
    old = backend.addNote("old", "", "none")
    mid = backend.addTask("mid", {})
    new = backend.addNote("", "body line", "none")
    clock.now -= timedelta(days=31)
    backend.deleteNote(old)
    clock.now += timedelta(days=30)
    backend.deleteTask(mid)
    clock.now += timedelta(days=1)
    backend.deleteNote(new)
    titles = [t["title"] for t in listing(backend, "trash")]
    assert titles == ["body line", "mid", "old"]  # newest first; an untitled note shows its text
    backend.purgeTrash()
    assert [t["title"] for t in listing(backend, "trash")] == ["body line", "mid"]
    backend.deleteForever("task", mid)
    assert [t["title"] for t in listing(backend, "trash")] == ["body line"]
    backend.emptyTrash()
    assert listing(backend, "trash") == []


# -- Events and versions --------------------------------------------------------------------


def test_lists_follow_domain_events_from_outside(
    backend: Backend, application: Application, qapp: QApplication
) -> None:
    changes: list[str] = []
    backend.notesChanged.connect(lambda: changes.append("notes"))
    application.notes.create_note(CreateNote(title="From the service"))
    qapp.processEvents()  # the refresh is queued, then run
    assert [n["title"] for n in listing(backend, "notes")] == ["From the service"]
    assert changes == ["notes"]


def test_a_stale_version_is_reloaded_and_retried(backend: Backend, application: Application) -> None:
    note_id = backend.addNote("v1", "", "none")
    stale = backend._versions["note"][note_id]
    application.notes.update_note(note_id, UpdateNote(expected_version=stale, title="elsewhere"))
    backend.updateNote(note_id, {"body": "mine"})  # the cache still holds the old version
    view = application.notes.get_note(note_id)
    assert (view.title, view.content) == ("elsewhere", "mine")


def test_changes_to_things_that_are_gone_are_quiet(backend: Backend) -> None:
    notices: list[str] = []
    backend.notice.connect(notices.append)
    backend.snoozeReminder("no-such-id")
    backend.updateTask("missing", {"title": "x"})
    task_id = backend.addTask("T", {})
    backend.deleteTask(task_id)
    backend.toggleTask(task_id)  # a deleted task cannot be ticked
    assert notices == []
    assert listing(backend, "tasks") == []


def test_saving_what_did_not_change_does_not_reorder_or_bump(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    first = backend.addNote("first", "a", "none")
    clock.now += timedelta(minutes=1)
    backend.addNote("second", "b", "none")
    version = application.notes.get_note(first).version
    clock.now += timedelta(minutes=1)
    backend.updateNote(first, {"title": "first", "body": "a", "tint": "none", "pinned": False})
    assert application.notes.get_note(first).version == version
    assert [n["title"] for n in listing(backend, "notes")] == ["second", "first"]
    task_id = backend.addTask("T", {"due": ms(NOON), "hasTime": True})
    task_version = application.tasks.get_task(task_id).version
    backend.updateTask(task_id, {"title": "T", "description": "", "due": ms(NOON), "hasTime": True})
    assert application.tasks.get_task(task_id).version == task_version
