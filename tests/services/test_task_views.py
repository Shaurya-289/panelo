from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from productivity_app.schemas.tasks import CreateTask, TaskCalendarQuery, TaskPageQuery


def test_calendar_window_uses_local_midnights_across_dst(application, clock) -> None:
    clock.now = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)  # midnight in New York before DST jump
    inside = application.tasks.create_task(
        CreateTask(title="DST day", due_at_utc=clock.now + timedelta(hours=22))
    )
    outside = application.tasks.create_task(
        CreateTask(title="Next day", start_at_utc=clock.now + timedelta(hours=23))
    )
    rows = application.tasks.list_today(TaskCalendarQuery(timezone="America/New_York"))
    assert [row.id for row in rows] == [inside.id]
    assert outside.id not in {row.id for row in rows}


def test_completed_and_important_views_are_status_aware(application) -> None:
    urgent = application.tasks.create_task(CreateTask(title="Urgent", priority="URGENT"))
    high = application.tasks.create_task(CreateTask(title="High", priority="HIGH"))
    completed = application.tasks.complete_task(high.id, high.version)
    cancelled = application.tasks.create_task(CreateTask(title="Cancelled", priority="URGENT"))
    application.tasks.cancel_task(cancelled.id, cancelled.version)
    assert [row.id for row in application.tasks.list_important(TaskPageQuery())] == [urgent.id]
    assert [row.id for row in application.tasks.list_completed(TaskPageQuery())] == [completed.id]


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError):
        TaskCalendarQuery(timezone="Mars/Olympus")
