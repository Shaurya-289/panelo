from __future__ import annotations

from datetime import UTC, datetime, timedelta

from productivity_app.bootstrap import create_application
from productivity_app.schemas.reminders import CreateReminder
from tests.conftest import FakeClock


def test_reminder_is_overdue_after_restart_without_recovery_write(app_config) -> None:
    before = FakeClock(datetime(2026, 9, 24, 9, tzinfo=UTC))
    first = create_application(app_config, before)
    try:
        reminder = first.reminders.create_reminder(
            CreateReminder(title="Persist", due_at_utc=before.now + timedelta(hours=1), scheduled_timezone="UTC")
        )
    finally:
        first.close()

    after = FakeClock(datetime(2026, 9, 24, 13, tzinfo=UTC))
    second = create_application(app_config, after)
    try:
        recovered = second.reminders.get_reminder(reminder.id)
        assert recovered.timing_state == "OVERDUE"
        assert [item.id for item in second.reminders.list_due()] == [reminder.id]
        assert [item.id for item in second.reminders.list_overdue()] == [reminder.id]
        assert recovered.version == reminder.version
        assert recovered.updated_at_utc == reminder.updated_at_utc
        assert len(second.reminders.list_reminder_events(reminder.id)) == 1
    finally:
        second.close()
