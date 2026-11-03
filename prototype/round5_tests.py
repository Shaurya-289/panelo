"""Self-test checks for round 5 (the Tasks, Reminders, Focus, Trash and Settings views).

`unit_checks` calls the real qml/Proto/smartparse.js and dates.js through the Full App's
`smartCall` / `datesCall` helpers, so the code that runs in the app is the code under test.
The `*_steps` functions drive the Full App with real QTest mouse and key events. Everything is
used by `main.py --selftest`; nothing here touches the real registry, database or Recycle Bin.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier

Step = tuple  # (delay ms, action | None, (name, predicate) | None)


def js(value):  # noqa: ANN001, ANN201
    return value.toVariant() if hasattr(value, "toVariant") else value


def local_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


def ms_of(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def day_at(offset: int, hour: int = 0, minute: int = 0) -> int:
    """Local ms of the day `offset` days from today at hour:minute."""
    base = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return ms_of(base + timedelta(days=offset))


class Driver:
    """Mouse and key helpers for one window (the Full App)."""

    def __init__(self, controller) -> None:  # noqa: ANN001
        self.controller = controller
        self.full = controller.full
        self.store = controller.store

    def at(self, x: float, y: float) -> QPoint:
        return QPoint(round(x), round(y))

    def probe(self, name: str, index=0) -> QPoint | None:  # noqa: ANN001
        value = js(self.full.probe(name, index))
        return None if value is None else self.at(value["x"], value["y"])

    def click(self, name: str, index=0, dx: float = 0, dy: float = 0) -> None:  # noqa: ANN001
        p = self.probe(name, index)
        assert p is not None, f"no probe point for {name} {index}"
        QTest.mouseClick(self.full, LEFT, NO_MOD, QPoint(p.x() + round(dx), p.y() + round(dy)))

    def click_at(self, p: QPoint) -> None:
        QTest.mouseClick(self.full, LEFT, NO_MOD, p)

    def press(self, p: QPoint) -> None:
        QTest.mousePress(self.full, LEFT, NO_MOD, p)

    def move(self, p: QPoint) -> None:
        QTest.mouseMove(self.full, p)

    def release(self, p: QPoint) -> None:
        QTest.mouseRelease(self.full, LEFT, NO_MOD, p)

    def type_text(self, text: str) -> None:
        for char in text:
            QTest.keyClick(self.full, char)

    def key(self, key, modifier=NO_MOD) -> None:  # noqa: ANN001
        QTest.keyClick(self.full, key, modifier)

    # store access
    def tasks(self) -> list[dict]:
        return js(self.store.property("tasks"))

    def task(self, task_id: int) -> dict:
        return next(t for t in self.tasks() if t["id"] == task_id)

    def reminders(self) -> list[dict]:
        return js(self.store.property("reminders"))

    def reminder(self, reminder_id: int) -> dict:
        return next(r for r in self.reminders() if r["id"] == reminder_id)

    def notes(self) -> list[dict]:
        return js(self.store.property("notes"))

    def group(self, name: str) -> list[dict]:
        return js(self.store.property("taskGroups"))[name]

    def rgroup(self, name: str) -> list[dict]:
        return js(self.store.property("reminderGroups"))[name]

    def prop(self, name: str):  # noqa: ANN201
        return self.full.property(name)


# ── Unit checks ──────────────────────────────────────────────────────


def unit_checks(check: Callable[[str, bool], None], full) -> None:  # noqa: ANN001
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


# ── Tasks ────────────────────────────────────────────────────────────


def tasks_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full = d.full
    memo: dict = {}
    tag = "tasks"

    def newest() -> dict:
        return max(d.tasks(), key=lambda t: t["id"])

    def tomorrow(hour: int, minute: int = 0) -> int:
        return day_at(1, hour, minute)

    def begin_drag(task_id: int) -> None:
        memo["from"] = d.probe("taskRow", task_id)
        d.press(memo["from"])
        d.move(d.at(memo["from"].x() + 6, memo["from"].y() + 4))   # inside the threshold
        d.move(d.at(memo["from"].x() + 40, memo["from"].y() + 30))  # past it

    def hold_over(group: str) -> Callable[[], None]:
        """Move over a group's header; when it is below the window, hold the row at the bottom edge
        so the list auto-scrolls first."""
        def go() -> None:
            p = d.probe("taskHeader", group)
            if p is None or p.y() > 770:
                p = d.at(memo["from"].x() + 40, 805)
            memo["to"] = p
            d.move(p)
        return go

    def drop() -> None:
        d.release(memo["to"])

    def drag(task_id: int, group: str) -> list[Step]:
        return [
            (0, lambda: full.scrollTasksTop(), None),
            (200, lambda: begin_drag(task_id), None),
            (350, hold_over(group), None),     # the other groups slide into place first
            (450, hold_over(group), None),
            (450, hold_over(group), None),
            (450, hold_over(group), None),
        ]

    def remember_orig(task_id: int) -> Callable[[], None]:
        return lambda: memo.update(orig=d.task(task_id))

    def click_row_left(task_id: int) -> None:
        p = d.probe("taskRow", task_id)
        d.click_at(d.at(p.x() - 420, p.y()))

    quick_text = "Call mom tomorrow 5pm !high"
    steps: list[Step] = [
        (0, lambda: controller.show_full("tasks"), None),
        (600, None, (f"{tag}: the Tasks page shows (section tasks) with its groups from the real dates",
                     lambda: full.property("section") == "tasks"
                     and len(d.group("overdue")) >= 1 and len(d.group("today")) >= 3
                     and len(d.group("tomorrow")) == 1 and len(d.group("week")) == 2
                     and len(d.group("later")) == 2 and len(d.group("none")) == 2)),
        # quick add
        (0, lambda: d.click("taskAdd"), None),
        (100, lambda: d.type_text(quick_text), None),
        (100, None, (f"{tag}: typing in the quick add shows the preview row 'Tomorrow 17:00 · High'",
                     lambda: "Tomorrow 17:00" in str(full.property("tasksPreview")) and "High" in str(full.property("tasksPreview")))),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (200, None, (f"{tag}: Enter creates 'Call mom', due tomorrow 17:00, priority high (parsed parts removed from the title)",
                     lambda: newest()["title"] == "Call mom" and newest()["due"] == tomorrow(17)
                     and newest()["hasTime"] is True and newest()["priority"] == "high"
                     and str(full.property("tasksPreview")) == "")),
        (0, lambda: memo.update(mom=newest()["id"]), None),
        (0, None, (f"{tag}: the new task is in the Tomorrow group", lambda: any(t["id"] == memo["mom"] for t in d.group("tomorrow")))),
        # ticking
        (0, lambda: d.click("taskCheck", 5), None),
        (300, None, (f"{tag}: ticking a task strikes it (done, doneAt set) and it stays in its group (Today)",
                     lambda: d.task(5)["done"] is True and d.task(5)["doneAt"] is not None
                     and any(t["id"] == 5 and t["done"] for t in d.group("today"))
                     and d.probe("taskRow", 5) is not None)),
        (0, lambda: d.click("taskCheck", 5), None),
        (300, None, (f"{tag}: ticking again reopens it", lambda: d.task(5)["done"] is False and d.task(5)["doneAt"] is None)),
        # drag Today -> Tomorrow (keeps the time), -> No date (clears), Overdue refused
        (0, remember_orig(3), None),
        *drag(3, "tomorrow"),
        (0, None, (f"{tag}: while dragging, the row lifts and the group under the cursor is the drop target",
                   lambda: bool(full.property("tasksDragging")))),
        (0, drop, None),
        (500, None, (f"{tag}: dragging 'Finish the quarterly report' from Today to Tomorrow moves its date and keeps 17:00",
                     lambda: d.task(3)["due"] == tomorrow(17) and d.task(3)["hasTime"] is True)),
        (0, None, (f"{tag}: ...it is now in the Tomorrow group and the drag is over",
                   lambda: any(t["id"] == 3 for t in d.group("tomorrow")) and not full.property("tasksDragging")
                   and full.property("tasksLastDrop") is True)),
        *drag(3, "none"),
        (0, drop, None),
        (500, None, (f"{tag}: dropping it on No date clears the due date",
                     lambda: d.task(3)["due"] is None and d.task(3)["hasTime"] is False
                     and any(t["id"] == 3 for t in d.group("none")))),
        (0, remember_orig(4), None),
        *drag(4, "overdue"),
        (0, drop, None),
        (500, None, (f"{tag}: dropping on Overdue is refused (the date is unchanged)",
                     lambda: d.task(4)["due"] == memo["orig"]["due"] and full.property("tasksLastDrop") is False
                     and not full.property("tasksDragging"))),
        *drag(9, "later"),
        (0, drop, None),
        (500, None, (f"{tag}: dropping on Later puts the task +8 days out",
                     lambda: d.task(9)["due"] == day_at(8, 18, 0))),
        # pop-out
        (0, lambda: (memo.update(row2_done=d.task(2)["done"]), full.scrollTasksTop()), None),
        (200, lambda: d.click("taskRow", 5, dx=-200), None),
        (600, None, (f"{tag}: clicking a row opens the task pop-out (grows from the row)",
                     lambda: full.property("taskEditorOpen") is True and full.property("taskEditorId") == 5)),
        (0, lambda: d.type_text(" edited"), None),
        (0, lambda: d.click("taskEditor.priority"), None),
        (300, lambda: d.click("taskEditor.menuItem", 0), None),
        (300, None, (f"{tag}: the pop-out's priority menu sets the priority (urgent)", lambda: d.task(5)["priority"] == "urgent")),
        (0, lambda: d.click("taskEditor.date"), None),
        (400, None, (f"{tag}: the date chip opens the date and time picker", lambda: full.property("pickerOpen") is True)),
        (0, lambda: d.click("picker.tomorrow"), None),
        (300, None, (f"{tag}: choosing 'Tomorrow' sets the due date to tomorrow (no time) and closes the picker",
                     lambda: d.task(5)["due"] == day_at(1) and d.task(5)["hasTime"] is False and full.property("pickerOpen") is False
                     and full.property("taskEditorOpen") is True)),
        (0, lambda: d.click("taskEditor.remind"), None),
        (300, None, (f"{tag}: 'Remind me' creates a reminder linked to the task, at 09:00 on its due day",
                     lambda: any(r["link"] and r["link"]["kind"] == "task" and r["link"]["id"] == 5 and r["due"] == day_at(1, 9, 0)
                                 for r in d.reminders()))),
        # click-through: the date picker and the priority menu inside the pop-out
        (0, lambda: d.click("taskEditor.date"), None),
        (400, None, None),
        (0, lambda: click_row_left(2), None),
        (300, None, (f"{tag}: click-through: clicking outside the date picker closes only the picker (the pop-out stays, nothing under it opens)",
                     lambda: full.property("pickerOpen") is False and full.property("taskEditorOpen") is True
                     and full.property("taskEditorId") == 5)),
        (0, lambda: d.click("taskEditor.priority"), None),
        (300, None, None),
        (0, lambda: click_row_left(2), None),
        (300, None, (f"{tag}: click-through: clicking outside the priority menu closes only the menu",
                     lambda: full.property("taskEditorOpen") is True and full.property("taskEditorId") == 5
                     and not js(full.property("taskMenuOpen")))),
        (0, lambda: click_row_left(2), None),
        (100, None, (f"{tag}: click-through: clicking outside the pop-out does not open the task under the click",
                     lambda: full.property("taskEditorId") in (5, -1))),
        (700, None, (f"{tag}: ...the pop-out saved the edited title and closed; the task under the click was not touched",
                     lambda: full.property("taskEditorOpen") is False and d.task(5)["title"] == "Review pull request edited"
                     and d.task(2)["done"] == memo["row2_done"])),
        # bell on a task without a date opens the picker
        (0, lambda: full.openTaskById(13), None),
        (600, lambda: d.click("taskEditor.remind"), None),
        (400, None, (f"{tag}: 'Remind me' on a task without a date opens the time picker",
                     lambda: full.property("pickerOpen") is True and full.property("taskEditorId") == 13)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (200, None, (f"{tag}: Esc closes the picker first, then the pop-out",
                     lambda: full.property("pickerOpen") is False and full.property("taskEditorOpen") is True)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (500, None, (f"{tag}: ...second Esc closes the pop-out", lambda: full.property("taskEditorOpen") is False)),
        (0, controller.hide_full, None),
    ]
    return steps
