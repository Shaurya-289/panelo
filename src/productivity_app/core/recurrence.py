"""Repeating-reminder arithmetic: a pure port of ``prototype/qml/Proto/recurrence.js``.

A rule is one of

* ``DAILY``
* ``WEEKDAYS``                       Monday to Friday
* ``WEEKLY`` with ``days``           weekday numbers, Sunday 0 .. Saturday 6, at least one
* ``MONTHLY`` with ``day_of_month``  1..31; a short month uses its last day
* ``YEARLY``                         the month and day of the anchor (Feb 29 clamps to Feb 28)
* ``EVERY`` with ``every_n`` and ``every_unit`` (``HOURS``, ``DAYS`` or ``WEEKS``)

Every rule keeps the local time of day of the *anchor* (the reminder's scheduled time) in the
reminder's time zone, so a 09:00 reminder stays at 09:00 across a daylight-saving change.  A
local time that does not exist (the spring-forward gap) moves forward by the gap and an
ambiguous one takes its first occurrence, as in the prototype.  ``EVERY n HOURS`` is the
exception: it steps in absolute time from the anchor.  Occurrences missed while the app was
closed are skipped: a result is always strictly after the ``after`` argument.

Instants are timezone-aware ``datetime`` values (any zone); results are in UTC.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Final, Literal

RepeatKind = Literal["DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY", "YEARLY", "EVERY"]
RepeatUnit = Literal["HOURS", "DAYS", "WEEKS"]

DAY_NAMES: Final = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
MONTH_NAMES: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
# The longest gap between two occurrences is a Feb 29 clamped to Feb 28: under 400 days.
SEARCH_DAYS: Final = 800
_MONDAY_FIRST: Final = (1, 2, 3, 4, 5, 6, 0)
_ONE_DAY_US: Final = 86_400_000_000


@dataclass(frozen=True, slots=True)
class RepeatRule:
    kind: RepeatKind
    days: tuple[int, ...] = ()
    day_of_month: int | None = None
    every_n: int | None = None
    every_unit: RepeatUnit | None = None


def days_to_mask(days: tuple[int, ...] | list[int]) -> int:
    """Weekday numbers (Sunday 0) as the stored bitmask: Sunday is bit 0 .. Saturday bit 6."""
    mask = 0
    for day in days:
        mask |= 1 << day
    return mask


def mask_to_days(mask: int) -> tuple[int, ...]:
    return tuple(day for day in range(7) if mask & (1 << day))


def is_valid(rule: RepeatRule | None) -> bool:
    if rule is None:
        return False
    if rule.kind in {"DAILY", "WEEKDAYS", "YEARLY"}:
        return True
    if rule.kind == "WEEKLY":
        return len(rule.days) > 0 and all(0 <= day <= 6 for day in rule.days)
    if rule.kind == "MONTHLY":
        return rule.day_of_month is not None and 1 <= rule.day_of_month <= 31
    if rule.kind == "EVERY":
        return (
            rule.every_n is not None
            and rule.every_n >= 1
            and rule.every_unit in {"HOURS", "DAYS", "WEEKS"}
        )
    return False


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def ordinal(number: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ``11th``, ``21st`` ..."""
    if 11 <= number % 100 <= 13:
        return f"{number}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _js_weekday(local: datetime) -> int:
    """Weekday number with Sunday 0 (``Date.getDay``)."""
    return (local.weekday() + 1) % 7


def _day_with_time(instant: datetime, offset: int, anchor_local: datetime, tz: tzinfo) -> datetime:
    """The day ``offset`` calendar days after the local day of ``instant``, at ``anchor``'s time.

    ``anchor_local`` is the anchor already converted to ``tz``.  The result is local time.
    """
    day = instant.astimezone(tz).date() + timedelta(days=offset)
    # ``fold=0``: an ambiguous local time (the repeated hour when clocks go back) takes its first
    # occurrence even when the anchor itself was the second one.
    clock = anchor_local.timetz().replace(tzinfo=None, fold=0)
    return datetime.combine(day, clock, tzinfo=tz)


def _matches_day(rule: RepeatRule, candidate_local: datetime, anchor_local: datetime) -> bool:
    if rule.kind == "DAILY":
        return True
    weekday = _js_weekday(candidate_local)
    if rule.kind == "WEEKDAYS":
        return 1 <= weekday <= 5
    if rule.kind == "WEEKLY":
        return weekday in rule.days
    if rule.kind == "MONTHLY":
        assert rule.day_of_month is not None
        return candidate_local.day == min(
            rule.day_of_month, _days_in_month(candidate_local.year, candidate_local.month)
        )
    if rule.kind == "YEARLY":
        return candidate_local.month == anchor_local.month and candidate_local.day == min(
            anchor_local.day, _days_in_month(candidate_local.year, anchor_local.month)
        )
    return False


def _microseconds(delta: timedelta) -> int:
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _next_every(rule: RepeatRule, after: datetime, anchor: datetime, tz: tzinfo) -> datetime:
    assert rule.every_n is not None and rule.every_unit is not None
    if rule.every_unit == "HOURS":
        step = timedelta(hours=rule.every_n)
        steps = _microseconds(after - anchor) // _microseconds(step) + 1
        at = anchor + steps * step
        while at <= after:
            at += step
        while at - step > after:
            at -= step
        return at.astimezone(UTC)
    interval = rule.every_n * (7 if rule.every_unit == "WEEKS" else 1)
    anchor_local = anchor.astimezone(tz)
    gone = math.floor(_microseconds(after - anchor) / _ONE_DAY_US + 0.5)  # JS Math.round
    count = max(0, gone // interval - 1)
    candidate = _day_with_time(anchor, count * interval, anchor_local, tz)
    while candidate <= after:
        count += 1
        candidate = _day_with_time(anchor, count * interval, anchor_local, tz)
    return candidate.astimezone(UTC)


def next_occurrence(
    rule: RepeatRule, after: datetime, anchor: datetime, tz: tzinfo
) -> datetime | None:
    """The next due time strictly after ``after``, or ``None`` for an invalid rule.

    ``anchor`` supplies the time of day (and, for ``YEARLY``, the month and day); for ``EVERY``
    it is also the first step.
    """
    if not is_valid(rule):
        return None
    # Arithmetic on an aware datetime in a zone with daylight saving is wall-clock arithmetic;
    # keep instants in UTC and convert to ``tz`` only to read or build local times.
    after, anchor = after.astimezone(UTC), anchor.astimezone(UTC)
    if rule.kind == "EVERY":
        return _next_every(rule, after, anchor, tz)
    anchor_local = anchor.astimezone(tz)
    for offset in range(SEARCH_DAYS):
        candidate = _day_with_time(after, offset, anchor_local, tz)
        if candidate > after and _matches_day(rule, candidate, anchor_local):
            return candidate.astimezone(UTC)
    return None


def align_due(rule: RepeatRule, due: datetime, tz: tzinfo) -> datetime | None:
    """The first valid due time at or after ``due`` (what choosing a rule does to the due time)."""
    due = due.astimezone(UTC)
    return next_occurrence(rule, due - timedelta(microseconds=1), due, tz)


def advance(
    rule: RepeatRule, due: datetime, anchor: datetime | None, now: datetime, tz: tzinfo
) -> datetime | None:
    """What Done does to a repeating reminder.

    The next occurrence is strictly after both ``now`` and the current ``due`` time (so any
    occurrence missed meanwhile is skipped).  ``anchor`` is the scheduled time, which differs
    from ``due`` after a snooze; ``EVERY n HOURS`` steps from the current due time instead.
    """
    due, now = due.astimezone(UTC), now.astimezone(UTC)
    base = due if rule.kind == "EVERY" and rule.every_unit == "HOURS" else (anchor or due)
    return next_occurrence(rule, max(now, due), base, tz)


def _sorted_days(days: tuple[int, ...]) -> list[int]:
    return [day for day in _MONDAY_FIRST if day in days]


def describe(
    rule: RepeatRule, anchor: datetime | None = None, tz: tzinfo | None = None
) -> str:
    """``every day``, ``weekdays``, ``every Mon, Wed, Fri``, ``monthly on the 1st``,
    ``every year on Nov 3``, ``every 2 hours``.

    ``YEARLY`` needs the ``anchor`` for its date (``tz`` defaults to the anchor's own zone);
    without it the text is ``every year``.  An invalid rule describes as the empty string.
    """
    if not is_valid(rule):
        return ""
    if rule.kind == "DAILY":
        return "every day"
    if rule.kind == "WEEKDAYS":
        return "weekdays"
    if rule.kind == "WEEKLY":
        return "every " + ", ".join(DAY_NAMES[day] for day in _sorted_days(rule.days))
    if rule.kind == "MONTHLY":
        assert rule.day_of_month is not None
        return f"monthly on the {ordinal(rule.day_of_month)}"
    if rule.kind == "YEARLY":
        if anchor is None:
            return "every year"
        local = anchor.astimezone(tz) if tz is not None else anchor
        return f"every year on {MONTH_NAMES[local.month - 1]} {local.day}"
    assert rule.every_n is not None and rule.every_unit is not None
    unit = rule.every_unit.lower()
    return f"every {unit[:-1]}" if rule.every_n == 1 else f"every {rule.every_n} {unit}"


__all__ = [
    "SEARCH_DAYS",
    "RepeatKind",
    "RepeatRule",
    "RepeatUnit",
    "advance",
    "align_due",
    "days_to_mask",
    "describe",
    "is_valid",
    "mask_to_days",
    "next_occurrence",
    "ordinal",
]
