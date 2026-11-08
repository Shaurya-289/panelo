"""Repeating reminders: rules on create/update, Done advancing the occurrence, snooze, history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text

from panelo.core.exceptions import InvalidStateTransitionError, ValidationError
from panelo.schemas.reminders import (
    CreateReminder,
    ReminderPageQuery,
    ReminderRepeat,
    SnoozeReminder,
    UpdateReminder,
)

NEW_YORK = ZoneInfo("America/New_York")
# 2026-09-24 is a Thursday; the fixture clock stands at noon UTC.
DAILY = ReminderRepeat(kind="DAILY")


def _create(application, due: datetime, repeat: ReminderRepeat | None = None, tz: str = "UTC", **extra):
    return application.reminders.create_reminder(
        CreateReminder(title="Repeating", due_at_utc=due, scheduled_timezone=tz, repeat=repeat, **extra)
    )


def _events(application, event_type: str) -> list:
    observed: list = []
    application.unit_of_work_factory()._event_bus.subscribe(event_type, observed.append)
    return observed


def _raw(application, reminder_id: str):
    with application.engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT repeat_kind, repeat_days, repeat_day_of_month, repeat_every_n, "
                "repeat_every_unit, anchor_at_utc, due_at_utc FROM reminders WHERE id = :id"
            ),
            {"id": reminder_id},
        ).one()


# -- the rule value -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "WEEKLY"},
        {"kind": "WEEKLY", "days": ()},
        {"kind": "WEEKLY", "days": (7,)},
        {"kind": "WEEKLY", "days": (-1,)},
        {"kind": "WEEKLY", "days": (1,), "day_of_month": 1},
        {"kind": "DAILY", "days": (1,)},
        {"kind": "WEEKDAYS", "every_n": 2},
        {"kind": "YEARLY", "day_of_month": 3},
        {"kind": "MONTHLY"},
        {"kind": "MONTHLY", "day_of_month": 0},
        {"kind": "MONTHLY", "day_of_month": 32},
        {"kind": "MONTHLY", "day_of_month": 5, "every_n": 1},
        {"kind": "EVERY"},
        {"kind": "EVERY", "every_n": 2},
        {"kind": "EVERY", "every_unit": "DAYS"},
        {"kind": "EVERY", "every_n": 0, "every_unit": "DAYS"},
        {"kind": "EVERY", "every_n": 1, "every_unit": "MONTHS"},
        {"kind": "EVERY", "every_n": 1, "every_unit": "DAYS", "days": (1,)},
        {"kind": "HOURLY"},
    ],
)
def test_malformed_rules_are_rejected_at_the_boundary(fields: dict[str, object]) -> None:
    with pytest.raises(PydanticValidationError):
        ReminderRepeat(**fields)  # type: ignore[arg-type]


def test_weekly_days_are_sorted_and_deduplicated() -> None:
    assert ReminderRepeat(kind="WEEKLY", days=(5, 1, 5, 3)).days == (1, 3, 5)


# -- create and update ----------------------------------------------------------------------------
def test_a_one_time_reminder_has_no_rule_or_anchor(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(hours=1))
    assert reminder.repeat is None and reminder.repeat_description is None
    assert reminder.anchor_at_utc is None and reminder.times_done == 0
    assert _raw(application, reminder.id)[:6] == (None, None, None, None, None, None)


def test_creating_with_a_rule_aligns_the_due_time_and_sets_the_anchor(application, clock) -> None:
    thursday = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)
    reminder = _create(application, thursday, ReminderRepeat(kind="WEEKLY", days=(1, 3, 5)))
    # Thursday is not a listed day: the next Friday 18:00 is the first valid time
    assert reminder.due_at_utc == datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
    assert reminder.anchor_at_utc == reminder.due_at_utc
    assert reminder.repeat == ReminderRepeat(kind="WEEKLY", days=(1, 3, 5))
    assert reminder.repeat_description == "every Mon, Wed, Fri"
    raw = _raw(application, reminder.id)
    assert raw[:5] == ("WEEKLY", 0b0101010, None, None, None)  # Sunday is bit 0
    again = application.reminders.get_reminder(reminder.id)
    assert again == reminder
    assert [item.repeat for item in application.reminders.list_reminders()] == [reminder.repeat]


def test_a_due_time_that_already_matches_the_rule_is_kept(application, clock) -> None:
    due = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)  # a Friday
    reminder = _create(application, due, ReminderRepeat(kind="WEEKLY", days=(5,)))
    assert reminder.due_at_utc == due == reminder.anchor_at_utc


def test_every_kind_round_trips_through_the_columns(application, clock) -> None:
    due = clock.now + timedelta(days=1)
    rules = [
        (ReminderRepeat(kind="DAILY"), "every day"),
        (ReminderRepeat(kind="WEEKDAYS"), "weekdays"),
        (ReminderRepeat(kind="MONTHLY", day_of_month=31), "monthly on the 31st"),
        (ReminderRepeat(kind="YEARLY"), "every year on Sep 25"),
        (ReminderRepeat(kind="EVERY", every_n=2, every_unit="HOURS"), "every 2 hours"),
        (ReminderRepeat(kind="EVERY", every_n=1, every_unit="WEEKS"), "every week"),
    ]
    for rule, description in rules:
        created = _create(application, due, rule)
        assert application.reminders.get_reminder(created.id).repeat == rule
        assert created.repeat_description == description


def test_setting_a_rule_realigns_the_due_time_and_clears_nothing_else(application, clock) -> None:
    reminder = _create(application, datetime(2026, 9, 24, 18, 0, tzinfo=UTC))  # Thursday
    observed = _events(application, "REMINDER_UPDATED")
    clock.now += timedelta(seconds=1)
    updated = application.reminders.update_reminder(
        reminder.id,
        UpdateReminder(
            expected_version=reminder.version, repeat=ReminderRepeat(kind="WEEKLY", days=(1,))
        ),
    )
    assert updated.due_at_utc == datetime(2026, 9, 28, 18, 0, tzinfo=UTC)  # the next Monday
    assert updated.anchor_at_utc == updated.due_at_utc
    assert updated.title == reminder.title and updated.version == reminder.version + 1
    assert [event.event_type for event in observed] == ["REMINDER_UPDATED"]
    history = application.reminders.list_reminder_events(reminder.id)
    assert (history[0].event_type, history[0].old_due_at_utc, history[0].new_due_at_utc) == (
        "REMINDER_UPDATED",
        reminder.due_at_utc,
        updated.due_at_utc,
    )


def test_setting_a_rule_never_moves_the_due_time_earlier(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(hours=5))
    updated = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, repeat=DAILY)
    )
    assert updated.due_at_utc == reminder.due_at_utc


def test_a_new_due_time_in_the_same_update_is_what_gets_aligned(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(hours=1))
    monday = datetime(2026, 9, 28, 7, 15, tzinfo=UTC)
    updated = application.reminders.update_reminder(
        reminder.id,
        UpdateReminder(
            expected_version=reminder.version,
            due_at_utc=monday + timedelta(days=1),  # Tuesday
            repeat=ReminderRepeat(kind="WEEKLY", days=(1,)),
        ),
    )
    assert updated.due_at_utc == datetime(2026, 10, 5, 7, 15, tzinfo=UTC)
    assert updated.anchor_at_utc == updated.due_at_utc


def test_clearing_the_rule_makes_the_reminder_one_time_again(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(days=1), DAILY)
    cleared = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, repeat=None)
    )
    assert cleared.repeat is None and cleared.anchor_at_utc is None
    assert cleared.due_at_utc == reminder.due_at_utc
    assert _raw(application, reminder.id)[:6] == (None, None, None, None, None, None)
    done = application.reminders.complete_reminder(cleared.id, cleared.version)
    assert done.status == "COMPLETED"


def test_an_update_that_leaves_the_rule_out_keeps_it(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(days=1), DAILY)
    renamed = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, title="Renamed")
    )
    assert renamed.repeat == DAILY and renamed.anchor_at_utc == reminder.anchor_at_utc


def test_changing_only_the_due_time_of_a_repeating_reminder_moves_the_anchor(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(days=1), DAILY)
    later = reminder.due_at_utc + timedelta(hours=3)
    moved = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, due_at_utc=later)
    )
    assert moved.due_at_utc == later and moved.anchor_at_utc == later and moved.repeat == DAILY


def test_resubmitting_the_same_rule_changes_nothing(application, clock) -> None:
    reminder = _create(application, clock.now + timedelta(days=1), DAILY)
    with pytest.raises(ValidationError):
        application.reminders.update_reminder(
            reminder.id, UpdateReminder(expected_version=reminder.version, repeat=DAILY)
        )
    with pytest.raises(ValidationError):
        application.reminders.update_reminder(reminder.id, UpdateReminder(expected_version=reminder.version))


def test_a_finished_reminder_cannot_be_given_a_rule_until_reopened(application, clock) -> None:
    reminder = _create(application, clock.now)
    done = application.reminders.complete_reminder(reminder.id, reminder.version)
    with pytest.raises(InvalidStateTransitionError):
        application.reminders.update_reminder(
            done.id, UpdateReminder(expected_version=done.version, repeat=DAILY)
        )
    reopened = application.reminders.reopen_reminder(done.id, done.version)
    updated = application.reminders.update_reminder(
        reopened.id, UpdateReminder(expected_version=reopened.version, repeat=DAILY)
    )
    assert updated.repeat == DAILY and updated.status == "PENDING"


# -- Done -----------------------------------------------------------------------------------------
def test_done_advances_a_repeating_reminder_instead_of_finishing_it(application, clock) -> None:
    due = clock.now - timedelta(minutes=5)  # due since a few minutes
    reminder = _create(application, due, DAILY)
    completed_events = _events(application, "REMINDER_COMPLETED")
    clock.now += timedelta(minutes=1)
    done = application.reminders.complete_reminder(reminder.id, reminder.version)
    assert done.status == "PENDING" and done.completed_at_utc is None
    assert done.due_at_utc == due + timedelta(days=1) == done.anchor_at_utc
    assert done.snooze_count == 0 and done.times_done == 1
    assert done.version == reminder.version + 1
    assert [event.event_type for event in completed_events] == ["REMINDER_COMPLETED"]
    history = application.reminders.list_reminder_events(reminder.id)
    assert (
        history[0].event_type,
        history[0].previous_status,
        history[0].new_status,
        history[0].old_due_at_utc,
        history[0].new_due_at_utc,
    ) == ("COMPLETED_OCCURRENCE", "PENDING", "PENDING", due, done.due_at_utc)
    # it is not in the Past list, and is upcoming again
    assert application.reminders.list_history() == ()
    assert [item.id for item in application.reminders.list_upcoming()] == [reminder.id]


def test_done_counts_every_completed_occurrence(application, clock) -> None:
    reminder = _create(application, clock.now, DAILY)
    current = reminder
    for expected in (1, 2, 3):
        clock.now = current.due_at_utc
        current = application.reminders.complete_reminder(current.id, current.version)
        assert current.times_done == expected
    assert application.reminders.get_reminder(reminder.id).times_done == 3
    assert [item.times_done for item in application.reminders.list_reminders()] == [3]
    other = _create(application, clock.now + timedelta(days=2), DAILY)
    assert application.reminders.get_reminder(other.id).times_done == 0
    kinds = [event.event_type for event in application.reminders.list_reminder_events(reminder.id)]
    assert kinds.count("COMPLETED_OCCURRENCE") == 3 and kinds.count("REMINDER_CREATED") == 1


def test_done_after_missing_several_occurrences_skips_to_one_in_the_future(application, clock) -> None:
    reminder = _create(application, clock.now, DAILY)
    clock.now += timedelta(days=4, hours=2)  # four missed days, now mid-day
    done = application.reminders.complete_reminder(reminder.id, reminder.version)
    assert done.due_at_utc == reminder.due_at_utc + timedelta(days=5)
    assert done.due_at_utc > clock.now


def test_done_after_a_snooze_returns_to_the_scheduled_time_and_forgets_snoozes(application, clock) -> None:
    reminder = _create(application, clock.now, DAILY)
    snoozed = application.reminders.snooze_reminder(
        reminder.id,
        SnoozeReminder(expected_version=reminder.version, due_at_utc=clock.now + timedelta(minutes=10)),
    )
    # snooze moves the due time, not the anchor
    assert snoozed.due_at_utc == clock.now + timedelta(minutes=10)
    assert snoozed.anchor_at_utc == reminder.anchor_at_utc and snoozed.snooze_count == 1
    clock.now += timedelta(minutes=11)
    done = application.reminders.complete_reminder(snoozed.id, snoozed.version)
    assert done.due_at_utc == reminder.due_at_utc + timedelta(days=1)  # not 10 minutes later
    assert done.anchor_at_utc == done.due_at_utc and done.snooze_count == 0


def test_done_on_an_every_n_hours_reminder_steps_from_the_current_due_time(application, clock) -> None:
    reminder = _create(application, clock.now, ReminderRepeat(kind="EVERY", every_n=2, every_unit="HOURS"))
    snoozed = application.reminders.snooze_reminder(
        reminder.id,
        SnoozeReminder(expected_version=reminder.version, due_at_utc=clock.now + timedelta(minutes=20)),
    )
    clock.now += timedelta(minutes=25)
    done = application.reminders.complete_reminder(snoozed.id, snoozed.version)
    assert done.due_at_utc == snoozed.due_at_utc + timedelta(hours=2)


def test_a_repeating_reminder_keeps_its_wall_clock_time_across_a_clock_change(application, clock) -> None:
    # Saturday 2026-03-07 09:00 New York (EST); the clocks jump the next night
    due = datetime(2026, 3, 7, 9, 0, tzinfo=NEW_YORK).astimezone(UTC)
    clock.now = due
    reminder = _create(application, due, DAILY, tz="America/New_York")
    done = application.reminders.complete_reminder(reminder.id, reminder.version)
    assert done.due_at_utc.astimezone(NEW_YORK) == datetime(2026, 3, 8, 9, 0, tzinfo=NEW_YORK)
    assert done.due_at_utc - due == timedelta(hours=23)


def test_weekly_rule_walks_through_its_days_on_each_done(application, clock) -> None:
    friday = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
    reminder = _create(application, friday, ReminderRepeat(kind="WEEKLY", days=(1, 3, 5)))
    current = reminder
    seen = []
    for _ in range(4):
        clock.now = current.due_at_utc
        current = application.reminders.complete_reminder(current.id, current.version)
        seen.append(current.due_at_utc.strftime("%a %H:%M"))
    assert seen == ["Mon 18:00", "Wed 18:00", "Fri 18:00", "Mon 18:00"]


def test_dismissing_or_cancelling_still_ends_a_repeating_reminder(application, clock) -> None:
    dismissed = _create(application, clock.now, DAILY)
    dismissed = application.reminders.dismiss_reminder(dismissed.id, dismissed.version)
    assert dismissed.status == "DISMISSED" and dismissed.repeat == DAILY
    cancelled = _create(application, clock.now, DAILY)
    cancelled = application.reminders.cancel_reminder(cancelled.id, cancelled.version)
    assert cancelled.status == "CANCELLED"
    reopened = application.reminders.reopen_reminder(dismissed.id, dismissed.version)
    assert reopened.status == "PENDING" and reopened.repeat == DAILY
    assert reopened.anchor_at_utc == dismissed.anchor_at_utc


def test_deleting_a_repeating_reminder_removes_its_completion_history(application, clock) -> None:
    reminder = _create(application, clock.now, DAILY)
    done = application.reminders.complete_reminder(reminder.id, reminder.version)
    application.reminders.delete_reminder(done.id, done.version)
    with pytest.raises(Exception, match="not found"):
        application.reminders.list_reminder_events(reminder.id, ReminderPageQuery())
    with application.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM reminder_events")) == 0
