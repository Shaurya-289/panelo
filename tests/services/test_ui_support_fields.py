"""Small additions the new UI needs from the service layer: listings that carry a task's
description and a reminder's message, and a timer listing without the focus-session timers."""

from __future__ import annotations

from datetime import timedelta

from productivity_app.bootstrap import Application
from productivity_app.schemas.focus import CreateFocusSequence, CreateFocusSession
from productivity_app.schemas.reminders import CreateReminder
from productivity_app.schemas.tasks import CreateTask
from productivity_app.schemas.timers import CreateTimer, TimerQuery
from tests.conftest import FakeClock


def test_task_summaries_carry_the_description(application: Application) -> None:
    application.tasks.create_task(CreateTask(title="T", description="the details"))
    assert [t.description for t in application.tasks.list_tasks()] == ["the details"]


def test_reminder_summaries_carry_the_message(application: Application, clock: FakeClock) -> None:
    application.reminders.create_reminder(
        CreateReminder(
            title="R", message="remember this", due_at_utc=clock.now + timedelta(hours=1),
            scheduled_timezone="UTC",
        )
    )
    assert [r.message for r in application.reminders.list_reminders()] == ["remember this"]


def test_timer_listing_can_leave_out_focus_timers(application: Application) -> None:
    ordinary = application.timers.create_timer(CreateTimer(name="Tea", planned_duration_ms=60_000))
    application.focus.create_sequence(CreateFocusSequence())
    application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
    everything = application.timers.list_timers(TimerQuery())
    only_ordinary = application.timers.list_timers(TimerQuery(exclude_focus=True))
    assert len(everything) == 3
    assert [t.id for t in only_ordinary] == [ordinary.id]
