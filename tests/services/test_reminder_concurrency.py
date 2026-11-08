from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from panelo.core.exceptions import StaleVersionError
from panelo.schemas.reminders import CreateReminder, UpdateReminder


def test_concurrent_reminder_updates_have_one_winner(application, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Original", due_at_utc=clock.now + timedelta(hours=1), scheduled_timezone="UTC")
    )

    def update(title: str) -> str:
        try:
            return application.reminders.update_reminder(
                reminder.id, UpdateReminder(expected_version=reminder.version, title=title)
            ).title
        except StaleVersionError:
            return "STALE"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, ("First", "Second")))
    assert results.count("STALE") == 1
    stored = application.reminders.get_reminder(reminder.id)
    assert stored.title in {"First", "Second"}
    assert stored.version == reminder.version + 1
