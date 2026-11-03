"""Focus statistics for the UI: per-day totals (rolling window) and the streak."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from productivity_app.schemas.focus import CreateFocusSession, DailyFocusTotal

NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")
# "Now" for these tests: Thursday 2026-09-24, 18:00 UTC (14:00 in New York, 23:30 in Kolkata)
NOW = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)


def _focus(application, clock, when: datetime, *, early_minutes: int | None = None, kind: str = "FOCUS") -> None:
    """Run one focus (or break) session starting at ``when``; the clock is left after it."""
    clock.now = when
    session = application.focus.create_session(CreateFocusSession(phase_kind=kind))
    started = application.timers.start_timer(session.timer.id, session.timer.version)
    if early_minutes is None:  # runs its full 25 minutes
        clock.now = when + timedelta(minutes=25)
    else:
        clock.now = when + timedelta(minutes=early_minutes)
    application.timers.complete_timer(started.id, started.version)


def _cancelled(application, clock, when: datetime) -> None:
    clock.now = when
    session = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
    started = application.timers.start_timer(session.timer.id, session.timer.version)
    clock.now = when + timedelta(minutes=5)
    application.timers.cancel_timer(started.id, started.version)


def _day(offset: int) -> datetime:
    """09:00 UTC, ``offset`` days before today."""
    return datetime(2026, 9, 24, 9, 0, tzinfo=UTC) - timedelta(days=offset)


def _totals(application, clock, days: int, tz="UTC"):
    clock.now = NOW
    return application.focus.daily_totals(days, tz)


def test_every_day_of_the_window_is_listed_oldest_first_even_without_focus(application, clock) -> None:
    clock.now = NOW
    totals = application.focus.daily_totals(7, "UTC")
    assert [row.day for row in totals] == [date(2026, 9, 18) + timedelta(days=i) for i in range(7)]
    assert all(row == DailyFocusTotal(row.day, 0, 0) for row in totals)
    assert application.focus.daily_totals(1, "UTC") == (DailyFocusTotal(date(2026, 9, 24), 0, 0),)


def test_rows_are_plain_date_sessions_minutes_tuples(application, clock) -> None:
    _focus(application, clock, _day(0))
    (row,) = _totals(application, clock, 1)
    assert tuple(row) == (date(2026, 9, 24), 1, 25)
    day, sessions, minutes = row
    assert (day, sessions, minutes) == (date(2026, 9, 24), 1, 25)


def test_sessions_and_minutes_are_totalled_per_local_day(application, clock) -> None:
    _focus(application, clock, _day(1))
    _focus(application, clock, _day(1) + timedelta(hours=1))
    _focus(application, clock, _day(0))
    _focus(application, clock, _day(0) + timedelta(hours=1), early_minutes=10)
    totals = {row.day: row for row in _totals(application, clock, 3)}
    assert totals[date(2026, 9, 23)] == DailyFocusTotal(date(2026, 9, 23), 2, 50)
    # a session ended early adds its minutes but is not a completed session
    assert totals[date(2026, 9, 24)] == DailyFocusTotal(date(2026, 9, 24), 1, 35)
    assert totals[date(2026, 9, 22)] == DailyFocusTotal(date(2026, 9, 22), 0, 0)


def test_breaks_cancelled_and_unfinished_sessions_do_not_count(application, clock) -> None:
    _focus(application, clock, _day(0), kind="SHORT_BREAK")
    _focus(application, clock, _day(0) + timedelta(hours=1), kind="LONG_BREAK")
    _cancelled(application, clock, _day(0) + timedelta(hours=2))
    clock.now = _day(0) + timedelta(hours=3)
    running = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
    application.timers.start_timer(running.timer.id, running.timer.version)
    assert _totals(application, clock, 1) == (DailyFocusTotal(date(2026, 9, 24), 0, 0),)
    assert application.focus.streak("UTC") == 0


def test_the_window_covers_exactly_the_requested_number_of_days(application, clock) -> None:
    _focus(application, clock, _day(6))
    _focus(application, clock, _day(7))
    week = _totals(application, clock, 7)
    assert week[0] == DailyFocusTotal(date(2026, 9, 18), 1, 25)
    assert sum(row.minutes for row in week) == 25  # the session 7 days ago is outside
    assert sum(row.minutes for row in _totals(application, clock, 8)) == 50
    assert sum(row.minutes for row in _totals(application, clock, 30)) == 50


def test_the_time_zone_decides_which_day_a_session_belongs_to(application, clock) -> None:
    _focus(application, clock, datetime(2026, 9, 24, 20, 0, tzinfo=UTC))  # 01:30 on the 25th in Kolkata
    _focus(application, clock, datetime(2026, 9, 24, 3, 0, tzinfo=UTC))  # still the 23rd in New York
    clock.now = datetime(2026, 9, 25, 2, 0, tzinfo=UTC)  # the 24th in New York, the 25th elsewhere
    by_zone = {
        zone: {row.day: row.sessions for row in application.focus.daily_totals(3, zone)}
        for zone in ("UTC", NEW_YORK, KOLKATA, "Asia/Kolkata")
    }
    assert by_zone["UTC"] == {date(2026, 9, 23): 0, date(2026, 9, 24): 2, date(2026, 9, 25): 0}
    assert by_zone[NEW_YORK] == {date(2026, 9, 22): 0, date(2026, 9, 23): 1, date(2026, 9, 24): 1}
    assert by_zone[KOLKATA] == {date(2026, 9, 23): 0, date(2026, 9, 24): 1, date(2026, 9, 25): 1}
    assert by_zone["Asia/Kolkata"] == by_zone[KOLKATA]


def test_the_window_starts_at_local_midnight(application, clock) -> None:
    _focus(application, clock, datetime(2026, 9, 22, 4, 30, tzinfo=UTC))  # 00:30 EDT on the 22nd
    _focus(application, clock, datetime(2026, 9, 22, 3, 30, tzinfo=UTC))  # 23:30 EDT on the 21st
    clock.now = NOW
    totals = application.focus.daily_totals(3, NEW_YORK)  # the 22nd .. the 24th
    assert [(row.day, row.sessions) for row in totals] == [
        (date(2026, 9, 22), 1),
        (date(2026, 9, 23), 0),
        (date(2026, 9, 24), 0),
    ]


def test_invalid_arguments(application, clock) -> None:
    with pytest.raises(ValueError):
        application.focus.daily_totals(0, "UTC")
    with pytest.raises(ZoneInfoNotFoundError):
        application.focus.daily_totals(1, "Not/AZone")
    with pytest.raises(ZoneInfoNotFoundError):
        application.focus.streak("Not/AZone")


# -- streak ---------------------------------------------------------------------------------------
def _streak(application, clock, tz="UTC") -> int:
    clock.now = NOW
    return application.focus.streak(tz)


def test_no_sessions_means_no_streak(application, clock) -> None:
    assert _streak(application, clock) == 0


def test_a_session_today_starts_a_streak_of_one(application, clock) -> None:
    _focus(application, clock, _day(0))
    assert _streak(application, clock) == 1


def test_consecutive_days_up_to_today_are_counted(application, clock) -> None:
    for offset in (0, 1, 2, 3):
        _focus(application, clock, _day(offset))
    assert _streak(application, clock) == 4


def test_nothing_yet_today_does_not_break_the_streak_until_the_day_ends(application, clock) -> None:
    for offset in (1, 2):
        _focus(application, clock, _day(offset))
    assert _streak(application, clock) == 2
    # a whole day without a session ends it
    assert application.focus.streak("UTC") == 2
    clock.now = NOW + timedelta(days=1)
    assert application.focus.streak("UTC") == 0


def test_a_gap_ends_the_streak(application, clock) -> None:
    for offset in (0, 1, 3, 4, 5):
        _focus(application, clock, _day(offset))
    assert _streak(application, clock) == 2


def test_several_sessions_in_one_day_count_once(application, clock) -> None:
    for hour in range(4):
        _focus(application, clock, _day(0) + timedelta(hours=hour))
    _focus(application, clock, _day(1))
    assert _streak(application, clock) == 2


def test_early_finished_breaks_and_cancelled_sessions_do_not_extend_a_streak(application, clock) -> None:
    _focus(application, clock, _day(0))
    _focus(application, clock, _day(1), early_minutes=10)
    _focus(application, clock, _day(1) + timedelta(hours=1), kind="SHORT_BREAK")
    _cancelled(application, clock, _day(1) + timedelta(hours=2))
    assert _streak(application, clock) == 1


def test_the_streak_follows_the_time_zone(application, clock) -> None:
    # 20:00 UTC on the 23rd and 03:00 UTC on the 24th: two days in UTC, one day (the 24th) in Kolkata
    _focus(application, clock, datetime(2026, 9, 23, 20, 0, tzinfo=UTC))
    _focus(application, clock, datetime(2026, 9, 24, 3, 0, tzinfo=UTC))
    clock.now = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    assert application.focus.streak("UTC") == 2
    assert application.focus.streak(KOLKATA) == 1
    assert application.focus.streak("Asia/Kolkata") == 1
    # a day later, with nothing new, the run ends in both: it was only alive through yesterday
    clock.now = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    assert application.focus.streak("UTC") == 0
    assert application.focus.streak(KOLKATA) == 0


def test_a_run_dated_in_the_future_is_ignored(application, clock) -> None:
    _focus(application, clock, _day(0) + timedelta(days=3))  # the system clock was later set back
    _focus(application, clock, _day(0))
    assert _streak(application, clock) == 1
