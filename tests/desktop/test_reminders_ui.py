from datetime import timedelta

import pytest
from PySide6.QtWidgets import QInputDialog

from productivity_app.desktop.ui.editors import local_instant
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.reminders import CreateReminder
from productivity_app.schemas.tasks import CreateTask
from tests.desktop.test_final_ui_integration import settle

pytestmark = pytest.mark.usefixtures("scripted_inputs")


def test_explicit_offset_converts_to_utc():
    assert local_instant("2026-10-03T18:30+05:30", "Asia/Calcutta").hour == 13


def test_reminder_lifecycle_snooze_and_history(final_ui, clock, monkeypatch):
    app = final_ui.session.application
    row = app.reminders.create_reminder(
        CreateReminder(
            title="In app", due_at_utc=clock.now - timedelta(hours=1), scheduled_timezone="UTC"
        )
    )
    settle(final_ui)
    presenter = final_ui.presenters["reminders"]
    presenter.view.load(row)
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: ((clock.now + timedelta(hours=2)).isoformat(), True),
    )
    presenter.mutate("snooze")
    settle(final_ui)
    assert presenter.view.current.snooze_count == 1, presenter.view.status.text()
    for action, state in (
        ("complete_reminder", "COMPLETED"),
        ("reopen_reminder", "PENDING"),
        ("dismiss_reminder", "DISMISSED"),
        ("reopen_reminder", "PENDING"),
        ("cancel_reminder", "CANCELLED"),
        ("reopen_reminder", "PENDING"),
        ("delete_reminder", "PENDING"),
        ("restore_reminder", "PENDING"),
    ):
        presenter.mutate(action)
        settle(final_ui)
        assert presenter.view.current.status == state
    presenter.mutate("history")
    settle(final_ui)
    assert "SNOOZED" in presenter.view.history.toPlainText()
    assert presenter.view.history.isReadOnly() and presenter.view.history.maximumHeight() == 180


def test_create_edit_and_single_link_validation_keeps_input(final_ui, clock):
    app = final_ui.session.application
    task = app.tasks.create_task(CreateTask(title="Linked task"))
    note = app.notes.create_note(CreateNote(title="Linked note"))
    settle(final_ui)
    p = final_ui.presenters["reminders"]
    p.view.fields["title"].setText("Reminder editor")
    p.view.fields["due_at_utc"].setText((clock.now + timedelta(hours=1)).isoformat())
    p.view.fields["scheduled_timezone"].setText("UTC")
    for key, row in (("task_id", task), ("note_id", note)):
        p.view.fields[key].setCurrentIndex(p.view.fields[key].findData(row.id))
    p.save()
    settle(final_ui)
    assert p.view.current is None and p.view.text("title") == "Reminder editor"
    assert app.reminders.list_reminders() == ()
    p.view.fields["note_id"].setCurrentIndex(0)
    p.save()
    settle(final_ui)
    assert p.view.current.task_id == task.id and p.view.current.note_id is None
    p.view.fields["message"].setPlainText("Edited message")
    p.save()
    settle(final_ui)
    assert app.reminders.get_reminder(p.view.current.id).message == "Edited message"
