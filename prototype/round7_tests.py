"""Self-test steps for round 7: repeating reminders, the title-bar search, the overlay greeting and clock,
and the first-launch tour (used by `main.py --selftest`).

`recurrence_unit_checks` calls the real qml/Proto/recurrence.js through the Full App's `recurCall`. The
`*_steps` functions drive the windows with real QTest mouse and key events. Nothing here touches the real
registry, database or Recycle Bin, and no real app or sound is launched.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta

from app import clock_is_12h
from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest
from round5_tests import CTRL, Driver, Step, local_ms, ms_of

NOW_TAG = "repeat"


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def js_weekday(day: date) -> int:
    """JavaScript's weekday of a date: 0 = Sunday .. 6 = Saturday."""
    return (day.weekday() + 1) % 7


def next_match(after_ms: int, anchor_ms: int, ok: Callable[[date], bool]) -> int:
    """The first day (counting from the day of `after_ms`) at the anchor's time of day that satisfies
    `ok` and lies strictly after `after_ms`. Written independently of recurrence.js."""
    after = datetime.fromtimestamp(after_ms / 1000)
    tod = datetime.fromtimestamp(anchor_ms / 1000).time()
    for offset in range(800):
        day = after.date() + timedelta(days=offset)
        candidate = ms_of(datetime.combine(day, tod))
        if candidate > after_ms and ok(day):
            return candidate
    raise AssertionError("no occurrence found")


def plus_days(ms: int, days: int) -> int:
    """Calendar days later, at the same local time of day."""
    return ms_of(datetime.fromtimestamp(ms / 1000) + timedelta(days=days))


def days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


# ── recurrence.js unit checks ────────────────────────────────────────


def recurrence_unit_checks(check: Callable[[str, bool], None], full) -> None:  # noqa: ANN001
    def rc(fn: str, *args):  # noqa: ANN202
        return json.loads(full.recurCall(fn, json.dumps(list(args))))

    def t(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
        return local_ms(year, month, day, hour, minute)

    nxt = lambda rule, after, anchor: rc("nextOccurrence", rule, after, anchor)  # noqa: E731
    daily, weekdays, yearly = {"kind": "daily"}, {"kind": "weekdays"}, {"kind": "yearly"}
    weekly = {"kind": "weekly", "days": [1, 3, 5]}

    check(
        "recurrence: daily gives the next day at the same time, or later today when that is still ahead",
        nxt(daily, t(2026, 11, 3, 10), t(2026, 11, 1, 9)) == t(2026, 11, 4, 9)
        and nxt(daily, t(2026, 11, 3, 8), t(2026, 11, 1, 9)) == t(2026, 11, 3, 9)
        and nxt(daily, t(2026, 11, 3, 9), t(2026, 11, 3, 9)) == t(2026, 11, 4, 9),
    )
    check(
        "recurrence: weekdays skip the weekend (Fri -> Mon, Sat -> Mon, Mon -> Tue)",
        nxt(weekdays, t(2026, 11, 6, 10), t(2026, 11, 2, 9)) == t(2026, 11, 9, 9)
        and nxt(weekdays, t(2026, 11, 7, 10), t(2026, 11, 2, 9)) == t(2026, 11, 9, 9)
        and nxt(weekdays, t(2026, 11, 2, 10), t(2026, 11, 2, 9)) == t(2026, 11, 3, 9),
    )
    check(
        "recurrence: weekly Mon/Wed/Fri: Tue -> Wed, Wed (after the time) -> Fri, Fri -> Mon, Mon morning -> Mon evening",
        nxt(weekly, t(2026, 11, 3, 10), t(2026, 11, 3, 18)) == t(2026, 11, 4, 18)
        and nxt(weekly, t(2026, 11, 4, 18), t(2026, 11, 3, 18)) == t(2026, 11, 6, 18)
        and nxt(weekly, t(2026, 11, 6, 19), t(2026, 11, 3, 18)) == t(2026, 11, 9, 18)
        and nxt(weekly, t(2026, 11, 2, 10), t(2026, 11, 3, 18)) == t(2026, 11, 2, 18),
    )
    first = {"kind": "monthly", "day": 1}
    last = {"kind": "monthly", "day": 31}
    check(
        "recurrence: monthly on the 1st; the 31st in February falls on Feb 28 (2027) / Feb 29 (2028), then Mar 31",
        nxt(first, t(2026, 11, 3, 10), t(2026, 11, 1, 9)) == t(2026, 12, 1, 9)
        and nxt(first, t(2026, 12, 31, 10), t(2026, 11, 1, 9)) == t(2027, 1, 1, 9)
        and nxt(last, t(2027, 1, 31, 10), t(2027, 1, 31, 9)) == t(2027, 2, 28, 9)
        and nxt(last, t(2028, 1, 31, 10), t(2028, 1, 31, 9)) == t(2028, 2, 29, 9)
        and nxt(last, t(2027, 2, 28, 9), t(2027, 1, 31, 9)) == t(2027, 3, 31, 9)
        and nxt({"kind": "monthly", "day": 30}, t(2027, 1, 30, 10), t(2027, 1, 30, 9)) == t(2027, 2, 28, 9),
    )
    check(
        "recurrence: yearly keeps the month and day of the anchor (Feb 29 -> Feb 28 in a normal year, Feb 29 in a leap year)",
        nxt(yearly, t(2026, 11, 3, 10), t(2026, 11, 3, 9)) == t(2027, 11, 3, 9)
        and nxt(yearly, t(2028, 2, 29, 10), t(2028, 2, 29, 9)) == t(2029, 2, 28, 9)
        and nxt(yearly, t(2031, 12, 1, 0), t(2028, 2, 29, 9)) == t(2032, 2, 29, 9),
    )
    hours2 = {"kind": "every", "n": 2, "unit": "hours"}
    check(
        "recurrence: every 2 hours steps from the anchor and catches up (10:00 -> 12:00; after 13:30 -> 14:00)",
        nxt(hours2, t(2026, 11, 3, 10), t(2026, 11, 3, 10)) == t(2026, 11, 3, 12)
        and nxt(hours2, t(2026, 11, 3, 13, 30), t(2026, 11, 3, 10)) == t(2026, 11, 3, 14)
        and rc("alignDue", hours2, t(2026, 11, 3, 10, 17)) == t(2026, 11, 3, 10, 17),
    )
    check(
        "recurrence: every 3 days / every 2 weeks step from the anchor's day, keeping its time",
        nxt({"kind": "every", "n": 3, "unit": "days"}, t(2026, 11, 10, 10), t(2026, 11, 1, 9)) == t(2026, 11, 13, 9)
        and nxt({"kind": "every", "n": 2, "unit": "weeks"}, t(2026, 11, 20, 10), t(2026, 11, 2, 9)) == t(2026, 11, 30, 9),
    )
    check(
        "recurrence: missed occurrences are skipped (a daily reminder 5 days late, a weekly one 5 weeks late, 2 h steps)",
        nxt(daily, t(2026, 11, 3, 10), t(2026, 10, 29, 9)) == t(2026, 11, 4, 9)
        and rc("advance", weekly, t(2026, 10, 2, 18), t(2026, 10, 2, 18), t(2026, 11, 3, 10)) == t(2026, 11, 4, 18)
        and rc("advance", hours2, t(2026, 11, 3, 10), t(2026, 11, 3, 10), t(2026, 11, 3, 15, 30)) == t(2026, 11, 3, 16),
    )
    check(
        "recurrence: after a snooze the next occurrence keeps the scheduled time of day (Wed 18:10 snooze -> Fri 18:00)",
        rc("advance", weekly, t(2026, 11, 4, 18, 10), t(2026, 11, 4, 18), t(2026, 11, 4, 18, 12)) == t(2026, 11, 6, 18),
    )
    check(
        "recurrence: picking a rule moves the due time to the next valid day at or after it (Tue + Mon/Wed/Fri -> Wed, same time)",
        rc("alignDue", weekly, t(2026, 11, 3, 10)) == t(2026, 11, 4, 10)
        and rc("alignDue", weekly, t(2026, 11, 4, 10)) == t(2026, 11, 4, 10)
        and rc("alignDue", daily, t(2026, 11, 3, 10)) == t(2026, 11, 3, 10),
    )
    check(
        "recurrence: invalid rules give null (no days, day 7, month day 0, every 0, unknown kind, no rule)",
        nxt({"kind": "weekly", "days": []}, 0, 0) is None and nxt({"kind": "weekly", "days": [7]}, 0, 0) is None
        and nxt({"kind": "monthly", "day": 0}, 0, 0) is None and nxt({"kind": "every", "n": 0, "unit": "days"}, 0, 0) is None
        and nxt({"kind": "fortnightly"}, 0, 0) is None and nxt(None, 0, 0) is None
        and rc("isValid", weekly) is True and rc("isValid", {"kind": "every", "n": 2, "unit": "years"}) is False,
    )
    anchor = t(2026, 11, 3, 9)
    texts = [
        rc("describe", daily), rc("describe", weekdays), rc("describe", weekly), rc("describe", {"kind": "weekly", "days": [5, 1, 3]}),
        rc("describe", {"kind": "weekly", "days": [0, 6]}), rc("describe", first), rc("describe", yearly, anchor),
        rc("describe", yearly), rc("describe", hours2), rc("describe", {"kind": "every", "n": 1, "unit": "hours"}),
        rc("describe", {"kind": "every", "n": 3, "unit": "weeks"}), rc("describe", {"kind": "every", "n": 1, "unit": "days"}),
    ]
    check(
        "recurrence: describe: " + " | ".join(texts),
        texts == ["every day", "weekdays", "every Mon, Wed, Fri", "every Mon, Wed, Fri", "every Sat, Sun",
                  "monthly on the 1st", "every year on Nov 3", "every year", "every 2 hours", "every hour",
                  "every 3 weeks", "every day"],
    )
    suffixes = [rc("ordinal", n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)]
    check(
        "recurrence: ordinals " + ", ".join(suffixes),
        suffixes == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st"],
    )
    check(
        "recurrence: the list line joins the rule and the relative time ('every Mon, Wed, Fri · in 2 days'; one-time: just the time)",
        rc("subtitle", weekly, anchor, "in 2 days") == "every Mon, Wed, Fri · in 2 days"
        and rc("subtitle", None, anchor, "in 2 days") == "in 2 days",
    )


# ── Repeating reminders in the Full App ──────────────────────────────


def repeat_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store = d.full, d.store
    memo: dict = {}
    tag = NOW_TAG

    def add(title: str, due: int, **fields) -> int:  # noqa: ANN003
        return int(store.addReminder(title, due, fields))

    def rem(key: str) -> dict:
        return d.reminder(memo[key])

    def rule(key: str):  # noqa: ANN202
        return rem(key)["repeat"]

    def chip() -> str:
        return str(full.property("repeatText"))

    def click_repeat(name: str, index: int = 0) -> None:
        d.click(f"reminderEditor.repeat.{name}", index)

    def next_tuesday_ten() -> int:
        today = datetime.now().date()
        ahead = (1 - today.weekday()) % 7 or 7        # Tuesday is weekday 1; never today
        return ms_of(datetime.combine(today + timedelta(days=ahead), datetime.min.time().replace(hour=10)))

    def seconds_ago(seconds: int) -> int:
        return now_ms() // 1000 * 1000 - seconds * 1000

    # -- due-now repeating reminders, finished with the real Done / Snooze buttons --
    def make_weekly() -> None:
        memo["weekly"] = add("Weekly test", seconds_ago(2), repeat={"kind": "weekly", "days": [1, 3, 5]}, snoozeCount=2)
        memo["weekly_due"] = rem("weekly")["due"]

    def make_hours() -> None:
        memo["hours"] = add("Hours test", seconds_ago(2), repeat={"kind": "every", "n": 2, "unit": "hours"})
        memo["hours_due"] = rem("hours")["due"]

    def make_snooze() -> None:
        memo["snooze"] = add("Snooze test", seconds_ago(2), repeat={"kind": "daily"})
        memo["snooze_due"] = rem("snooze")["due"]
        memo["before"] = now_ms()

    def weekly_expected() -> int:
        return next_match(now_ms(), memo["weekly_due"], lambda day: js_weekday(day) in (1, 3, 5))

    def in_open_groups(key: str) -> bool:
        groups = ("dueNow", "today", "tomorrow", "week", "later")
        return any(r["id"] == memo[key] for g in groups for r in d.rgroup(g))

    def in_past(key: str) -> bool:
        return any(r["id"] == memo[key] for r in d.rgroup("past"))

    def make_late_daily() -> None:
        late = ms_of(datetime.combine(datetime.now().date() - timedelta(days=5), datetime.min.time().replace(hour=9)))
        memo["late"] = add("Late daily", late, repeat={"kind": "daily"})
        memo["late_anchor"] = late
        store.completeReminder(memo["late"])

    # -- the pop-out and its Repeat popup --
    def make_popup_reminder() -> None:
        memo["popup"] = add("Popup test", next_tuesday_ten())
        memo["tue"] = rem("popup")["due"]

    def due_of(key: str) -> int:
        return rem(key)["due"]

    def open_popup_reminder() -> None:
        full.openReminderById(memo["popup"])

    def open_seed(reminder_id: int) -> None:
        full.openReminderById(reminder_id)

    def days_of(key: str) -> list[int]:
        current = rule(key)
        return sorted(current["days"]) if current and current["kind"] == "weekly" else []

    def month_day_expected(wanted: int, from_ms: int, anchor_ms: int) -> int:
        return next_match(from_ms - 1, anchor_ms,
                          lambda day: day.day == min(wanted, days_in_month(day.year, day.month)))

    def click_blank_left() -> None:
        d.click_at(d.at(60, 420))

    def shown_close() -> bool:
        return full.property("reminderEditorOpen") is False

    def full_size_window() -> None:
        full.setWidth(1320)
        full.setHeight(820)

    return [
        (0, lambda: controller.show_full("reminders"), None),
        (300, full_size_window, None),
        (500, lambda: full.scrollRemindersTop(), None),
        # seeds
        (0, None, (f"{tag}: the seed has 3 repeating reminders: Drink water every 2 hours, Gym Mon/Wed/Fri 18:00, Pay rent monthly on the 1st 09:00",
                   lambda: _seed_ok(d))),
        # Done on weekly / hours / snooze
        (0, make_weekly, None),
        (500, lambda: d.click("reminderDone", memo["weekly"]), None),
        (500, None, (f"{tag}: Done on a weekly Mon/Wed/Fri reminder jumps to the next day in the set at the same time (not to Past)",
                     lambda: rem("weekly")["due"] == weekly_expected() and rem("weekly")["done"] is False
                     and rem("weekly")["due"] > now_ms() and not in_past("weekly") and in_open_groups("weekly"))),
        (0, None, (f"{tag}: ...and it resets the snooze count and records the completion in its history",
                   lambda: rem("weekly")["snoozeCount"] == 0 and len(rem("weekly")["history"]) == 1
                   and rem("weekly")["repeat"] == {"kind": "weekly", "days": [1, 3, 5]})),
        (0, make_hours, None),
        (500, lambda: d.click("reminderDone", memo["hours"]), None),
        (500, None, (f"{tag}: Done on an every-2-hours reminder moves it exactly 2 hours on",
                     lambda: rem("hours")["due"] == memo["hours_due"] + 7200000 and rem("hours")["done"] is False
                     and not in_past("hours"))),
        (0, make_snooze, None),
        (500, lambda: d.click("reminderSnooze", memo["snooze"]), None),
        (400, None, (f"{tag}: Snooze on a repeating reminder is +10 min and leaves the rule alone",
                     lambda: abs(rem("snooze")["due"] - (memo["before"] + 600000)) < 15000 and rule("snooze") == {"kind": "daily"}
                     and rem("snooze")["snoozeCount"] == 1)),
        (0, lambda: store.completeReminder(memo["snooze"]), None),
        (300, None, (f"{tag}: Done after a snooze goes to the scheduled time tomorrow, not 10 minutes later",
                     lambda: rem("snooze")["due"] == next_match(now_ms() + 600000, memo["snooze_due"], lambda _day: True))),
        (0, make_late_daily, None),
        (300, None, (f"{tag}: Done on a reminder that missed 5 daily occurrences skips to the next 09:00 in the future",
                     lambda: rem("late")["due"] == next_match(now_ms(), memo["late_anchor"], lambda _day: True)
                     and rem("late")["due"] > now_ms())),
        # the Repeat popup
        (0, make_popup_reminder, None),
        (0, open_popup_reminder, None),
        (700, None, (f"{tag}: the pop-out has a 'Repeat' chip (reads 'Repeat' while off)",
                     lambda: full.property("reminderEditorOpen") is True and chip() == "Repeat" and rule("popup") is None)),
        (0, lambda: d.click("reminderEditor.repeat"), None),
        (400, None, (f"{tag}: clicking the chip opens the Repeat popup", lambda: full.property("repeatOpen") is True)),
        (0, lambda: click_repeat("daily"), None),
        (300, None, (f"{tag}: Every day sets the rule, the chip reads 'every day' and the due time is unchanged",
                     lambda: rule("popup") == {"kind": "daily"} and chip() == "every day" and due_of("popup") == memo["tue"])),
        (0, lambda: click_repeat("weekdays"), None),
        (300, None, (f"{tag}: Weekdays sets {{kind: weekdays}} and the chip reads 'weekdays'",
                     lambda: rule("popup") == {"kind": "weekdays"} and chip() == "weekdays")),
        (0, lambda: click_repeat("weekly"), None),
        (300, None, (f"{tag}: Every week on defaults to the due date's weekday (Tuesday)",
                     lambda: days_of("popup") == [2] and chip() == "every Tue")),
        (0, lambda: click_repeat("pill", 0), None),
        (0, lambda: click_repeat("pill", 2), None),
        (0, lambda: click_repeat("pill", 4), None),
        (300, None, (f"{tag}: the weekly pills toggle days (Mon, Tue, Wed, Fri selected)",
                     lambda: days_of("popup") == [1, 2, 3, 5] and chip() == "every Mon, Tue, Wed, Fri" and due_of("popup") == memo["tue"])),
        (0, lambda: click_repeat("pill", 1), None),
        (300, None, (f"{tag}: deselecting Tuesday (Mon/Wed/Fri left) moves a Tuesday reminder to Wednesday at the same time",
                     lambda: days_of("popup") == [1, 3, 5] and chip() == "every Mon, Wed, Fri"
                     and due_of("popup") == plus_days(memo["tue"], 1))),
        (0, lambda: (click_repeat("pill", 0), click_repeat("pill", 4)), None),
        (300, None, (f"{tag}: ...only Wednesday is left", lambda: days_of("popup") == [3])),
        (0, lambda: click_repeat("pill", 2), None),
        (300, None, (f"{tag}: the weekly pills can't all be deselected (the last day stays)",
                     lambda: days_of("popup") == [3] and chip() == "every Wed")),
        (0, lambda: click_repeat("monthly"), None),
        (300, None, (f"{tag}: Every month on the... defaults to the due date's day of the month",
                     lambda: rule("popup")["kind"] == "monthly"
                     and rule("popup")["day"] == datetime.fromtimestamp(due_of("popup") / 1000).day
                     and chip().startswith("monthly on the "))),
        (0, lambda: memo.update(before_month=due_of("popup")), None),
        (0, lambda: click_repeat("dayChip"), None),
        (300, None, (f"{tag}: the day chip opens the day menu", lambda: full.property("repeatMenuOpen") is True)),
        (0, lambda: click_repeat("menuItem", 8), None),
        (300, None, (f"{tag}: choosing the 9th sets monthly day 9, the chip updates and the due moves to the next 9th at the same time",
                     lambda: rule("popup") == {"kind": "monthly", "day": 9} and chip() == "monthly on the 9th"
                     and due_of("popup") == month_day_expected(9, memo["before_month"], memo["before_month"])
                     and full.property("repeatMenuOpen") is False and full.property("repeatOpen") is True)),
        (0, lambda: click_repeat("yearly"), None),
        (300, None, (f"{tag}: Every year sets {{kind: yearly}} and the chip names the date ('every year on Mon D')",
                     lambda: rule("popup") == {"kind": "yearly"}
                     and chip() == "every year on " + datetime.fromtimestamp(due_of("popup") / 1000).strftime("%b") + " "
                     + str(datetime.fromtimestamp(due_of("popup") / 1000).day))),
        (0, lambda: click_repeat("custom"), None),
        (300, None, (f"{tag}: Custom starts at every 2 hours",
                     lambda: rule("popup") == {"kind": "every", "n": 2, "unit": "hours"} and chip() == "every 2 hours")),
        (0, lambda: click_repeat("stepperPlus"), None),
        (300, None, (f"{tag}: the stepper changes the count (every 3 hours)",
                     lambda: rule("popup") == {"kind": "every", "n": 3, "unit": "hours"} and chip() == "every 3 hours")),
        (0, lambda: click_repeat("unitChip"), None),
        (300, None, (f"{tag}: the unit chip opens the hours / days / weeks menu", lambda: full.property("repeatMenuOpen") is True)),
        (0, lambda: click_repeat("menuItem", 1), None),
        (300, None, (f"{tag}: choosing 'days' gives every 3 days",
                     lambda: rule("popup") == {"kind": "every", "n": 3, "unit": "days"} and chip() == "every 3 days")),
        # click-through
        (0, lambda: click_repeat("monthly"), None),
        (200, lambda: click_repeat("dayChip"), None),
        (300, None, (f"{tag}: the day menu is open again", lambda: full.property("repeatMenuOpen") is True)),
        (0, lambda: memo.update(rule_before=rule("popup")), None),
        (0, lambda: click_repeat("done"), None),
        (300, None, (f"{tag}: click-through: clicking outside the day menu (over the popup's Done button) closes only the menu",
                     lambda: full.property("repeatMenuOpen") is False and full.property("repeatOpen") is True
                     and rule("popup") == memo["rule_before"])),
        (0, click_blank_left, None),
        (300, None, (f"{tag}: click-through: clicking outside the popup closes only the popup (the pop-out stays open, nothing under the click reacts)",
                     lambda: full.property("repeatOpen") is False and full.property("reminderEditorOpen") is True
                     and full.property("taskEditorOpen") is False and full.property("editorOpen") is False)),
        (0, lambda: d.click("reminderEditor.repeat"), None),
        (300, lambda: click_repeat("custom"), None),
        (0, lambda: click_repeat("unitChip"), None),
        (300, None, None),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (300, None, (f"{tag}: Esc closes the open menu first", lambda: full.property("repeatMenuOpen") is False and full.property("repeatOpen") is True)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (300, None, (f"{tag}: ...then the popup, with the pop-out still open", lambda: full.property("repeatOpen") is False and full.property("reminderEditorOpen") is True)),
        (0, lambda: d.click("reminderEditor.repeat"), None),
        (300, lambda: click_repeat("never"), None),
        (300, None, (f"{tag}: Never clears the rule and the chip reads 'Repeat' again",
                     lambda: rule("popup") is None and chip() == "Repeat")),
        (0, lambda: click_repeat("done"), None),
        (300, None, (f"{tag}: the popup's Done button closes it", lambda: full.property("repeatOpen") is False)),
        (0, lambda: d.click("reminderEditor.close"), None),
        (600, None, (f"{tag}: the pop-out closes", lambda: shown_close())),
        # the pop-out of a repeating reminder
        (0, lambda: open_seed(12), None),
        (700, None, (f"{tag}: the pop-out of 'Pay rent' shows 'monthly on the 1st' and 'Done 5×'",
                     lambda: chip() == "monthly on the 1st" and str(full.property("reminderInfoText")).startswith("Done 5× "))),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (600, lambda: (open_seed(11), memo.update(gym=d.reminder(11)["due"])), None),
        (700, lambda: d.click("reminderEditor.done"), None),
        (700, None, (f"{tag}: Done in the pop-out of a repeating reminder jumps to its next occurrence and counts it (Gym: Done 4×)",
                     lambda: d.reminder(11)["due"] > memo["gym"] and d.reminder(11)["done"] is False
                     and len(d.reminder(11)["history"]) == 4 and shown_close())),
        (0, controller.hide_full, None),
    ]


# ── The "Take a note" bars lose their checklist icon ─────────────────


def checklist_steps(controller) -> list[Step]:  # noqa: ANN001
    full, overlay = controller.full, controller.overlay
    tag = "take-note bar"
    return [
        (0, lambda: controller.show_full("notes"), None),
        (500, None, (f"{tag}: the Full App's bar has no checklist icon any more (only the palette is left)",
                     lambda: full.property("takeNoteIconCount") == 1 and full.property("takeNoteHasChecklist") is False)),
        (0, controller.hide_full, None),
        (200, controller.open_overlay, None),
        (500, None, (f"{tag}: the overlay's capture box has no icons at all, and no checklist",
                     lambda: overlay.property("captureIconsVisible") is False and overlay.property("captureHasChecklist") is False)),
        (0, controller.close_overlay, None),
        (400, None, None),
    ]


# ── The title-bar search ─────────────────────────────────────────────


def search_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, library = d.full, controller.library
    memo: dict = {}
    tag = "search"
    down, up = Qt.Key.Key_Down, Qt.Key.Key_Up

    def counts() -> dict:
        return json.loads(str(full.property("searchCounts")))

    def shown() -> dict:
        return json.loads(str(full.property("searchShown")))

    def expected(query: str) -> dict:
        q = query.lower()
        return {
            "notes": len([n for n in d.notes() if not n["deletedAt"] and not n["archived"] and q in (n["title"] + "\n" + n["body"]).lower()]),
            "tasks": len([t for t in d.tasks() if not t["deletedAt"] and q in t["title"].lower()]),
            "reminders": len([r for r in d.reminders() if q in r["title"].lower()]),
            "files": len(library.search(query)),
        }

    def current() -> str:
        return str(full.property("searchCurrentTitle"))

    def nav_to(prefix: str) -> None:
        for _ in range(int(full.property("searchNavCount")) + 1):
            if current().startswith(prefix):
                return
            d.key(down)

    def open_box_and_type(text: str) -> None:
        d.key(Qt.Key.Key_F, CTRL)
        d.type_text(text)

    def retype(text: str) -> None:
        d.key(Qt.Key.Key_A, CTRL)
        d.type_text(text)

    def first_note_title() -> str:
        live = [n for n in d.notes() if not n["deletedAt"] and not n["archived"] and "re" in (n["title"] + "\n" + n["body"]).lower()]
        ordered = [n for n in live if n["pinned"]] + [n for n in live if not n["pinned"]]
        first = ordered[0]
        return first["title"] or first["body"].split("\n")[0]

    def dclick(name: str, index: int = 0) -> None:
        QTest.mouseDClick(full, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, d.probe(name, index))

    def reset_window() -> None:
        full.setWidth(1320)
        full.setHeight(820)

    def narrow_window() -> None:
        full.setWidth(860)

    def esc() -> None:
        d.key(Qt.Key.Key_Escape)

    def pick_task() -> None:
        """A task that is still around after the earlier groups (some of them delete or rename tasks)."""
        memo["task"] = next(t for t in d.tasks() if not t["deletedAt"] and not t["done"])

    def pick_reminder() -> None:
        others = [x["title"].lower() for x in d.tasks() + d.notes()]
        memo["reminder"] = next(r for r in d.reminders() if not r["done"]
                                and not any(title.startswith(r["title"].lower()) for title in others))

    def note_title_of(note_id: int) -> str:
        note = next(n for n in d.notes() if n["id"] == note_id)
        return note["title"] or note["body"].split("\n")[0]

    return [
        (0, lambda: controller.show_full("notes"), None),
        (400, reset_window, None),
        (400, None, (f"{tag}: the title bar has a search box (not collapsed at 1320 px) and the dropdown is closed",
                     lambda: full.property("searchBoxShown") is True and full.property("searchCollapsed") is False
                     and full.property("searchDropdownOpen") is False)),
        (0, lambda: d.key(Qt.Key.Key_F, CTRL), None),
        (200, None, (f"{tag}: Ctrl+F focuses the box", lambda: full.property("searchFocused") is True)),
        (0, lambda: d.type_text("re"), None),
        (300, None, (f"{tag}: typing opens the dropdown and each group lists its matches (notes, tasks, reminders, files)",
                     lambda: full.property("searchDropdownOpen") is True and counts() == expected("re")
                     and all(shown()[k] == min(5, counts()[k]) for k in counts()) and counts()["tasks"] > 5)),
        (0, lambda: d.type_text("n"), None),
        (300, None, (f"{tag}: typing more narrows every group ('ren')",
                     lambda: counts() == expected("ren") and sum(counts().values()) < sum(expected("re").values()))),
        (0, lambda: retype("a"), None),
        (300, None, (f"{tag}: the query 'a' matches all four groups (up to 5 shown each, 'Show all' rows for the rest)",
                     lambda: counts() == expected("a") and all(counts()[k] > 0 for k in counts())
                     and all(shown()[k] == min(5, counts()[k]) for k in counts())
                     and int(full.property("searchNavCount")) == sum(shown().values()) + sum(1 for k in counts() if counts()[k] > 5))),
        (0, lambda: retype("zzzq"), None),
        (300, None, (f"{tag}: a query without matches shows 'No matches' (no entries)",
                     lambda: full.property("searchDropdownOpen") is True and sum(counts().values()) == 0 and full.property("searchNavCount") == 0)),
        # keyboard navigation
        (0, lambda: retype("re"), None),
        (300, None, (f"{tag}: the first result is highlighted (the first pinned note)",
                     lambda: full.property("searchCurrent") == 0 and current() == first_note_title())),
        (0, lambda: d.key(down), None),
        (100, None, (f"{tag}: Down moves to the next row", lambda: full.property("searchCurrent") == 1)),
        (0, lambda: d.key(up), None),
        (100, None, (f"{tag}: Up moves back", lambda: full.property("searchCurrent") == 0)),
        (0, lambda: d.key(up), None),
        (100, None, (f"{tag}: Up from the first row wraps to the last",
                     lambda: full.property("searchCurrent") == int(full.property("searchNavCount")) - 1)),
        (0, lambda: d.key(down), None),
        (100, None, (f"{tag}: ...and Down from the last wraps to the first", lambda: full.property("searchCurrent") == 0)),
        # opening results
        (0, lambda: memo.update(note_title=current()), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (600, None, (f"{tag}: Enter on a note opens the Notes section and that note's editor, and clears the search",
                     lambda: full.property("section") == "notes" and full.property("editorOpen") is True
                     and note_title_of(full.property("editorNoteId")) == memo["note_title"]
                     and full.property("searchText") == "" and full.property("searchDropdownOpen") is False)),
        (0, esc, None),
        (600, pick_task, None),
        (0, lambda: open_box_and_type(memo["task"]["title"]), None),
        (300, lambda: nav_to(memo["task"]["title"]), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (600, None, (f"{tag}: Enter on a task opens the Tasks section and its pop-out",
                     lambda: full.property("section") == "tasks" and full.property("taskEditorOpen") is True
                     and d.task(full.property("taskEditorId"))["title"] == memo["task"]["title"])),
        (0, esc, None),
        (600, pick_reminder, None),
        (0, lambda: open_box_and_type(memo["reminder"]["title"]), None),
        (300, lambda: nav_to(memo["reminder"]["title"]), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (600, None, (f"{tag}: Enter on a reminder opens the Reminders section and its pop-out",
                     lambda: full.property("section") == "reminders" and full.property("reminderEditorOpen") is True
                     and d.reminder(full.property("reminderEditorId"))["title"] == memo["reminder"]["title"])),
        (0, esc, None),
        (600, lambda: (open_box_and_type("kinemat"), memo.update(launched=len(library.launched))), None),
        (300, lambda: nav_to("Chapter 3"), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (400, None, (f"{tag}: Enter on a file opens it with Library.open (stubbed) and the Full App stays",
                     lambda: library.launched[memo["launched"]:] == [("open", "Study/Physics/Chapter 3 - Kinematics.pdf")]
                     and controller.full_visible())),
        (0, lambda: open_box_and_type("physics"), None),
        (300, lambda: nav_to("Physics"), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (500, None, (f"{tag}: Enter on a folder opens the Files section at that folder",
                     lambda: full.property("section") == "files" and full.property("filesRel") == "Study/Physics")),
        (0, lambda: (full.setProperty("section", "reminders"), open_box_and_type("re")), None),
        (300, lambda: nav_to("Show all"), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (400, None, (f"{tag}: 'Show all in Notes' switches to the Notes section", lambda: full.property("section") == "notes")),
        (0, lambda: open_box_and_type("review"), None),
        (300, None, None),
        (0, lambda: d.click("search.row", 0), None),
        (500, None, (f"{tag}: clicking a result row opens it too (the first one: a note)",
                     lambda: full.property("editorOpen") is True and full.property("searchDropdownOpen") is False)),
        (0, esc, None),
        # Esc
        (600, lambda: open_box_and_type("abc"), None),
        (200, None, (f"{tag}: the dropdown is open for 'abc'", lambda: full.property("searchDropdownOpen") is True)),
        (0, esc, None),
        (200, None, (f"{tag}: Esc clears the text and closes the dropdown (the box keeps the focus)",
                     lambda: full.property("searchText") == "" and full.property("searchDropdownOpen") is False
                     and full.property("searchFocused") is True)),
        (0, esc, None),
        (200, None, (f"{tag}: a second Esc lets go of the box", lambda: full.property("searchFocused") is False)),
        # click outside
        (0, lambda: open_box_and_type("re"), None),
        (300, None, (f"{tag}: dropdown open again for the click-outside check", lambda: full.property("searchDropdownOpen") is True)),
        (0, lambda: d.click("noteOutsideSearch"), None),
        (300, None, (f"{tag}: click-through: clicking outside the dropdown over a note closes it and does not open that note",
                     lambda: full.property("searchDropdownOpen") is False and full.property("editorOpen") is False
                     and full.property("searchText") == "re")),
        (0, lambda: d.click("search.box"), None),
        (300, None, (f"{tag}: clicking the box (which the click-away layer leaves alone) opens the dropdown again with the text kept",
                     lambda: full.property("searchDropdownOpen") is True and full.property("searchFocused") is True)),
        (0, lambda: (esc(), esc(), esc()), None),
        (500, None, None),
        # the title bar still works around the box
        (0, lambda: dclick("titleBlank"), None),
        (700, None, (f"{tag}: a double-click on the title bar beside the box still maximizes the window",
                     lambda: full.visibility() == QWindow.Visibility.Maximized)),
        (0, lambda: dclick("titleBlank"), None),
        (900, None, (f"{tag}: ...and a second one restores it", lambda: full.visibility() == QWindow.Visibility.Windowed)),
        (0, lambda: dclick("search.box"), None),
        (700, None, (f"{tag}: a double-click inside the box does not reach the title bar (no maximize) and focuses the box",
                     lambda: full.visibility() == QWindow.Visibility.Windowed and full.property("searchFocused") is True)),
        (0, esc, None),
        # narrow window: an icon
        (0, narrow_window, None),
        (500, None, (f"{tag}: below 900 px the search collapses to an icon",
                     lambda: full.property("searchCollapsed") is True and full.property("searchBoxShown") is False)),
        (0, lambda: d.click("search.box"), None),
        (400, None, (f"{tag}: clicking the icon opens the box with the focus",
                     lambda: full.property("searchBoxShown") is True and full.property("searchFocused") is True)),
        (0, lambda: d.type_text("pr"), None),
        (300, None, (f"{tag}: the narrow box shows the dropdown too", lambda: full.property("searchDropdownOpen") is True)),
        (0, lambda: (esc(), esc()), None),
        (300, None, (f"{tag}: Esc twice clears it and folds it back into an icon",
                     lambda: full.property("searchBoxShown") is False and full.property("searchText") == "")),
        (0, lambda: d.key(Qt.Key.Key_F, CTRL), None),
        (300, None, (f"{tag}: Ctrl+F opens the icon's box too", lambda: full.property("searchBoxShown") is True and full.property("searchFocused") is True)),
        (0, esc, None),
        (0, reset_window, None),
        (400, None, None),
        (0, controller.hide_full, None),
    ]


# ── The overlay's greeting and clock ─────────────────────────────────


def greeting_steps(controller) -> list[Step]:  # noqa: ANN001
    overlay, full, store = controller.overlay, controller.full, controller.store
    tag = "overlay greeting"

    def at(hour: int, minute: int = 0) -> int:
        return local_ms(2026, 11, 3, hour, minute)

    def greeting_unit(hour: int, name) -> str:  # noqa: ANN001
        return json.loads(full.datesCall("greeting", json.dumps([hour, name])))

    def pin(hour: int, minute: int = 0) -> None:
        store.setProperty("clockOverride", at(hour, minute))

    def text() -> str:
        return str(overlay.property("greetingTextShown"))

    def clock() -> tuple[str, str]:
        return str(overlay.property("clockTextShown")), str(overlay.property("clockSuffixShown"))

    def boundary_texts() -> list[str]:
        out = []
        for hour, minute in ((0, 0), (4, 59), (5, 0), (11, 59), (12, 0), (16, 59), (17, 0), (21, 59), (22, 0), (23, 59)):
            pin(hour, minute)
            out.append(text())
        return out

    def clock_forms() -> list[tuple[str, str]]:
        out = []
        for twelve, hour, minute in ((False, 14, 5), (True, 14, 5), (True, 0, 7), (True, 9, 41), (False, 9, 41), (True, 12, 30), (False, 0, 0)):
            store.setProperty("twelveHour", twelve)
            pin(hour, minute)
            out.append(clock())
        return out

    def geometry() -> dict:
        return json.loads(str(overlay.cornerGeometry()))

    def aligned() -> bool:
        g = geometry()
        greeting, clock_box, search, base = g["greeting"], g["clock"], g["search"], g["base"]
        middle = search["y"] + search["height"] / 2
        return (
            abs(greeting["x"] - base["x"]) < 1.5
            and abs(clock_box["x"] + clock_box["width"] - (base["x"] + base["width"])) < 1.5
            and abs(greeting["y"] + greeting["height"] / 2 - middle) < 1.5
            and abs(clock_box["y"] + clock_box["height"] / 2 - middle) < 1.5
        )

    def opacities() -> tuple[float, float]:
        return float(overlay.property("greetingOpacity")), float(overlay.property("clockOpacity"))

    def restore() -> None:
        store.setProperty("clockOverride", 0)
        store.setProperty("twelveHour", False)
        store.setProperty("userName", "")

    return [
        (0, lambda: store.setProperty("userName", ""), None),
        (0, None, (f"{tag}: the greeting word is chosen by the hour (05:00 morning, 12:00 afternoon, 17:00 evening, 22:00 night)",
                   lambda: [greeting_unit(h, "") for h in (0, 4, 5, 11, 12, 16, 17, 21, 22, 23)]
                   == ["Good night", "Good night", "Good morning", "Good morning", "Good afternoon", "Good afternoon",
                       "Good evening", "Good evening", "Good night", "Good night"])),
        (0, None, (f"{tag}: ...with ', Name' appended when a name is set (blank or missing: nothing)",
                   lambda: greeting_unit(9, "Riya") == "Good morning, Riya" and greeting_unit(9, "  Riya ") == "Good morning, Riya"
                   and greeting_unit(9, "   ") == "Good morning" and greeting_unit(9, None) == "Good morning")),
        (0, None, (f"{tag}: the Windows format string decides 12h or 24h ('h:mm AP', 'hh:mm:ss ap', 'h:mm tt'-style lowercase h: 12-hour; 'HH:mm', 'H:mm': 24-hour)",
                   lambda: all(clock_is_12h(f) for f in ("h:mm AP", "hh:mm:ss ap", "h:mm", "h:mm:ss AP"))
                   and not any(clock_is_12h(f) for f in ("HH:mm", "H:mm", "HH:mm:ss", "")))),
        (0, controller.open_overlay, None),
        (700, None, (f"{tag}: the overlay shows the greeting and the clock (faded in, calm 0.9 opacity)",
                     lambda: opacities()[0] > 0.85 and opacities()[1] > 0.85 and text().startswith("Good ") and clock()[0] != "")),
        (0, None, (f"{tag}: the greeting lines up with the panel group's left edge, the clock with its right edge, both centered on the search bar",
                   aligned)),
        (0, None, (f"{tag}: with an injected clock the greeting follows the boundary times "
                   "(00:00 and 04:59 night, 05:00 and 11:59 morning, 12:00 and 16:59 afternoon, 17:00 and 21:59 evening, 22:00 and 23:59 night)",
                   lambda: boundary_texts() == ["Good night", "Good night", "Good morning", "Good morning", "Good afternoon",
                                                "Good afternoon", "Good evening", "Good evening", "Good night", "Good night"])),
        (0, lambda: (pin(9, 0), store.setProperty("userName", "Riya")), None),
        (100, None, (f"{tag}: a name set in the settings is appended ('Good morning, Riya')", lambda: text() == "Good morning, Riya")),
        (0, lambda: store.setProperty("userName", ""), None),
        (100, None, (f"{tag}: a blank name leaves the plain greeting ('Good morning')", lambda: text() == "Good morning")),
        (0, None, (f"{tag}: the clock follows the 12h/24h setting: 24h 14:05; 12h 2:05 PM, 12:07 AM, 9:41 AM, 12:30 PM; 24h 09:41, 00:00",
                   lambda: clock_forms() == [("14:05", ""), ("2:05", "PM"), ("12:07", "AM"), ("9:41", "AM"), ("09:41", ""),
                                             ("12:30", "PM"), ("00:00", "")])),
        (0, lambda: store.tickClock(), None),
        (0, None, (f"{tag}: the clock timer re-aims at the next minute boundary after every tick",
                   lambda: abs(int(store.property("clockInterval")) - (60000 - now_ms() % 60000 + 30)) < 150
                   and 30 <= int(store.property("clockInterval")) <= 60030)),
        (0, lambda: (pin(9, 41), overlay.demo("layout-drag-swap")), None),
        (500, None, (f"{tag}: while a panel is being dragged (the snap bar shows) the greeting and the clock fade out",
                     lambda: overlay.property("snapBarShown") is True and opacities()[0] < 0.05 and opacities()[1] < 0.05)),
        (0, lambda: overlay.cancelDrag(), None),
        (600, None, (f"{tag}: ...and they come back after the drag", lambda: opacities()[0] > 0.85 and opacities()[1] > 0.85)),
        (0, lambda: overlay.setWidth(900), None),
        (500, None, (f"{tag}: on a narrow screen (900 px) the greeting is hidden first, the clock stays",
                     lambda: opacities()[0] == 0.0 and opacities()[1] > 0.85)),
        (0, lambda: overlay.setWidth(640), None),
        (500, None, (f"{tag}: on a very narrow one (640 px) the clock goes too (nothing touches the search bar)",
                     lambda: opacities()[0] == 0.0 and opacities()[1] == 0.0)),
        (0, restore, None),
        (0, controller.close_overlay, None),
    ]


# ── The first-launch tour ────────────────────────────────────────────


def tour_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store, overlay = d.full, d.store, controller.overlay
    memo: dict = {}
    tag = "tour"

    def ini(key: str):  # noqa: ANN202
        """A fresh read of the settings file, as a restarted app would see it."""
        from PySide6.QtCore import QSettings  # noqa: PLC0415

        value = QSettings(str(controller.settings_path), QSettings.Format.IniFormat).value(f"app/{key}")
        return str(value).lower() if isinstance(value, bool) else value

    def show_tour() -> None:
        store.setProperty("userName", "")
        store.setProperty("tourDone", False)

    def active() -> bool:
        return full.property("tourActive") is True

    def step() -> int:
        return int(full.property("tourStep"))

    def drawer_closed_and_nothing_open() -> bool:
        return (full.property("drawerOpen") is False and full.property("editorOpen") is False
                and full.property("taskEditorOpen") is False and full.property("reminderEditorOpen") is False)

    def set_name_text(text: str) -> None:
        d.click("tour.name")
        d.key(Qt.Key.Key_A, CTRL)
        d.type_text(text)

    return [
        (0, lambda: controller.show_full("notes"), None),
        (500, None, (f"{tag}: under the self-test the tour is done by default (the setting defaults to true) and does not show",
                     lambda: store.property("tourDone") is True and not active() and full.property("tourVisible") is False)),
        (0, show_tour, None),
        (600, None, (f"{tag}: with tourDone false the Full App shows the tour (Welcome, 'Step 1 of 3', 'Next ›')",
                     lambda: active() and full.property("tourVisible") is True and step() == 0
                     and full.property("tourStepLabel") == "Step 1 of 3" and full.property("tourNextLabel") == "Next ›")),
        (0, None, (f"{tag}: the welcome step quotes the real current hotkey ('Ctrl + Alt + Space')",
                   lambda: "Ctrl + Alt + Space" in str(full.property("tourIntroText")))),
        (0, lambda: store.setProperty("hotkey", "Ctrl+Shift+K"), None),
        (100, None, (f"{tag}: ...and follows a changed hotkey",
                     lambda: "Ctrl + Shift + K" in str(full.property("tourIntroText")))),
        (0, lambda: store.setProperty("hotkey", "Ctrl+Alt+Space"), None),
        # modal: the scrim and the keyboard
        (0, lambda: d.click("menu"), None),
        (300, None, (f"{tag}: click-through: clicking the scrim over the hamburger does nothing (the tour is modal)",
                     lambda: active() and drawer_closed_and_nothing_open())),
        (0, lambda: d.click("note", 0), None),
        (300, None, (f"{tag}: click-through: clicking the scrim over a note opens nothing and does not close the tour",
                     lambda: active() and drawer_closed_and_nothing_open() and step() == 0)),
        (0, lambda: (memo.update(flags=[t["done"] for t in d.tasks()]), d.click_at(d.at(1032, 288))), None),
        (200, None, (f"{tag}: clicking the scrim over a rail checkbox does not tick the task",
                     lambda: active() and drawer_closed_and_nothing_open() and step() == 0
                     and [t["done"] for t in d.tasks()] == memo["flags"])),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (200, None, (f"{tag}: Esc does not close the tour (only Skip does)", lambda: active() and step() == 0)),
        (0, lambda: d.key(Qt.Key.Key_F, CTRL), None),
        (200, None, (f"{tag}: Ctrl+F is off while the tour shows", lambda: full.property("searchFocused") is False)),
        # name and Next
        (0, lambda: set_name_text("Riya"), None),
        (100, None, (f"{tag}: the name box takes typing", lambda: full.property("tourNameText") == "Riya")),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (400, None, (f"{tag}: Enter in the name box is Next (Step 2 of 3)",
                     lambda: step() == 1 and full.property("tourStepLabel") == "Step 2 of 3")),
        (0, lambda: d.click("tour.next"), None),
        (400, None, (f"{tag}: the Next button goes to step 3, whose button reads 'Get started'",
                     lambda: step() == 2 and full.property("tourNextLabel") == "Get started" and full.property("tourStepLabel") == "Step 3 of 3")),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (800, None, (f"{tag}: Enter on the last step is 'Get started': the tour finishes, tourDone is saved, and the name is saved as userName",
                     lambda: not active() and full.property("tourVisible") is False and store.property("userName") == "Riya"
                     and ini("tourDone") == "true" and ini("userName") == "Riya")),
        # the greeting uses the saved name
        (0, lambda: (controller.hide_full(), store.setProperty("clockOverride", local_ms(2026, 11, 3, 9, 0))), None),
        (200, controller.open_overlay, None),
        (700, None, (f"{tag}: the overlay greets the saved name ('Good morning, Riya')",
                     lambda: overlay.property("greetingTextShown") == "Good morning, Riya")),
        (0, lambda: (controller.close_overlay(), store.setProperty("clockOverride", 0)), None),
        # Skip
        (500, lambda: controller.show_full("notes"), None),
        (600, show_tour, None),
        (600, None, (f"{tag}: showing the tour again starts at step 1", lambda: active() and step() == 0)),
        (0, lambda: d.click("tour.next"), None),
        (300, None, None),
        (0, lambda: d.click("tour.skip"), None),
        (700, None, (f"{tag}: Skip (from step 2) closes the tour and sets tourDone",
                     lambda: not active() and full.property("tourVisible") is False and ini("tourDone") == "true")),
        (0, show_tour, None),
        (600, lambda: set_name_text("Zed"), None),
        (0, lambda: d.click("tour.skip"), None),
        (700, None, (f"{tag}: Skip on step 1 keeps a typed name ('Zed')", lambda: not active() and store.property("userName") == "Zed")),
        (0, lambda: store.setProperty("tourDone", False), None),
        (600, lambda: (d.click("tour.name"), d.key(Qt.Key.Key_A, CTRL), d.key(Qt.Key.Key_Delete)), None),
        (0, lambda: d.click("tour.skip"), None),
        (700, None, (f"{tag}: Skip with an empty name box leaves the saved name alone",
                     lambda: not active() and store.property("userName") == "Zed")),
        # Change hotkey
        (0, show_tour, None),
        (600, lambda: d.click("tour.change"), None),
        (700, None, (f"{tag}: 'Change hotkey' finishes the tour and lands on the Settings hotkey recorder (listening for keys)",
                     lambda: not active() and full.property("section") == "settings" and full.property("hotkeyRecording") is True)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (300, None, (f"{tag}: Esc cancels the recording", lambda: full.property("hotkeyRecording") is False)),
        # Settings: name and "Show the welcome tour again"
        (0, lambda: d.click("settings.nameBox"), None),
        (200, lambda: (d.key(Qt.Key.Key_A, CTRL), d.type_text("Mira")), None),
        (900, None, (f"{tag}: the Settings 'Your name' box saves live (the greeting follows)",
                     lambda: store.property("userName") == "Mira" and ini("userName") == "Mira")),
        (0, lambda: d.click("settings.tourAgain"), None),
        (600, None, (f"{tag}: 'Show the welcome tour again' sets tourDone=false and shows the tour (name pre-filled)",
                     lambda: active() and step() == 0 and store.property("tourDone") is False
                     and full.property("tourNameText") == "Mira" and ini("tourDone") == "false")),
        (0, lambda: d.click("tour.skip"), None),
        (700, None, (f"{tag}: ...and Skip closes it again", lambda: not active() and store.property("tourDone") is True)),
        (0, lambda: store.setProperty("userName", ""), None),
        (0, lambda: full.setProperty("section", "notes"), None),
        (0, controller.hide_full, None),
    ]


def _seed_ok(d: Driver) -> bool:
    by_title = {r["title"]: r for r in d.reminders() if r["id"] in (10, 11, 12)}
    water, gym, rent = by_title.get("Drink water"), by_title.get("Gym"), by_title.get("Pay rent")
    if not (water and gym and rent):
        return False
    gym_due, rent_due = datetime.fromtimestamp(gym["due"] / 1000), datetime.fromtimestamp(rent["due"] / 1000)
    return (
        water["repeat"] == {"kind": "every", "n": 2, "unit": "hours"} and water["due"] > now_ms()
        and gym["repeat"] == {"kind": "weekly", "days": [1, 3, 5]} and (gym_due.hour, gym_due.minute) == (18, 0)
        and js_weekday(gym_due.date()) in (1, 3, 5) and gym["due"] > now_ms()
        and rent["repeat"] == {"kind": "monthly", "day": 1} and (rent_due.hour, rent_due.minute) == (9, 0)
        and rent_due.day == 1 and rent["due"] > now_ms()
        and (len(water["history"]), len(gym["history"]), len(rent["history"])) == (5, 3, 5)
    )

