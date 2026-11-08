"""Repeating-reminder arithmetic (a port of prototype/qml/Proto/recurrence.js).

Expected values were cross-checked against the JavaScript original run under Node with the same
time zones (a randomized differential run over more than 100,000 comparisons found no difference).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from panelo.core.recurrence import (
    RepeatRule,
    advance,
    align_due,
    days_to_mask,
    describe,
    is_valid,
    mask_to_days,
    next_occurrence,
    ordinal,
)

NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")
UTC_ZONE = ZoneInfo("UTC")

DAILY = RepeatRule("DAILY")
WEEKDAYS = RepeatRule("WEEKDAYS")
YEARLY = RepeatRule("YEARLY")


def local(year: int, month: int, day: int, hour: int = 9, minute: int = 0, tz=UTC_ZONE) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def weekly(*days: int) -> RepeatRule:
    return RepeatRule("WEEKLY", days=days)


def monthly(day: int) -> RepeatRule:
    return RepeatRule("MONTHLY", day_of_month=day)


def every(n: int, unit: str) -> RepeatRule:
    return RepeatRule("EVERY", every_n=n, every_unit=unit)  # type: ignore[arg-type]


def at(rule: RepeatRule, after: datetime, anchor: datetime, tz=UTC_ZONE) -> datetime:
    result = next_occurrence(rule, after, anchor, tz)
    assert result is not None
    assert result.tzinfo is UTC or result.utcoffset() == timedelta(0)
    return result


# -- validity -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rule",
    [
        DAILY, WEEKDAYS, YEARLY, weekly(0), weekly(0, 1, 2, 3, 4, 5, 6), monthly(1), monthly(31),
        every(1, "HOURS"), every(5, "DAYS"), every(2, "WEEKS"),
    ],
)
def test_valid_rules(rule: RepeatRule) -> None:
    assert is_valid(rule)


@pytest.mark.parametrize(
    "rule",
    [
        None,
        RepeatRule("WEEKLY"),
        weekly(7),
        weekly(-1),
        RepeatRule("MONTHLY"),
        monthly(0),
        monthly(32),
        RepeatRule("EVERY"),
        every(0, "DAYS"),
        every(-1, "HOURS"),
        RepeatRule("EVERY", every_n=2),
        RepeatRule("EVERY", every_n=2, every_unit="MONTHS"),  # type: ignore[arg-type]
        RepeatRule("HOURLY"),  # type: ignore[arg-type]
    ],
)
def test_invalid_rules_have_no_occurrence_and_no_description(rule: RepeatRule | None) -> None:
    anchor = local(2026, 11, 3)
    assert not is_valid(rule)
    if rule is not None:
        assert next_occurrence(rule, anchor, anchor, UTC_ZONE) is None
        assert align_due(rule, anchor, UTC_ZONE) is None
        assert advance(rule, anchor, anchor, anchor, UTC_ZONE) is None
        assert describe(rule) == ""


def test_weekday_mask_uses_sunday_as_bit_zero() -> None:
    assert days_to_mask((0,)) == 0b0000001
    assert days_to_mask((6,)) == 0b1000000
    assert days_to_mask([1, 3, 5]) == 0b0101010
    assert days_to_mask(tuple(range(7))) == 127
    for mask in range(1, 128):
        assert days_to_mask(mask_to_days(mask)) == mask
    assert mask_to_days(0b0101010) == (1, 3, 5)


# -- day-based rules ----------------------------------------------------------------------------
def test_daily_is_the_next_day_at_the_anchor_time() -> None:
    anchor = local(2026, 11, 3, 9, 30)
    assert at(DAILY, anchor, anchor) == local(2026, 11, 4, 9, 30)
    assert at(DAILY, local(2026, 11, 3, 9, 29), anchor) == anchor
    assert at(DAILY, local(2026, 11, 3, 23, 59), anchor) == local(2026, 11, 4, 9, 30)


def test_result_is_always_strictly_after_the_reference_instant() -> None:
    anchor = local(2026, 11, 3, 9, 0)
    assert at(DAILY, anchor - timedelta(microseconds=1), anchor) == anchor
    assert at(DAILY, anchor, anchor) == anchor + timedelta(days=1)


def test_missed_occurrences_are_skipped() -> None:
    anchor = local(2026, 1, 5, 9, 0)  # a Monday
    after = local(2026, 11, 3, 12, 0)  # months later, a Tuesday
    assert at(DAILY, after, anchor) == local(2026, 11, 4, 9, 0)
    assert at(weekly(1), after, anchor) == local(2026, 11, 9, 9, 0)


def test_weekdays_skip_the_weekend() -> None:
    friday = local(2026, 11, 6, 9, 0)
    assert at(WEEKDAYS, friday, friday) == local(2026, 11, 9, 9, 0)  # Monday
    assert at(WEEKDAYS, local(2026, 11, 7, 10), friday) == local(2026, 11, 9, 9, 0)
    assert at(WEEKDAYS, local(2026, 11, 3, 10), friday) == local(2026, 11, 4, 9, 0)


def test_weekly_picks_the_next_listed_day_with_sunday_as_zero() -> None:
    anchor = local(2026, 11, 2, 18, 0)  # Monday
    assert at(weekly(1, 3, 5), anchor, anchor) == local(2026, 11, 4, 18, 0)  # Wednesday
    assert at(weekly(1, 3, 5), local(2026, 11, 4, 18), anchor) == local(2026, 11, 6, 18, 0)  # Friday
    assert at(weekly(1, 3, 5), local(2026, 11, 6, 18), anchor) == local(2026, 11, 9, 18, 0)  # Monday
    assert at(weekly(0), anchor, anchor) == local(2026, 11, 8, 18, 0)  # Sunday
    assert at(weekly(6), anchor, anchor) == local(2026, 11, 7, 18, 0)  # Saturday
    assert at(weekly(1), anchor, anchor) == local(2026, 11, 9, 18, 0)  # same weekday: next week


def test_monthly_clamps_to_the_last_day_of_a_short_month() -> None:
    anchor = local(2027, 1, 31, 8, 0)
    february = at(monthly(31), anchor, anchor)
    assert february == local(2027, 2, 28, 8, 0)
    assert at(monthly(31), february, anchor) == local(2027, 3, 31, 8, 0)
    assert at(monthly(31), local(2027, 3, 31, 8), anchor) == local(2027, 4, 30, 8, 0)
    assert at(monthly(30), local(2027, 1, 30, 8), anchor) == local(2027, 2, 28, 8, 0)
    assert at(monthly(29), local(2028, 1, 29, 8), anchor) == local(2028, 2, 29, 8, 0)  # leap year
    assert at(monthly(29), local(2027, 1, 29, 8), anchor) == local(2027, 2, 28, 8, 0)
    assert at(monthly(1), local(2026, 12, 15), anchor) == local(2027, 1, 1, 8, 0)


def test_yearly_keeps_the_anchor_date_and_clamps_february_29() -> None:
    anchor = local(2026, 11, 3, 7, 45)
    assert at(YEARLY, anchor, anchor) == local(2027, 11, 3, 7, 45)
    leap = local(2028, 2, 29, 7, 45)
    assert at(YEARLY, leap, leap) == local(2029, 2, 28, 7, 45)
    assert at(YEARLY, local(2031, 3, 1), leap) == local(2032, 2, 29, 7, 45)


# -- every N units --------------------------------------------------------------------------------
def test_every_n_hours_steps_from_the_anchor_in_absolute_time() -> None:
    anchor = local(2026, 11, 3, 9, 0)
    assert at(every(2, "HOURS"), anchor, anchor) == local(2026, 11, 3, 11, 0)
    assert at(every(2, "HOURS"), anchor + timedelta(hours=1, minutes=59, seconds=59), anchor) == (
        local(2026, 11, 3, 11, 0)
    )
    # five hours ten minutes in: occurrences at +2h and +4h were missed, the next is +6h
    assert at(every(2, "HOURS"), anchor + timedelta(hours=5, minutes=10), anchor) == local(2026, 11, 3, 15, 0)
    assert at(every(1, "HOURS"), anchor + timedelta(hours=5, minutes=10), anchor) == local(2026, 11, 3, 15, 0)
    # the grid of steps extends before the anchor as well
    assert at(every(2, "HOURS"), anchor - timedelta(hours=5), anchor) == anchor - timedelta(hours=4)
    assert at(every(2, "HOURS"), anchor - timedelta(microseconds=1), anchor) == anchor


def test_every_n_hours_ignores_daylight_saving() -> None:
    anchor = local(2026, 3, 8, 0, 30, NEW_YORK)  # 00:30 EST, two hours before the clocks jump
    first = at(every(2, "HOURS"), anchor, anchor, NEW_YORK)
    second = at(every(2, "HOURS"), first, anchor, NEW_YORK)
    assert first - anchor == timedelta(hours=2) and second - first == timedelta(hours=2)
    assert first.astimezone(NEW_YORK).hour == 3  # 02:30 does not exist: it is 03:30 EDT


def test_every_n_days_keeps_the_local_time_across_a_clock_change() -> None:
    anchor = local(2026, 3, 6, 9, 0, NEW_YORK)  # Friday, EST
    first = at(every(3, "DAYS"), anchor, anchor, NEW_YORK)
    assert first == local(2026, 3, 9, 9, 0, NEW_YORK).astimezone(UTC)
    assert first.hour == 13  # 09:00 EDT is 13:00 UTC, one hour earlier than 09:00 EST was
    assert at(every(3, "DAYS"), first, anchor, NEW_YORK) == local(2026, 3, 12, 9, 0, NEW_YORK)


def test_every_n_weeks_and_far_catch_up() -> None:
    anchor = local(2026, 11, 3, 9, 0)
    assert at(every(2, "WEEKS"), anchor, anchor) == local(2026, 11, 17, 9, 0)
    assert at(every(1, "WEEKS"), local(2026, 11, 10, 9, 0), anchor) == local(2026, 11, 17, 9, 0)
    far = anchor + timedelta(days=1000, hours=3)
    result = at(every(7, "DAYS"), far, anchor)
    assert result > far and result - far <= timedelta(days=7)
    assert (result - anchor).days % 7 == 0 and result.hour == 9


# -- daylight saving ------------------------------------------------------------------------------
def test_daily_keeps_the_wall_clock_time_through_both_transitions() -> None:
    anchor = local(2026, 3, 7, 9, 0, NEW_YORK)
    spring = at(DAILY, anchor, anchor, NEW_YORK)
    assert spring.astimezone(NEW_YORK) == local(2026, 3, 8, 9, 0, NEW_YORK)
    assert spring - anchor == timedelta(hours=23)
    anchor = local(2026, 10, 31, 9, 0, NEW_YORK)
    autumn = at(DAILY, anchor, anchor, NEW_YORK)
    assert autumn.astimezone(NEW_YORK) == local(2026, 11, 1, 9, 0, NEW_YORK)
    assert autumn - anchor == timedelta(hours=25)


def test_a_time_in_the_spring_forward_gap_moves_forward_by_the_gap() -> None:
    anchor = local(2026, 3, 7, 2, 30, NEW_YORK)
    result = at(DAILY, anchor, anchor, NEW_YORK)
    assert result.astimezone(NEW_YORK) == local(2026, 3, 8, 3, 30, NEW_YORK)
    following = at(DAILY, result, anchor, NEW_YORK)
    assert following.astimezone(NEW_YORK) == local(2026, 3, 9, 2, 30, NEW_YORK)


def test_a_repeated_autumn_hour_takes_its_first_occurrence() -> None:
    first = local(2026, 10, 31, 1, 30, NEW_YORK)  # EDT
    result = at(DAILY, first, first, NEW_YORK)
    assert result == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT, not the 01:30 EST hour later


def test_a_second_occurrence_anchor_still_lands_on_the_first_occurrence_of_a_repeated_hour() -> None:
    auckland = ZoneInfo("Pacific/Auckland")  # clocks go back 03:00 -> 02:00 on 2026-04-05
    anchor = datetime(2026, 4, 5, 2, 16, tzinfo=auckland, fold=1)  # the second 02:16, NZST
    assert anchor.utcoffset() == timedelta(hours=12)
    after = datetime(2026, 4, 4, 4, 21, tzinfo=UTC)
    # as in the prototype, the result is the first 02:16 (NZDT, +13:00)
    assert at(DAILY, after, anchor, auckland) == datetime(2026, 4, 4, 13, 16, tzinfo=UTC)
    assert at(DAILY, anchor, anchor, auckland) == datetime(2026, 4, 5, 14, 16, tzinfo=UTC)


def test_time_zone_decides_the_calendar_day() -> None:
    anchor = datetime(2026, 11, 3, 20, 0, tzinfo=UTC)  # Tuesday 20:00 UTC, Wednesday 01:30 in Kolkata
    assert anchor.astimezone(KOLKATA).weekday() == 2
    # Wednesday is today in Kolkata, so the next one is a week away; in UTC it is tomorrow
    assert at(weekly(3), anchor, anchor, KOLKATA) == datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
    assert at(weekly(3), anchor, anchor, UTC_ZONE) == datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


# -- alignment and Done ---------------------------------------------------------------------------
def test_align_due_keeps_a_matching_time_and_moves_a_non_matching_one() -> None:
    monday = local(2026, 11, 2, 9, 0)
    tuesday = local(2026, 11, 3, 9, 0)
    assert align_due(weekly(1), monday, UTC_ZONE) == monday
    assert align_due(weekly(1), tuesday, UTC_ZONE) == local(2026, 11, 9, 9, 0)
    assert align_due(DAILY, tuesday, UTC_ZONE) == tuesday
    assert align_due(WEEKDAYS, local(2026, 11, 7, 9, 0), UTC_ZONE) == local(2026, 11, 9, 9, 0)
    assert align_due(monthly(15), tuesday, UTC_ZONE) == local(2026, 11, 15, 9, 0)
    assert align_due(every(2, "HOURS"), tuesday, UTC_ZONE) == tuesday


def test_advance_is_strictly_after_both_now_and_the_due_time() -> None:
    anchor = local(2026, 11, 2, 9, 0)
    # on time: the next day
    assert advance(DAILY, anchor, anchor, anchor, UTC_ZONE) == local(2026, 11, 3, 9, 0)
    # done early (before it is due): still the occurrence after the due time
    assert advance(DAILY, anchor, anchor, anchor - timedelta(hours=3), UTC_ZONE) == local(2026, 11, 3, 9, 0)
    # done days late: occurrences that were missed are skipped
    assert advance(DAILY, anchor, anchor, local(2026, 11, 6, 15, 0), UTC_ZONE) == local(2026, 11, 7, 9, 0)


def test_advance_uses_the_anchor_for_time_of_day_after_a_snooze() -> None:
    anchor = local(2026, 11, 2, 9, 0)
    snoozed_due = local(2026, 11, 2, 9, 40)
    result = advance(DAILY, snoozed_due, anchor, local(2026, 11, 2, 9, 45), UTC_ZONE)
    assert result == local(2026, 11, 3, 9, 0)  # back on the scheduled 09:00, not 09:40


def test_advance_for_every_n_hours_steps_from_the_current_due_time() -> None:
    anchor = local(2026, 11, 2, 9, 0)
    snoozed_due = local(2026, 11, 2, 9, 20)
    result = advance(every(2, "HOURS"), snoozed_due, anchor, local(2026, 11, 2, 9, 25), UTC_ZONE)
    assert result == local(2026, 11, 2, 11, 20)
    # without an anchor the due time is the base
    assert advance(DAILY, snoozed_due, None, snoozed_due, UTC_ZONE) == local(2026, 11, 3, 9, 20)


# -- describe -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rule", "text"),
    [
        (DAILY, "every day"),
        (WEEKDAYS, "weekdays"),
        (weekly(1, 3, 5), "every Mon, Wed, Fri"),
        (weekly(0, 6, 1), "every Mon, Sat, Sun"),  # Monday first, Sunday last
        (weekly(0), "every Sun"),
        (monthly(1), "monthly on the 1st"),
        (monthly(2), "monthly on the 2nd"),
        (monthly(3), "monthly on the 3rd"),
        (monthly(11), "monthly on the 11th"),
        (monthly(12), "monthly on the 12th"),
        (monthly(21), "monthly on the 21st"),
        (monthly(22), "monthly on the 22nd"),
        (monthly(31), "monthly on the 31st"),
        (every(1, "HOURS"), "every hour"),
        (every(2, "HOURS"), "every 2 hours"),
        (every(1, "DAYS"), "every day"),
        (every(3, "DAYS"), "every 3 days"),
        (every(1, "WEEKS"), "every week"),
        (every(3, "WEEKS"), "every 3 weeks"),
        (YEARLY, "every year"),
    ],
)
def test_describe(rule: RepeatRule, text: str) -> None:
    assert describe(rule) == text


def test_describe_yearly_uses_the_anchor_date_in_the_given_zone() -> None:
    anchor = datetime(2026, 11, 3, 23, 30, tzinfo=UTC)  # already Nov 4 in Kolkata
    assert describe(YEARLY, anchor, UTC_ZONE) == "every year on Nov 3"
    assert describe(YEARLY, anchor, KOLKATA) == "every year on Nov 4"
    assert describe(YEARLY, anchor) == "every year on Nov 3"  # the anchor's own zone


def test_ordinals() -> None:
    assert [ordinal(n) for n in (1, 2, 3, 4, 10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31)] == [
        "1st", "2nd", "3rd", "4th", "10th", "11th", "12th", "13th", "14th", "20th", "21st", "22nd",
        "23rd", "24th", "30th", "31st",
    ]


# -- properties -----------------------------------------------------------------------------------
def test_randomized_results_are_after_the_reference_and_keep_the_local_time() -> None:
    generator = random.Random(2026)
    zones = [UTC_ZONE, NEW_YORK, KOLKATA, ZoneInfo("Australia/Lord_Howe"), ZoneInfo("Europe/London")]
    for _ in range(400):
        zone = generator.choice(zones)
        anchor = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=generator.randint(0, 60 * 24 * 700))
        after = anchor + timedelta(minutes=generator.randint(-5_000, 60 * 24 * 900))
        rule = generator.choice(
            [DAILY, WEEKDAYS, YEARLY, weekly(*generator.sample(range(7), 3)), monthly(generator.randint(1, 31)),
             every(generator.randint(1, 9), "DAYS"), every(generator.randint(1, 3), "WEEKS"),
             every(generator.randint(1, 30), "HOURS")]
        )
        result = at(rule, after, anchor, zone)
        assert result > after
        if rule.every_unit != "HOURS":
            wanted = anchor.astimezone(zone)
            got = result.astimezone(zone)
            # the wall-clock time of day survives except where it falls in a clock-change gap
            if datetime.combine(got.date(), wanted.timetz().replace(tzinfo=None), tzinfo=zone).astimezone(UTC) == result:
                assert (got.hour, got.minute) == (wanted.hour, wanted.minute)
        # a later reference never gives an earlier result
        assert at(rule, result, anchor, zone) > result
