"""Checks of the QML helper libraries (smartparse.js, dates.js, recurrence.js, layout.js).

They call the real files through helper functions on the windows (``smartCall``, ``datesCall``,
``recurCall``, ``layoutCall``), so the code that runs in the app is the code under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from panelo.ui.selftest.driver import local_ms


def smartparse_and_dates_checks(check: Callable[[str, bool], None], full) -> None:  # noqa: ANN001
    now = local_ms(2026, 11, 4, 10, 0)   # a Wednesday, 10:00

    def parse(text: str, at: int = now) -> dict:
        return json.loads(full.smartCall(text, at))

    def dates(fn: str, *args):  # noqa: ANN202
        return json.loads(full.datesCall(fn, json.dumps(list(args))))

    def due(text: str) -> int | None:
        return parse(text)["due"]

    p = parse("Call mom tomorrow 5pm !high")
    check(
        "smartparse: 'Call mom tomorrow 5pm !high' gives title, tomorrow 17:00 and priority high",
        p["title"] == "Call mom" and p["due"] == local_ms(2026, 11, 5, 17) and p["hasTime"] is True
        and p["priority"] == "high",
    )
    pills = p["pills"]
    check(
        "smartparse: 'tomorrow 5pm' is one date pill and '!high' a priority pill",
        len(pills) == 2 and pills[0]["kind"] == "date" and pills[0]["start"] == 9 and pills[0]["end"] == 21
        and pills[1]["kind"] == "priority" and pills[1]["value"] == "high",
    )
    check(
        "smartparse: today (date only), tonight (20:00)",
        due("Pay rent today") == local_ms(2026, 11, 4) and parse("Pay rent today")["hasTime"] is False
        and due("Movie tonight") == local_ms(2026, 11, 4, 20) and parse("Movie tonight")["hasTime"] is True,
    )
    check(
        "smartparse: weekday names mean the next one ('mon', 'monday', and 'wed' on a Wednesday)",
        due("Standup mon") == local_ms(2026, 11, 9) and due("Standup monday") == local_ms(2026, 11, 9)
        and due("Gym fri") == local_ms(2026, 11, 6) and due("Review wed") == local_ms(2026, 11, 11),
    )
    check(
        "smartparse: next week is the next Monday; in 3 days; in 2 h; in 30 min",
        due("Plan next week") == local_ms(2026, 11, 9) and due("Plan in 3 days") == local_ms(2026, 11, 7)
        and due("Stretch in 2 h") == now + 2 * 3600000 and due("Eggs in 30 min") == now + 30 * 60000
        and parse("Stretch in 2 hours")["due"] == now + 2 * 3600000,
    )
    check(
        "smartparse: dates 'nov 5', '5 nov', '5/11' (day/month), a past date goes to next year",
        due("A nov 5") == local_ms(2026, 11, 5) and due("B 5 nov") == local_ms(2026, 11, 5)
        and due("C 5/11") == local_ms(2026, 11, 5) and due("D dec 1") == local_ms(2026, 12, 1)
        and due("E jan 3") == local_ms(2027, 1, 3) and due("F on 1st of november") == local_ms(2027, 11, 1),
    )
    check(
        "smartparse: times '5pm', '5:30pm', '17:00', 'at 9' (tomorrow 09:00 once past), 'at 11'",
        due("A 5pm") == local_ms(2026, 11, 4, 17) and due("B 5:30pm") == local_ms(2026, 11, 4, 17, 30)
        and due("C 17:00") == local_ms(2026, 11, 4, 17) and due("D at 9") == local_ms(2026, 11, 5, 9)
        and due("E at 11") == local_ms(2026, 11, 4, 11) and due("F 9am") == local_ms(2026, 11, 5, 9)
        and due("G 12am") == local_ms(2026, 11, 5, 0),
    )
    check(
        "smartparse: a date and a time together ('fri 3pm', 'nov 20 at 9:30', 'tomorrow at 9')",
        due("X fri 3pm") == local_ms(2026, 11, 6, 15) and due("X nov 20 at 9:30") == local_ms(2026, 11, 20, 9, 30)
        and due("X tomorrow at 9") == local_ms(2026, 11, 5, 9),
    )
    check(
        "smartparse: priorities !low !high !urgent !! (high) !!! (urgent), removed from the title",
        parse("a !low")["priority"] == "low" and parse("a !high")["priority"] == "high"
        and parse("a !urgent")["priority"] == "urgent" and parse("a !!")["priority"] == "high"
        and parse("a !!!")["priority"] == "urgent" and parse("a !!!")["title"] == "a"
        and parse("!high a b")["title"] == "a b",
    )
    plain = parse("Buy #home milk")
    check(
        "smartparse: unparsed text is the title, '#word' stays in it, nothing recognized gives no date",
        plain["title"] == "Buy #home milk" and plain["due"] is None and plain["priority"] is None
        and plain["pills"] == [] and parse("tomorrow")["title"] == "",
    )

    # grouping across midnight and week boundaries
    sun_late = local_ms(2026, 11, 8, 23, 59)
    check(
        "dates: grouping just before midnight: Sunday 23:59 is today, Monday 00:01 is tomorrow, Saturday is overdue",
        dates("groupOf", local_ms(2026, 11, 8, 0, 0), sun_late) == "today"
        and dates("groupOf", local_ms(2026, 11, 9, 0, 1), sun_late) == "tomorrow"
        and dates("groupOf", local_ms(2026, 11, 7, 23, 59), sun_late) == "overdue",
    )
    midnight = local_ms(2026, 11, 9, 0, 0)
    check(
        "dates: grouping just after midnight: the same Monday-00:01 task is now today",
        dates("groupOf", local_ms(2026, 11, 9, 0, 1), midnight) == "today"
        and dates("groupOf", local_ms(2026, 11, 8, 23, 59), midnight) == "overdue",
    )
    check(
        "dates: week boundary: +2..+7 days is 'week', +8 days is 'later', no date is 'none'",
        dates("groupOf", local_ms(2026, 11, 6, 9), now) == "week" and dates("groupOf", local_ms(2026, 11, 11, 9), now) == "week"
        and dates("groupOf", local_ms(2026, 11, 12, 9), now) == "later" and dates("groupOf", None, now) == "none"
        and dates("groupOf", local_ms(2026, 11, 5, 23, 59), now) == "tomorrow",
    )
    check(
        "dates: relative() and formatDue()",
        dates("relative", now + 52 * 60000, now) == "in 52 min" and dates("relative", now - 2 * 3600000, now) == "2 h ago"
        and dates("relative", local_ms(2026, 11, 5, 9), now) == "in 23 h"
        and dates("relative", local_ms(2026, 11, 6, 9), now) == "in 2 days"
        and dates("formatDue", local_ms(2026, 11, 4, 17), True, now) == "17:00"
        and dates("formatDue", local_ms(2026, 11, 5, 9, 30), True, now) == "09:30"
        and dates("formatDue", local_ms(2026, 11, 9, 9, 30), True, now) == "Mon 09:30"
        and dates("formatDue", local_ms(2026, 11, 9), False, now) == "Mon"
        and dates("formatDue", local_ms(2026, 11, 20), False, now) == "Nov 20",
    )
    moved = dates("moveToGroup", local_ms(2026, 11, 4, 17), True, "tomorrow", now)
    later = dates("moveToGroup", local_ms(2026, 11, 4), False, "later", now)
    check(
        "dates: moveToGroup keeps the time, Later is +8 days, No date clears, Overdue is refused",
        moved["due"] == local_ms(2026, 11, 5, 17) and moved["hasTime"] is True
        and later["due"] == local_ms(2026, 11, 12) and later["hasTime"] is False
        and dates("moveToGroup", local_ms(2026, 11, 4, 17), True, "none", now)["due"] is None
        and dates("moveToGroup", local_ms(2026, 11, 4, 17), True, "overdue", now) is None
        and dates("moveToGroup", local_ms(2026, 11, 4, 17), True, "week", now)["due"] == local_ms(2026, 11, 6, 17),
    )


def recurrence_checks(check: Callable[[str, bool], None], full) -> None:  # noqa: ANN001
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


PANELS = ["notes", "todo", "files", "timers", "reminders"]
RECT = {"x": 0, "y": 0, "width": 1632, "height": 918}
GAP = 10
MIN_W, MIN_H = 220, 160


def close(a: dict, b: dict, eps: float = 1.0) -> bool:
    return all(abs(a[k] - b[k]) <= eps for k in ("x", "y", "width", "height"))


def layout_checks(check: Callable[[str, bool], None], overlay) -> None:  # noqa: ANN001
    def call(fn: str, *args):  # noqa: ANN202
        return json.loads(overlay.layoutCall(fn, json.dumps(list(args))))

    def rects_of(tree: dict, rect: dict = RECT) -> dict:
        return call("computeRects", tree, rect, GAP)["rects"]

    default = call("defaultTree")
    check("layout.js: the default tree is valid", call("validate", default) is True)

    # The old fixed layout, as the formulas it used.
    avail = RECT["width"] - 2 * GAP
    w_left, w_mid = avail * 0.30, avail * 0.40
    w_right = avail - w_left - w_mid
    col_h = RECT["height"] - GAP
    todo_h, timers_h = col_h * 0.38, col_h * 0.5
    mid_x, right_x = w_left + GAP, w_left + w_mid + 2 * GAP
    old = {
        "notes": {"x": 0, "y": 0, "width": w_left, "height": RECT["height"]},
        "todo": {"x": mid_x, "y": 0, "width": w_mid, "height": todo_h},
        "files": {"x": mid_x, "y": todo_h + GAP, "width": w_mid, "height": col_h - todo_h},
        "timers": {"x": right_x, "y": 0, "width": w_right, "height": timers_h},
        "reminders": {"x": right_x, "y": timers_h + GAP, "width": w_right, "height": col_h - timers_h},
    }
    now = rects_of(default)
    check("layout.js: default rects match the old fixed layout within 1px",
          all(close(now[k], old[k]) for k in PANELS))

    swapped = rects_of(call("swap", default, "notes", "timers"))
    check("layout.js: swap exchanges two panels' rectangles",
          close(swapped["notes"], now["timers"]) and close(swapped["timers"], now["notes"])
          and close(swapped["todo"], now["todo"]))

    left = call("splitAt", default, "files", "notes", "left")
    r = rects_of(left)
    left_ok = (call("validate", left) and r["notes"]["x"] < r["files"]["x"]
               and abs(r["notes"]["y"] - r["files"]["y"]) < 1
               and abs(r["notes"]["height"] - r["files"]["height"]) < 1
               and abs(r["notes"]["width"] - r["files"]["width"]) < 1)
    top = call("splitAt", default, "timers", "reminders", "top")
    r = rects_of(top)
    top_ok = (call("validate", top) and r["reminders"]["y"] < r["timers"]["y"]
              and abs(r["reminders"]["x"] - r["timers"]["x"]) < 1
              and abs(r["reminders"]["height"] - r["timers"]["height"]) < 1)
    right = call("splitAt", default, "files", "todo", "right")
    r = rects_of(right)
    right_ok = (call("validate", right) and r["todo"]["x"] >= r["files"]["x"] + r["files"]["width"]
                and abs(r["todo"]["y"] - r["files"]["y"]) < 1 and r["files"]["height"] > 900)
    bottom = call("splitAt", default, "timers", "notes", "bottom")
    r = rects_of(bottom)
    bottom_ok = (call("validate", bottom) and r["notes"]["y"] > r["timers"]["y"]
                 and r["notes"]["y"] < r["reminders"]["y"]
                 and abs(r["notes"]["x"] - r["timers"]["x"]) < 1)
    check("layout.js: splitAt left (nests in a row) and top (nests in a column)", left_ok and top_ok)
    check("layout.js: splitAt right and bottom, where the parent already runs that way, insert a sibling",
          right_ok and bottom_ok
          and right["sizes"] == [0.3, 0.2, 0.2, 0.3] and len(right["children"]) == 4
          and bottom["children"][1]["sizes"] == [0.25, 0.25, 0.5]
          and [c["panel"] for c in bottom["children"][1]["children"]] == ["timers", "notes", "reminders"])
    check("layout.js: splitAt onto itself or with a bad side is refused",
          call("splitAt", default, "notes", "notes", "left") is None
          and call("splitAt", default, "files", "notes", "middle") is None)

    no_todo = call("remove", default, "todo")
    no_mid = call("remove", no_todo, "files")
    check("layout.js: remove collapses single-child containers (and merges same-direction ones)",
          no_todo["children"][1] == {"panel": "files"} and len(no_mid["children"]) == 2
          and no_mid["dir"] == "row" and abs(sum(no_mid["sizes"]) - 1) < 1e-9)

    order = list(PANELS)
    templates_ok = True
    for name in ("sketch", "focus", "rows", "columns"):
        slots = call("templateSlotRects", name, RECT, GAP)
        tree = call("applyTemplate", name, order)
        placed = rects_of(tree)
        templates_ok = (templates_ok and len(slots) == 5 and call("validate", tree)
                        and all(s["width"] > 0 and s["height"] > 0 for s in slots)
                        and all(close(placed[order[i]], slots[i], 0.01) for i in range(5)))
    check("layout.js: each template has 5 slots and applyTemplate puts panel k into slot k", templates_ok)

    rank = call("rankByArea", now)
    check("layout.js: rankByArea ranks the default layout Notes, Files, To-Do, Timers, Reminders",
          rank == ["notes", "files", "todo", "timers", "reminders"])
    shuffled = ["reminders", "notes", "files", "todo", "timers"]
    again = call("applyTemplate", "columns", shuffled)
    placed = rects_of(again)
    check("layout.js: applying a template keeps the ranking (rank k goes to slot k)",
          [k for k, _ in sorted(placed.items(), key=lambda kv: kv[1]["x"])] == shuffled
          and call("orderForSlot", rank, "reminders", 1)[0] == "reminders"
          and call("orderForSlot", rank, "reminders", 1)[1:] == ["notes", "files", "todo", "timers"])
    tie = call("rankByArea", rects_of(call("applyTemplate", "columns", shuffled)))
    check("layout.js: equal areas tie-break in the order Notes, To-Do, Files, Timers, Reminders",
          tie == PANELS)

    grown = call("resize", default, [], 0, -0.5, RECT, GAP, MIN_W, MIN_H)
    r = rects_of(grown)
    shrunk = call("resize", default, [], 0, 0.9, RECT, GAP, MIN_W, MIN_H)
    r2 = rects_of(shrunk)
    inner = call("resize", default, [1], 0, -0.9, RECT, GAP, MIN_W, MIN_H)
    r3 = rects_of(inner)
    check("layout.js: resize clamps at the minimum size (both directions, nested containers too)",
          abs(r["notes"]["width"] - MIN_W) < 0.5 and abs(r2["todo"]["width"] - MIN_W) < 0.5
          and abs(r3["todo"]["height"] - MIN_H) < 0.5 and r3["files"]["height"] > MIN_H
          and abs(sum(grown["sizes"]) - 1) < 1e-9)

    columns = call("applyTemplate", "columns", order)
    check("layout.js: fits is false for five columns at 900px wide, true at 1632px",
          call("fits", columns, {"x": 0, "y": 0, "width": 900, "height": 600}, GAP, MIN_W, MIN_H) is False
          and call("fits", columns, RECT, GAP, MIN_W, MIN_H) is True)

    dup = {"dir": "row", "sizes": [0.5, 0.5], "children": [{"panel": "notes"}, {"panel": "notes"}]}
    custom = call("swap", default, "files", "todo")
    check("layout.js: invalid or unreadable saved JSON falls back to the default tree",
          call("parse", "{not json") == default and call("parse", "") == default
          and call("parse", json.dumps(dup)) == default and call("parse", json.dumps({"dir": "row"})) == default
          and call("parse", json.dumps(custom)) == custom)

