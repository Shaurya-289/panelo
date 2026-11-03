from datetime import timedelta

import pytest
from PySide6.QtCore import Qt

from productivity_app.desktop.ui.editors import local_instant
from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.tags import CreateTag
from productivity_app.schemas.tasks import CreateTask
from tests.desktop.test_final_ui_integration import settle


def test_ambiguous_and_nonexistent_local_times():
    with pytest.raises(ValueError, match="Ambiguous"):
        local_instant("2026-11-01T01:30", "America/New_York")
    with pytest.raises(ValueError, match="does not exist"):
        local_instant("2026-03-08T02:30", "America/New_York")
    assert local_instant("2026-11-01T01:30", "America/New_York", "0") != local_instant(
        "2026-11-01T01:30", "America/New_York", "1"
    )


def test_task_checkbox_waits_for_backend_and_lifecycle(final_ui):
    app = final_ui.session.application
    row = app.tasks.create_task(CreateTask(title="Task"))
    settle(final_ui)
    presenter = final_ui.presenters["tasks"]
    item = next(
        presenter.view.records.item(i)
        for i in range(presenter.view.records.count())
        if presenter.view.records.item(i).data(Qt.ItemDataRole.UserRole) == row.id
    )
    item.setCheckState(Qt.CheckState.Checked)
    assert item.checkState() == Qt.CheckState.Unchecked
    assert not presenter.view.records.isEnabled()
    settle(final_ui)
    assert app.tasks.get_task(row.id).status == "COMPLETED"
    presenter.view.load(app.tasks.get_task(row.id))
    for action, state in (
        ("reopen_task", "TODO"),
        ("start_task", "IN_PROGRESS"),
        ("return_task_to_todo", "TODO"),
        ("cancel_task", "CANCELLED"),
        ("reopen_task", "TODO"),
        ("delete_task", "TODO"),
        ("restore_task", "TODO"),
    ):
        presenter.mutate(action)
        settle(final_ui)
        assert presenter.view.current.status == state


def test_calendar_views_use_public_backend_membership(final_ui, clock):
    app = final_ui.session.application
    app.tasks.create_task(CreateTask(title="Due today", due_at_utc=clock.now + timedelta(hours=1)))
    app.tasks.create_task(CreateTask(title="Later", due_at_utc=clock.now + timedelta(days=4)))
    settle(final_ui)
    presenter = final_ui.presenters["tasks"]
    presenter.view.fields["timezone"].setText("UTC")
    from productivity_app.schemas.tasks import TaskCalendarQuery

    for label, method in (
        ("Today", "list_today"),
        ("Next Seven Days", "list_next_seven_days"),
        ("Upcoming", "list_upcoming"),
    ):
        presenter.view.mode.setCurrentText(label)
        settle(final_ui)
        assert [row.id for row in presenter.rows] == [
            row.id for row in getattr(app.tasks, method)(TaskCalendarQuery(timezone="UTC"))
        ]


def test_task_create_edit_relationships_and_list_filters(final_ui, clock):
    app = final_ui.session.application
    folder = app.folders.create_folder(CreateFolder(name="Scoped"))
    tag = app.tags.create_tag(CreateTag(name="Tagged"))
    settle(final_ui)
    p = final_ui.presenters["tasks"]
    p.view.fields["title"].setText("Editable task")
    p.view.fields["description"].setPlainText("Plain description")
    p.view.fields["priority"].setCurrentText("HIGH")
    p.view.fields["due_at_utc"].setText((clock.now + timedelta(days=2)).isoformat())
    p.view.fields["folder_id"].setCurrentIndex(p.view.fields["folder_id"].findData(folder.id))
    p.view.tags.item(0).setCheckState(Qt.CheckState.Checked)
    p.save()
    settle(final_ui)
    row = app.tasks.get_task(p.view.current.id)
    assert row.folder_id == folder.id and row.tag_ids == (tag.id,) and row.priority == "HIGH"
    p.view.fields["title"].setText("Edited")
    p.save()
    settle(final_ui)
    assert app.tasks.get_task(row.id).title == "Edited"
    app.tasks.create_task(CreateTask(title="Not in filter"))
    settle(final_ui)
    p.folder_filter, p.tag_filter, p.priority_filter, p.status_filter = (
        folder.id,
        tag.id,
        "HIGH",
        "TODO",
    )
    p.view.mode.setCurrentText("All")
    p.refresh()
    settle(final_ui)
    assert [value.id for value in p.rows] == [row.id]
