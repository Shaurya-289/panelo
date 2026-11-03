from __future__ import annotations

from datetime import timedelta

from productivity_app.schemas.reminders import CreateReminder, ReminderQuery


def test_reminder_repository_claims_one_version_and_filters_timing(application, clock) -> None:
    past = application.reminders.create_reminder(
        CreateReminder(title="Past", due_at_utc=clock.now - timedelta(microseconds=1), scheduled_timezone="UTC")
    )
    future = application.reminders.create_reminder(
        CreateReminder(title="Future", due_at_utc=clock.now + timedelta(minutes=1), scheduled_timezone="UTC")
    )

    with application.unit_of_work_factory() as uow:
        assert uow.reminders is not None
        assert uow.reminders.claim_version(past.id, past.version, clock.now)
        assert not uow.reminders.claim_version(past.id, past.version, clock.now)
        uow.commit()

    with application.unit_of_work_factory() as uow:
        assert uow.reminders is not None
        assert [item.id for item in uow.reminders.list_reminders(ReminderQuery(timing_states=("OVERDUE",)), clock.now)] == [past.id]
        assert [item.id for item in uow.reminders.list_upcoming(clock.now, limit=50, offset=0)] == [future.id]
        assert uow.reminders.get(past.id).version == past.version + 1  # type: ignore[union-attr]
