"""Self-test steps for the Tasks, Reminders, Focus, Trash and Settings views, and the focus
Reset / fullscreen checks.  Ported from the prototype (rounds 5 and 6); the data is now the
backend's, seeded by ``seed.py``.  Real QTest mouse and key events, nothing outside temp folders."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QPoint, QRect, QSettings, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest

from panelo.desktop.shell import FullWindowState
from panelo.ui.selftest.driver import (
    ALT,
    CTRL,
    NO_MOD,
    Driver,
    Step,
    day_at,
    js,
    now_ms,
)

SEEN = QWindow.Visibility
QML_DIR = Path(__file__).resolve().parents[1] / "qml"

def tasks_steps(env: Any) -> list[Step]:
    controller = env.controller
    d = Driver(env)
    full = d.full
    memo: dict = {}
    tag = "tasks"

    def newest() -> dict:
        return d.newest(d.tasks())

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
                     and any(t["id"] == d.real("task", 5) and t["done"] for t in d.group("today"))
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
                   lambda: any(t["id"] == d.real("task", 3) for t in d.group("tomorrow")) and not full.property("tasksDragging")
                   and full.property("tasksLastDrop") is True)),
        *drag(3, "none"),
        (0, drop, None),
        (500, None, (f"{tag}: dropping it on No date clears the due date",
                     lambda: d.task(3)["due"] is None and d.task(3)["hasTime"] is False
                     and any(t["id"] == d.real("task", 3) for t in d.group("none")))),
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
                     lambda: full.property("taskEditorOpen") is True and full.property("taskEditorId") == d.real("task", 5))),
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
                     lambda: any(r["link"] and r["link"]["kind"] == "task" and r["link"]["id"] == d.real("task", 5) and r["due"] == day_at(1, 9, 0)
                                 for r in d.reminders()))),
        # click-through: the date picker and the priority menu inside the pop-out
        (0, lambda: d.click("taskEditor.date"), None),
        (400, None, None),
        (0, lambda: click_row_left(2), None),
        (300, None, (f"{tag}: click-through: clicking outside the date picker closes only the picker (the pop-out stays, nothing under it opens)",
                     lambda: full.property("pickerOpen") is False and full.property("taskEditorOpen") is True
                     and full.property("taskEditorId") == d.real("task", 5))),
        (0, lambda: d.click("taskEditor.priority"), None),
        (300, None, None),
        (0, lambda: click_row_left(2), None),
        (300, None, (f"{tag}: click-through: clicking outside the priority menu closes only the menu",
                     lambda: full.property("taskEditorOpen") is True and full.property("taskEditorId") == d.real("task", 5)
                     and not js(full.property("taskMenuOpen")))),
        (0, lambda: click_row_left(2), None),
        (100, None, (f"{tag}: click-through: clicking outside the pop-out does not open the task under the click",
                     lambda: full.property("taskEditorId") in (d.real("task", 5), -1))),
        (700, None, (f"{tag}: ...the pop-out saved the edited title and closed; the task under the click was not touched",
                     lambda: full.property("taskEditorOpen") is False and d.task(5)["title"] == "Review pull request edited"
                     and d.task(2)["done"] == memo["row2_done"])),
        # bell on a task without a date opens the picker
        (0, lambda: full.openTaskById(d.real("task", 13)), None),
        (600, lambda: d.click("taskEditor.remind"), None),
        (400, None, (f"{tag}: 'Remind me' on a task without a date opens the time picker",
                     lambda: full.property("pickerOpen") is True and full.property("taskEditorId") == d.real("task", 13))),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (200, None, (f"{tag}: Esc closes the picker first, then the pop-out",
                     lambda: full.property("pickerOpen") is False and full.property("taskEditorOpen") is True)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (500, None, (f"{tag}: ...second Esc closes the pop-out", lambda: full.property("taskEditorOpen") is False)),
        (0, controller.hide_full, None),
    ]
    return steps


def reminders_steps(env: Any) -> list[Step]:
    controller = env.controller
    d = Driver(env)
    full, store = d.full, d.store
    memo: dict = {}
    tag = "reminders"

    def newest() -> dict:
        return d.newest(d.reminders())

    def add_due_now(title: str) -> None:
        memo["due_id"] = str(store.addReminder(title, now_ms() - 2000, {}))

    def click_outside(reminder_id: int) -> None:
        p = d.probe("reminderRow", reminder_id)
        d.click_at(d.at(p.x() - 230, p.y()))

    def once(group: str) -> list[dict]:
        """The one-time reminders of a group (the seed also has three repeating ones, whose group depends on the date)."""
        return [r for r in d.rgroup(group) if not r.get("repeat")]

    def hhmm(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")

    return [
        (0, lambda: controller.show_full("reminders"), None),
        (600, None, (f"{tag}: the Reminders page shows: Due now, upcoming groups, and Past (4) collapsed",
                     lambda: full.property("section") == "reminders" and len(d.rgroup("dueNow")) >= 1
                     and len(d.rgroup("past")) == 4 and full.property("pastOpen") is False
                     and len(once("today")) + len(once("tomorrow")) >= 2
                     and len(once("week")) == 1 and len(once("later")) == 1)),
        # Snooze and Done on a due-now card
        (0, lambda: memo.update(before=now_ms()), None),
        (0, lambda: d.click("reminderSnooze", 1), None),
        (300, None, (f"{tag}: Snooze +10 min moves a due-now reminder to about 10 minutes from now",
                     lambda: abs(d.reminder(1)["due"] - (memo["before"] + 600000)) < 15000 and d.reminder(1)["snoozeCount"] == 1
                     and d.reminder(1)["done"] is False and all(r["id"] != d.real("reminder", 1) for r in d.rgroup("dueNow"))
                     and any(r["id"] == d.real("reminder", 1) for r in d.rgroup("today") + d.rgroup("tomorrow")))),
        (0, lambda: add_due_now("Due test"), None),
        (400, None, (f"{tag}: a reminder that is due now shows in Due now",
                     lambda: any(r["id"] == memo["due_id"] for r in d.rgroup("dueNow")))),
        (0, lambda: d.click("reminderDone", memo["due_id"]), None),
        (300, None, (f"{tag}: Done moves it to Past (done, doneAt set)",
                     lambda: d.reminder(memo["due_id"])["done"] is True
                     and any(r["id"] == memo["due_id"] for r in d.rgroup("past"))
                     and all(r["id"] != memo["due_id"] for r in d.rgroup("dueNow")))),
        # Past is collapsed by default and toggles
        (0, lambda: d.click("reminderPast"), None),
        (300, None, (f"{tag}: clicking the Past header expands it", lambda: full.property("pastOpen") is True)),
        (0, lambda: d.click("reminderPast"), None),
        (200, None, (f"{tag}: ...and collapses it again", lambda: full.property("pastOpen") is False)),
        # quick add
        (0, lambda: d.click("reminderAdd"), None),
        (100, lambda: d.type_text("Call Sam tomorrow 3pm"), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (300, None, (f"{tag}: a reminder quick add with a time creates the reminder right away (no picker)",
                     lambda: newest()["title"] == "Call Sam" and newest()["due"] == day_at(1, 15, 0)
                     and full.property("pickerOpen") is False)),
        (0, lambda: d.click("reminderAdd"), None),
        (100, lambda: d.type_text("Pay rent"), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (400, None, (f"{tag}: a reminder quick add without a time opens the picker instead",
                     lambda: full.property("pickerOpen") is True and newest()["title"] == "Call Sam")),
        (0, lambda: click_outside(5), None),
        (300, None, (f"{tag}: click-through: clicking outside the time picker closes it, opens no reminder under the click and keeps the typed text",
                     lambda: full.property("pickerOpen") is False and full.property("reminderEditorOpen") is False
                     and newest()["title"] == "Call Sam")),
        (0, lambda: d.click("reminderAdd"), None),
        (200, lambda: d.key(Qt.Key.Key_Return), None),
        (400, None, (f"{tag}: pressing Enter on the kept text opens the picker again", lambda: full.property("pickerOpen") is True)),
        (0, lambda: d.click("picker.field"), None),
        (100, lambda: d.type_text("21:15"), None),
        (0, lambda: d.key(Qt.Key.Key_Return), None),
        (300, None, (f"{tag}: typing a time in the picker and pressing Enter creates the reminder",
                     lambda: newest()["title"] == "Pay rent" and hhmm(newest()["due"]) == "21:15"
                     and full.property("pickerOpen") is False)),
        # link chips open the linked task and note
        (0, lambda: full.scrollRemindersTop(), None),
        (200, lambda: d.click("reminderLink", 3), None),
        (600, None, (f"{tag}: the link chip of a task reminder opens that task's pop-out",
                     lambda: full.property("taskEditorOpen") is True and full.property("taskEditorId") == d.real("task", 8)
                     and full.property("reminderEditorOpen") is False)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (500, lambda: d.click("reminderLink", 4), None),
        (600, None, (f"{tag}: the link chip of a note reminder opens that note's editor",
                     lambda: full.property("editorOpen") is True and full.property("editorNoteId") == d.real("note", 1))),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (500, None, None),
        # the reminder pop-out
        (0, lambda: d.click("reminderRow", 2), None),
        (600, None, (f"{tag}: clicking a row opens the reminder pop-out",
                     lambda: full.property("reminderEditorOpen") is True and full.property("reminderEditorId") == d.real("reminder", 2))),
        (0, lambda: d.type_text("!"), None),
        (0, lambda: click_outside(5), None),
        (100, None, (f"{tag}: click-through: clicking outside the pop-out does not open the reminder under the click",
                     lambda: full.property("reminderEditorId") in (d.real("reminder", 2), -1))),
        (600, None, (f"{tag}: ...the pop-out saved the title and closed, and no other pop-out opened",
                     lambda: full.property("reminderEditorOpen") is False and d.reminder(2)["title"] == "Gym!"
                     and full.property("taskEditorOpen") is False)),
        (0, lambda: d.click("reminderRow", 2), None),
        (600, lambda: d.click("reminderEditor.time"), None),
        (400, None, (f"{tag}: the pop-out's time chip opens the picker",
                     lambda: full.property("pickerOpen") is True)),
        (0, lambda: d.click_at(d.at(40, 300)), None),
        (300, None, (f"{tag}: click-through: clicking outside the picker closes only the picker",
                     lambda: full.property("pickerOpen") is False and full.property("reminderEditorOpen") is True)),
        (0, lambda: d.click("reminderEditor.done"), None),
        (600, None, (f"{tag}: the pop-out's Done completes the reminder and closes",
                     lambda: full.property("reminderEditorOpen") is False and d.reminder(2)["done"] is True)),
        (0, lambda: d.click("reminderRow", 5), None),
        (600, lambda: d.click("reminderEditor.delete"), None),
        (400, None, (f"{tag}: Delete in the pop-out removes the reminder",
                     lambda: full.property("reminderEditorOpen") is False and all(r["id"] != d.real("reminder", 5) for r in d.reminders()))),
        (0, controller.hide_full, None),
    ]


def focus_steps(env: Any) -> list[Step]:
    controller = env.controller
    d = Driver(env)
    full, store = d.full, d.store
    memo: dict = {}
    tag = "focus"

    def timers() -> list[dict]:
        return js(store.property("timers"))

    def timer(timer_id: Any) -> dict:
        return d.timer(timer_id)

    def newest_timer() -> dict:
        return timers()[-1]       # the list is oldest first

    def stats() -> dict:
        return js(store.property("focusStats"))

    def set_lengths(focus_s: int, short_s: int, long_s: int) -> None:
        store.setProperty("focusMs", focus_s * 1000)
        store.setProperty("shortMs", short_s * 1000)
        store.setProperty("longMs", long_s * 1000)
        env.backend.flush_settings()

    def finish_a_session() -> None:
        """A 2-second focus session (short lengths), started from the first phase."""
        memo["stats"] = stats()
        set_lengths(2, 3, 4)
        store.focusStartOver()
        store.setProperty("focusRunning", True)

    def restore_lengths() -> None:
        store.focusStartOver()
        set_lengths(25 * 60, 5 * 60, 15 * 60)

    def minutes() -> int:
        return round(int(store.property("focusMs")) / 60000)

    return [
        (0, lambda: controller.show_full("focus"), None),
        (600, None, (f"{tag}: the Focus page shows, with the stats strip from the focus history",
                     lambda: full.property("section") == "focus" and stats()["streak"] >= 4 and stats()["sessionsToday"] >= 3
                     and stats()["weekMinutes"] >= 75)),
        # the history grows when a focus phase completes
        (0, finish_a_session, None),
        (3300, None, (f"{tag}: completing a focus phase counts a session in the stats (and moves on to the break)",
                      lambda: stats()["sessionsToday"] == memo["stats"]["sessionsToday"] + 1
                      and stats()["minutesToday"] >= memo["stats"]["minutesToday"]
                      and stats()["weekMinutes"] >= memo["stats"]["weekMinutes"] and store.property("focusPhase") == 1)),
        (0, restore_lengths, None),
        # the lengths popover
        (0, lambda: memo.update(focus=minutes(), short=round(int(store.property("shortMs")) / 60000)), None),
        (0, lambda: d.click("focus.gear"), None),
        (300, None, (f"{tag}: the gear opens the lengths popover", lambda: full.property("lengthsOpen") is True)),
        (0, lambda: d.click("focus.focusPlus"), None),
        (200, None, (f"{tag}: the Focus stepper's + raises the focus length by 1 minute (the Settings value)",
                     lambda: minutes() == memo["focus"] + 1 and str(full.property("lengthsText")).split(",")[0] == str(memo["focus"] + 1))),
        (0, lambda: d.click("focus.focusMinus"), None),
        (200, None, (f"{tag}: ...and the − lowers it again", lambda: minutes() == memo["focus"])),
        (0, lambda: store.setProperty("shortMs", 7 * 60000), None),
        (200, None, (f"{tag}: a length changed elsewhere (Settings) shows in the popover's field",
                     lambda: str(full.property("lengthsText")).split(",")[1] == "7")),
        (0, lambda: store.setProperty("shortMs", memo["short"] * 60000), None),
        (0, lambda: memo.update(count=len(timers())), None),
        (0, lambda: d.click("focus.preset", 0), None),
        (300, None, (f"{tag}: click-through: clicking a preset chip outside the open popover only closes it (no timer starts)",
                     lambda: full.property("lengthsOpen") is False and len(timers()) == memo["count"])),
        # presets
        (0, lambda: d.click("focus.preset", 0), None),
        (300, None, (f"{tag}: a preset chip starts a new running timer named '5 min timer'",
                     lambda: len(timers()) == memo["count"] + 1 and newest_timer()["name"] == "5 min timer"
                     and newest_timer()["total"] == 300 and newest_timer()["running"] is True)),
        (0, lambda: memo.update(preset=newest_timer()["id"]), None),
        # + New
        (0, lambda: d.click("focus.newTimer"), None),
        (300, None, (f"{tag}: '+ New' opens the new-timer popup", lambda: full.property("newTimerOpen") is True)),
        (0, lambda: d.click("focus.nameField"), None),
        (100, lambda: d.type_text("Pasta"), None),
        (0, lambda: d.click("focus.minutesField"), None),
        (100, lambda: d.key(Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier), None),
        (0, lambda: d.type_text("12"), None),
        (0, lambda: d.click("focus.start"), None),
        (300, None, (f"{tag}: Start creates the named timer 'Pasta' of 12 minutes, running",
                     lambda: newest_timer()["name"] == "Pasta" and newest_timer()["total"] == 720
                     and newest_timer()["running"] is True and full.property("newTimerOpen") is False)),
        # click-through for the new-timer popup
        (0, lambda: d.click("focus.newTimer"), None),
        (300, lambda: store.setProperty("focusRunning", True), None),
        (200, lambda: d.click("focus.reset"), None),
        (300, None, (f"{tag}: click-through: clicking outside the new-timer popup closes it and does not press the Reset button under it",
                     lambda: full.property("newTimerOpen") is False and store.property("focusRunning") is True)),
        (0, lambda: store.setProperty("focusRunning", False), None),
        # several timers run at once
        (0, lambda: memo.update(tea=timer(1)["remaining"], laundry=timer(2)["remaining"], pasta=newest_timer()["remaining"]), None),
        (2300, None, (f"{tag}: several timers run at once (Tea, Laundry, the preset and Pasta all counting down)",
                      lambda: timer(1)["remaining"] < memo["tea"] and timer(2)["remaining"] < memo["laundry"]
                      and newest_timer()["remaining"] < memo["pasta"] and timer(memo["preset"])["running"] is True)),
        # the card menu
        (0, lambda: d.click("focus.timerMenu", memo["preset"]), None),
        (300, None, (f"{tag}: a timer card's menu opens", lambda: full.property("timerMenuOpen") is True)),
        (0, lambda: memo.update(count=len(timers())), None),
        (0, lambda: d.click("focus.preset", 1), None),
        (300, None, (f"{tag}: click-through: clicking outside a timer card's menu closes it and does not start the preset chip under the click",
                     lambda: full.property("timerMenuOpen") is False and len(timers()) == memo["count"])),
        (0, lambda: d.click("focus.timerMenu", memo["preset"]), None),
        (300, lambda: d.click("focus.menuItem", 0), None),
        (300, None, (f"{tag}: Reset puts the timer back to its full time, paused",
                     lambda: timer(memo["preset"])["remaining"] == timer(memo["preset"])["total"]
                     and timer(memo["preset"])["running"] is False)),
        (0, lambda: d.click("focus.timerMenu", memo["preset"]), None),
        (300, lambda: d.click("focus.menuItem", 1), None),
        (300, None, (f"{tag}: Delete removes the timer", lambda: all(t["id"] != memo["preset"] for t in timers()))),
        # a finishing timer fires the toast path
        (0, lambda: memo.update(toasts=controller.timer_toasts), None),
        (0, lambda: store.addTimer("Zed", 1, True), None),
        (2300, None, (f"{tag}: a finished timer fires the toast path ('Zed timer done')",
                      lambda: controller.timer_toasts == memo["toasts"] + 1 and controller.last_timer_message == "Zed timer done")),
        (0, lambda: controller.toast.dismiss(), None),
        (0, controller.hide_full, None),
    ]


def trash_steps(env: Any) -> list[Step]:
    controller = env.controller
    d = Driver(env)
    full, store, overlay = d.full, d.store, controller.overlay
    memo: dict = {}
    tag = "trash"

    def trash() -> list[dict]:
        return js(store.property("trashItems"))

    def notes_shown() -> int:
        return len(js(full.property("visibleNotes")))

    def ids(kind: str) -> list[str]:
        return [t["id"] for t in trash() if t["kind"] == kind]

    def task_id(number: int) -> str:
        return str(d.real("task", number))

    def note_id(number: int) -> str:
        return str(d.real("note", number))

    def in_database(kind: str, entity_id: str) -> bool:
        """Is the task or note still in the database (live or in the Trash)?"""
        service = env.application.tasks if kind == "task" else env.application.notes
        try:
            (service.get_task if kind == "task" else service.get_note)(entity_id, include_deleted=True)
        except Exception:
            return False
        return True

    def delete_days_ago(number: int, days: int) -> None:
        """Trash a task as if it had been deleted `days` days ago (the app's clock is moved
        back for the moment of the delete)."""
        from datetime import timedelta  # noqa: PLC0415

        tasks = env.application.tasks
        entity = task_id(number)
        with env.clock.at(env.clock.now_utc() - timedelta(days=days)):
            tasks.delete_task(entity, tasks.get_task(entity).version)

    return [
        (0, lambda: controller.show_full("trash"), None),
        (600, None, (f"{tag}: the Trash page lists the trashed notes and tasks (newest first, days left)",
                     lambda: full.property("section") == "trash" and len(trash()) == 4
                     and [t["deletedAt"] for t in trash()] == sorted((t["deletedAt"] for t in trash()), reverse=True)
                     and {t["daysLeft"] for t in trash()} == {28, 25, 10, 3})),
        (0, None, (f"{tag}: items older than 30 days were purged on load",
                   lambda: not in_database("task", task_id(22)) and in_database("task", task_id(21)))),
        # deleting a task in its pop-out sends it to the Trash
        (0, lambda: full.setProperty("section", "tasks"), None),
        (300, lambda: full.openTaskById(task_id(14)), None),
        (600, lambda: d.click("taskEditor.delete"), None),
        (400, None, (f"{tag}: deleting a task in its pop-out moves it to the Trash (gone from the lists)",
                     lambda: task_id(14) in ids("task") and all(t["id"] != task_id(14) for t in d.tasks())
                     and all(t["id"] != task_id(14) for t in d.group("none")) and full.property("taskEditorOpen") is False)),
        # deleting a note in the editor sends it to the Trash too
        (0, lambda: (memo.update(notes=notes_shown(), overlay_notes=int(overlay.property("shownNotes"))),
                     full.setProperty("section", "notes")), None),
        (400, lambda: d.click("note", 0), None),
        (600, None, (f"{tag}: a note opens in the editor", lambda: full.property("editorOpen") is True)),
        (0, lambda: d.click("noteEditorTrash"), None),
        (400, None, (f"{tag}: deleting a note in the editor moves it to the Trash (hidden on the Notes page and in the overlay)",
                     lambda: note_id(1) in ids("note") and notes_shown() == memo["notes"] - 1 and full.property("editorOpen") is False
                     and int(overlay.property("shownNotes")) == memo["overlay_notes"] - 1)),
        # restore and delete forever
        (0, lambda: full.setProperty("section", "trash"), None),
        (400, None, (f"{tag}: the trashed note and task are listed, newest first",
                     lambda: len(trash()) == 6 and trash()[0]["kind"] == "note" and trash()[1]["id"] == task_id(14))),
        (0, lambda: d.click("trashRestore", 0), None),
        (300, None, (f"{tag}: Restore brings the note back (Notes page again, same pin, out of the Trash)",
                     lambda: note_id(1) not in ids("note") and notes_shown() == memo["notes"]
                     and d.note(1)["pinned"] is True and d.note(1)["deletedAt"] is None)),
        (0, lambda: d.click("trashRestore", 0), None),
        (300, None, (f"{tag}: Restore brings the task back into its group (No date)",
                     lambda: task_id(14) not in ids("task") and any(t["id"] == task_id(14) for t in d.group("none")))),
        (0, lambda: store.deleteTask(task_id(14)), None),
        (200, lambda: d.click("trashForever", 0), None),
        (300, None, (f"{tag}: Delete forever removes the item for good",
                     lambda: all(t["id"] != task_id(14) for t in d.tasks()) and task_id(14) not in ids("task")
                     and not in_database("task", task_id(14)) and len(trash()) == 4)),
        # purge: older than 30 days goes, 29 days stays
        (0, lambda: (delete_days_ago(13, 31), delete_days_ago(12, 29)), None),
        (200, lambda: store.purgeTrash(), None),
        (300, None, (f"{tag}: the purge deletes items older than 30 days and keeps newer ones",
                     lambda: not in_database("task", task_id(13)) and in_database("task", task_id(12))
                     and task_id(12) in ids("task") and task_id(13) not in ids("task"))),
        # Empty trash needs a confirmation
        (0, lambda: d.click("trashEmpty"), None),
        (400, None, (f"{tag}: Empty trash asks for confirmation", lambda: full.property("confirmOpen") is True and len(trash()) > 0)),
        (0, lambda: d.click_at(d.at(40, 300)), None),
        (300, None, (f"{tag}: click-through: clicking outside the dialog cancels it and does not empty the trash",
                     lambda: full.property("confirmOpen") is False and len(trash()) > 0)),
        (0, lambda: d.click("trashEmpty"), None),
        (400, lambda: d.key(Qt.Key.Key_Escape), None),
        (300, None, (f"{tag}: Esc cancels the confirmation", lambda: full.property("confirmOpen") is False and len(trash()) > 0)),
        (0, lambda: d.click("trashEmpty"), None),
        (400, lambda: d.click("confirmYes"), None),
        (400, None, (f"{tag}: confirming Empty trash deletes everything in it",
                     lambda: len(trash()) == 0 and full.property("confirmOpen") is False
                     and not any(n["deletedAt"] for n in d.notes()) and not any(t["deletedAt"] for t in d.tasks()))),
        (0, controller.hide_full, None),
    ]


def settings_steps(env: Any) -> list[Step]:
    controller = env.controller
    d = Driver(env)
    full, store, overlay, library = d.full, d.store, controller.overlay, controller.library
    stub = env.hotkeys
    sounds = controller.sounds
    memo: dict = {}
    tag = "settings"
    tmp_dirs: list[Path] = []

    def ini(key: str) -> Any:
        """A setting as the database holds it, as a restarted app would read it."""
        return d.saved(key)

    def prop(name: str):
        return store.property(name)

    def press_combo(key, modifiers) -> None:
        QTest.keyClick(full, key, modifiers)

    def slider(name: str, fraction: float) -> QPoint:
        value = js(full.settingsSlider(name, fraction))
        return d.at(value["x"], value["y"])

    def drag_slider(name: str, start: float, end: float) -> None:
        a, b = slider(name, start), slider(name, end)
        d.press(a)
        d.move(b)
        d.release(b)

    def click_segment(key: str) -> None:
        value = js(full.settingsSegment(key))
        d.click_at(d.at(value["x"], value["y"]))

    def fire() -> None:
        memo["log_len"] = len(controller.notify_log)
        memo["sound_calls"] = len(sounds.calls)
        backend = env.backend
        reminder_id = backend.addReminder("Demo reminder", now_ms() - 1000, {})   # already due: no alert by itself
        backend.alert_reminder(reminder_id)                                         # the real alert path

    def new_log() -> list:
        return controller.notify_log[memo["log_len"]:]

    def make_dir(name: str, files: bool = False, create: bool = True) -> Path:
        base = Path(tempfile.mkdtemp(prefix="proto-lib-move-"))
        tmp_dirs.append(base)
        target = base / name
        if create:
            target.mkdir()
            if files:
                (target / "keep.txt").write_text("hi", encoding="utf-8")
        return target

    def same(a: str, b: Path) -> bool:
        return Path(a).resolve() == b.resolve()

    def cleanup() -> None:
        for path in tmp_dirs:
            shutil.rmtree(path, ignore_errors=True)

    def click_outside_menu() -> None:
        # a spot over the General card, away from any open menu
        p = d.probe("settings.autoSwitch")
        d.click_at(d.at(p.x() - 400, p.y()))

    return [
        (0, lambda: controller.show_full("settings"), None),
        (600, lambda: full.setProperty("section", "settings"), None),
        # ── toggles persist ──
        (0, lambda: d.click("settings.startSwitch"), None),
        (0, lambda: d.click("settings.closeSwitch"), None),
        (0, lambda: d.click("settings.autoSwitch"), None),
        (900, None, (f"{tag}: the toggles apply live and persist (re-read from the settings file): start with Windows on, close-to-tray off, auto backup off",
                     lambda: prop("startWithWindows") is True and prop("closeHidesToTray") is False and prop("autoBackup") is False
                     and str(ini("startWithWindows")) == "true" and str(ini("closeHidesToTray")) == "false"
                     and str(ini("autoBackup")) == "false")),
        (0, lambda: d.click("settings.startSwitch"), None),
        (0, lambda: d.click("settings.closeSwitch"), None),
        (0, lambda: d.click("settings.autoSwitch"), None),
        (900, None, (f"{tag}: ...and toggling back persists too",
                     lambda: str(ini("startWithWindows")) == "false" and str(ini("closeHidesToTray")) == "true" and str(ini("autoBackup")) == "true")),
        # ── hotkey recording ──
        (0, lambda: memo.update(calls=len(stub.calls)), None),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, None, (f"{tag}: clicking the hotkey box starts recording ('Press keys…', highlighted)",
                     lambda: full.property("hotkeyRecording") is True)),
        (0, lambda: press_combo(Qt.Key.Key_K, CTRL), None),
        (200, None, (f"{tag}: an invalid combination (one modifier) shows the error and does not touch the hotkey",
                     lambda: "two modifiers" in str(full.property("hotkeyStatus")) and full.property("hotkeyRecording") is False
                     and len(stub.calls) == memo["calls"] and prop("hotkey") == "Ctrl+Alt+Space")),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, lambda: press_combo(Qt.Key.Key_K, CTRL | ALT), None),
        (200, None, (f"{tag}: a valid combination calls the (stubbed) HotkeyManager.replace, shows '✓ available' and is saved",
                     lambda: stub.calls[memo["calls"]:] == ["Ctrl+Alt+K"] and prop("hotkey") == "Ctrl+Alt+K"
                     and "available" in str(full.property("hotkeyStatus")) and full.property("hotkeyRecording") is False)),
        (0, lambda: stub.taken.add("Ctrl+Alt+F"), None),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, lambda: press_combo(Qt.Key.Key_F, CTRL | ALT), None),
        (200, None, (f"{tag}: a combination another app already uses shows 'already used by another app' and keeps the old hotkey",
                     lambda: "already used by another app" in str(full.property("hotkeyStatus")) and prop("hotkey") == "Ctrl+Alt+K")),
        (0, lambda: memo.update(calls=len(stub.calls)), None),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, lambda: QTest.keyClick(full, Qt.Key.Key_Escape, NO_MOD), None),
        (200, None, (f"{tag}: Esc cancels the recording and changes nothing",
                     lambda: full.property("hotkeyRecording") is False and len(stub.calls) == memo["calls"] and prop("hotkey") == "Ctrl+Alt+K")),
        (0, lambda: store.setProperty("hotkey", "Ctrl+Alt+Space"), None),
        # ── focus lengths: only the Focus page's ⚙ edits them now (the Settings section is gone) ──
        (0, lambda: memo.update(focus=round(int(prop("focusMs")) / 60000)), None),
        (0, lambda: full.setProperty("section", "focus"), None),
        (400, lambda: d.click("focus.gear"), None),
        (300, lambda: d.click("focus.focusPlus"), None),
        (900, None, (f"{tag}: the Focus page's ⚙ stepper still changes the focus length and persists it (re-read from the settings file)",
                     lambda: round(int(prop("focusMs")) / 60000) == memo["focus"] + 1
                     and str(full.property("lengthsText")).split(",")[0] == str(memo["focus"] + 1)
                     and str(ini("focusMs")) == str((memo["focus"] + 1) * 60000))),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (300, lambda: store.setProperty("focusMs", memo["focus"] * 60000), None),
        (0, lambda: full.setProperty("section", "settings"), None),
        (300, None, (f"{tag}: the Settings page has no focus length steppers any more",
                     lambda: d.probe("settings.focusPlus") is None and d.probe("settings.shortPlus") is None
                     and d.probe("settings.longPlus") is None and full.property("settingsLengthsText") is None)),
        # ── notifications ──
        (0, lambda: click_segment("windows"), None),
        (200, fire, None),
        (300, None, (f"{tag}: style 'Windows notification' sends only the Windows notification (no glass toast)",
                     lambda: prop("notificationStyle") == "windows" and [kind for kind, _ in new_log()] == ["windows"])),
        (0, lambda: click_segment("both"), None),
        (200, fire, None),
        (300, None, (f"{tag}: style 'Both' sends the toast and the Windows notification",
                     lambda: prop("notificationStyle") == "both" and sorted(kind for kind, _ in new_log()) == ["toast", "windows"])),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: click_segment("toast"), None),
        (200, fire, None),
        (300, None, (f"{tag}: style 'Glass toast' shows only the toast, and with the sound on None it plays nothing",
                     lambda: prop("notificationStyle") == "toast" and [kind for kind, _ in new_log()] == ["toast"]
                     and sounds.calls[memo["sound_calls"]:] == [])),
        (0, lambda: controller.toast.dismiss(), None),
        # ── notification sound picker (the player is a stub: it only records what would play) ──
        (0, lambda: memo.update(options=[o for o in sounds.options() if o["path"]]), None),
        (0, lambda: None, (f"{tag}: the sound list is 'None' plus only Windows sounds whose file exists",
                           lambda: sounds.options()[0] == {"path": "", "label": "None"} and len(memo["options"]) >= 1
                           and all(Path(o["path"]).is_file() and Path(o["path"]).parent == sounds.media_dir for o in memo["options"])
                           and [o["label"] for o in memo["options"]] == [o["label"] for o in memo["options"] if o["label"] in
                                {"Notify", "Ding", "Chimes", "Chord", "Balloon", "Alarm", "Ring"}])),
        (0, lambda: d.click("settings.soundButton"), None),
        (300, None, (f"{tag}: the sound button opens the list", lambda: full.property("soundMenuOpen") is True)),
        (0, lambda: memo.update(calls=len(sounds.calls)), None),
        (0, lambda: d.move(d.probe("settings.menuItem", 1)), None),
        (700, None, (f"{tag}: hovering a sound row for a moment plays a preview of it (stub called with that file, nothing chosen yet)",
                     lambda: sounds.calls[memo["calls"]:] == [("preview", memo["options"][0]["path"])] and prop("notificationSound") == "")),
        (0, lambda: memo.update(calls=len(sounds.calls), stops=sounds.stops), None),
        (0, lambda: d.move(d.probe("settings.menuItem", 0)), None),
        (700, None, (f"{tag}: hovering 'None' stops the preview and plays nothing new",
                     lambda: len(sounds.calls) == memo["calls"] and sounds.stops > memo["stops"])),
        (0, lambda: memo.update(calls=len(sounds.calls), pick=min(1, len(memo["options"]) - 1)), None),
        (0, lambda: d.click("settings.menuItem", memo["pick"] + 1), None),
        (900, None, (f"{tag}: choosing a sound plays its preview (stop first), saves it and closes the list",
                     lambda: prop("notificationSound") == memo["options"][memo["pick"]]["path"]
                     and sounds.calls[memo["calls"]:] == [("preview", prop("notificationSound"))]
                     and full.property("soundMenuOpen") is False
                     and Path(str(ini("notificationSound"))).resolve() == Path(prop("notificationSound")).resolve())),
        (0, lambda: memo.update(chosen=str(prop("notificationSound"))), None),
        (0, fire, None),
        (300, None, (f"{tag}: a glass toast plays the selected sound",
                     lambda: [kind for kind, _ in new_log()] == ["toast"] and sounds.calls[memo["sound_calls"]:] == [("play", memo["chosen"])])),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: (controller.on_focus_completed("Focus session done"), controller.on_timer_finished("Zed")), None),
        (300, None, (f"{tag}: ...a focus phase end and a finished timer play it too",
                     lambda: sounds.calls[memo["sound_calls"] + 1:] == [("play", memo["chosen"])] * 2)),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: d.click("settings.soundButton"), None),
        (300, lambda: d.click("settings.menuItem", 0), None),
        (900, None, (f"{tag}: choosing 'None' saves '' and closes the list",
                     lambda: prop("notificationSound") == "" and str(ini("notificationSound") or "") == ""
                     and full.property("soundMenuOpen") is False)),
        (0, fire, None),
        (300, None, (f"{tag}: with 'None' a toast shows silently (no sound call)",
                     lambda: [kind for kind, _ in new_log()] == ["toast"] and sounds.calls[memo["sound_calls"]:] == [])),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: d.click("settings.alertsSwitch"), None),
        (200, fire, None),
        (300, None, (f"{tag}: with reminder alerts off a due reminder shows no alert at all",
                     lambda: prop("reminderAlerts") is False and new_log() == [])),
        (0, lambda: d.click("settings.alertsSwitch"), None),
        (200, None, (f"{tag}: ...and the notification settings persist",
                     lambda: prop("reminderAlerts") is True and str(ini("notificationStyle")) == "toast")),
        # ── appearance sliders ──
        (0, lambda: drag_slider("blur", 0.95, 0.5), None),
        (0, lambda: drag_slider("dim", 0.4, 0.2), None),
        (300, None, (f"{tag}: the blur and dim sliders change BlurredBackdrop in the Full App and in both overlay layers",
                     lambda: abs(float(prop("backdropBlur")) - 0.5) < 0.03 and abs(float(prop("backdropDim")) - 0.2) < 0.03
                     and abs(float(full.property("backdropBlurValue")) - float(prop("backdropBlur"))) < 1e-6
                     and abs(float(full.property("backdropDimValue")) - float(prop("backdropDim"))) < 1e-6
                     and abs(float(overlay.property("backdropBlurValue")) - float(prop("backdropBlur"))) < 1e-6
                     and abs(float(overlay.property("backdropDimValue")) - float(prop("backdropDim"))) < 1e-6
                     and abs(float(overlay.property("ghostBlurValue")) - float(prop("backdropBlur"))) < 1e-6
                     and abs(float(overlay.property("ghostDimValue")) - float(prop("backdropDim"))) < 1e-6)),
        (800, None, (f"{tag}: ...and the appearance values persist in the settings file",
                     lambda: abs(float(ini("backdropBlur")) - float(prop("backdropBlur"))) < 1e-6 and abs(float(ini("backdropDim")) - float(prop("backdropDim"))) < 1e-6)),
        (0, lambda: (store.setProperty("backdropBlur", 1.0), store.setProperty("backdropDim", 0.45)), None),
        (0, lambda: drag_slider("size", 0.6, 0.0), None),
        (300, None, (f"{tag}: the overlay size slider (60%) changes the overlay's group rectangle",
                     lambda: abs(float(prop("overlaySize")) - 0.6) < 0.001
                     and abs(float(overlay.property("groupW")) - 0.6 * float(overlay.property("width"))) < 1.0
                     and abs(float(overlay.property("groupH")) - 0.6 * float(overlay.property("height"))) < 1.0
                     and abs(float(overlay.property("baseRect").width()) - float(overlay.property("groupW"))) < 1.0)),
        (0, lambda: drag_slider("size", 0.0, 1.0), None),
        (300, None, (f"{tag}: the size slider's maximum is 90% (not 95%), and dragging to the end gives 90%, a bigger group again",
                     lambda: abs(float(full.property("settingsSizeMax")) - 0.90) < 1e-9
                     and abs(float(prop("overlaySize")) - 0.90) < 0.001
                     and abs(float(overlay.property("groupW")) - 0.90 * float(overlay.property("width"))) < 1.0)),
        (0, lambda: store.setProperty("overlaySize", 0.95), None),
        (900, None, (f"{tag}: a stored size above the maximum (0.95) is clamped down to 0.90 (the same handler runs when it is loaded) and 0.90 is what is saved",
                     lambda: abs(float(prop("overlaySize")) - 0.90) < 1e-9 and abs(float(ini("overlaySize")) - 0.90) < 1e-6)),
        # five columns need ~1140 px: dragging the size down must clamp, not squeeze the panels
        (0, lambda: overlay.applyTemplateNamed("columns"), None),
        (200, lambda: drag_slider("size", 1.0, 0.0), None),
        # (a screen wide enough for five columns at 60% needs no clamp; one too narrow for them even at 90% keeps 90%)
        (300, None, (f"{tag}: the layout minimum is kept: a small size is clamped up (never past 90%) so no panel drops below 220x160",
                     lambda: 0.6 - 1e-9 <= float(overlay.property("sizeFraction")) <= 0.90 + 1e-9
                     and (float(overlay.property("sizeFraction")) > 0.6 + 1e-6
                          or 0.6 * float(overlay.property("width")) >= 5 * 220 + 4 * float(overlay.property("gap")))
                     and (float(overlay.property("sizeFraction")) >= 0.90 - 1e-9
                          or all(r["width"] >= 219.5 and r["height"] >= 159.5 for r in js(overlay.property("rects")).values())))),
        (0, lambda: (overlay.resetLayout(), store.setProperty("overlaySize", 0.85)), None),
        # ── files: the library location ──
        (0, lambda: d.click("settings.openFolder"), None),
        (200, None, (f"{tag}: 'Open folder' opens the library folder in Explorer (stubbed)",
                     lambda: library.launched[-1] == ("openFolder", ""))),
        (0, lambda: memo.update(orig=Path(library.rootPath).resolve(), new=make_dir("newlib", create=False)), None),
        (0, lambda: full.requestLibraryMove(str(memo["new"])), None),
        (400, None, (f"{tag}: Change… to a new folder asks 'Move your files to ...?'",
                     lambda: full.property("confirmOpen") is True and library.root == memo["orig"])),
        (0, lambda: d.click("confirmYes"), None),
        (600, None, (f"{tag}: confirming moves the library contents and switches the root",
                     lambda: same(library.rootPath, memo["new"]) and (memo["new"] / "Study/Physics").is_dir()
                     and not any(memo["orig"].iterdir()) and same(str(prop("libraryPath")), memo["new"]))),
        (0, lambda: memo.update(full_dir=make_dir("busy", files=True)), None),
        (0, lambda: full.requestLibraryMove(str(memo["full_dir"])), None),
        (300, None, (f"{tag}: a folder that already has files is refused (no dialog, an error shows)",
                     lambda: full.property("confirmOpen") is False and "empty or new" in str(full.property("filesError"))
                     and same(library.rootPath, memo["new"]))),
        (0, lambda: full.requestLibraryMove(str(memo["orig"])), None),
        (400, lambda: d.click("confirmYes"), None),
        (600, None, (f"{tag}: moving back to the (now empty) original folder works",
                     lambda: same(library.rootPath, memo["orig"]) and (memo["orig"] / "Study/Physics").is_dir()
                     and full.property("filesError") == "")),
        # ── backups ──
        (0, lambda: memo.update(backups=int(full.property("backupCount"))), None),
        (0, lambda: d.click("settings.backUpNow"), None),
        (300, None, (f"{tag}: Back up now adds a backup entry at the top",
                     lambda: int(full.property("backupCount")) == min(memo["backups"] + 1, int(prop("keepBackups")))
                     and js(prop("backups"))[0]["at"] >= js(prop("backups"))[1]["at"])),
        (0, lambda: d.click("settings.keepButton"), None),
        (300, None, (f"{tag}: the 'Keep the last' menu opens", lambda: full.property("settingsMenuOpen") is True)),
        (0, click_outside_menu, None),
        (300, None, (f"{tag}: click-through: clicking outside the menu closes it and does not flip the toggle under the click",
                     lambda: full.property("settingsMenuOpen") is False and prop("autoBackup") is True)),
        (0, lambda: d.click("settings.keepButton"), None),
        (300, lambda: d.click("settings.menuItem", 0), None),
        (300, None, (f"{tag}: choosing '3 backups' sets how many are kept", lambda: prop("keepBackups") == 3)),
        (0, lambda: d.click("settings.backUpNow"), None),
        (300, None, (f"{tag}: Back up now trims the list to the number kept", lambda: int(full.property("backupCount")) == 3)),
        (0, lambda: memo.update(log_len=len(controller.notify_log)), None),
        (0, lambda: d.click("settings.restore", 0), None),
        (400, None, (f"{tag}: Restore asks for confirmation", lambda: full.property("confirmOpen") is True)),
        (0, lambda: d.click("confirmYes"), None),
        (400, None, (f"{tag}: confirming Restore shows the (fake) success toast",
                     lambda: any("restored" in text for _, text in controller.notify_log[memo["log_len"]:]))),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: memo.update(backups=int(full.property("backupCount"))), None),
        (0, lambda: d.click("settings.backupMenu", 0), None),
        (300, lambda: d.click("settings.menuItem", 0), None),
        (300, None, (f"{tag}: a backup's ⋯ menu > Delete removes it", lambda: int(full.property("backupCount")) == memo["backups"] - 1)),
        (0, lambda: store.setProperty("keepBackups", 7), None),
        (0, cleanup, None),
        (0, controller.hide_full, None),
    ]


class _StubBackend(QObject):
    """A Backend that holds only the settings JSON ``stored`` (everything else empty): enough for
    a second, throwaway engine to load the Store from it."""

    settingsChanged = Signal()
    notesChanged = Signal()
    tasksChanged = Signal()
    remindersChanged = Signal()
    timersChanged = Signal()
    presetsChanged = Signal()
    trashChanged = Signal()
    backupsChanged = Signal()
    focusChanged = Signal()
    statsChanged = Signal()

    def __init__(self, settings: str) -> None:
        super().__init__()
        self._settings = settings

    settingsJson = Property(str, lambda self: self._settings, notify=settingsChanged)
    notesJson = Property(str, lambda self: "[]", notify=notesChanged)
    tasksJson = Property(str, lambda self: "[]", notify=tasksChanged)
    remindersJson = Property(str, lambda self: "[]", notify=remindersChanged)
    timersJson = Property(str, lambda self: "[]", notify=timersChanged)
    presetsJson = Property(str, lambda self: "[]", notify=presetsChanged)
    trashJson = Property(str, lambda self: "[]", notify=trashChanged)
    backupsJson = Property(str, lambda self: "[]", notify=backupsChanged)
    focusJson = Property(
        str,
        lambda self: '{"active":false,"phase":0,"total":1500,"remaining":1500,"running":false,"deadline":null}',
        notify=focusChanged,
    )
    statsJson = Property(str, lambda self: "{}", notify=statsChanged)

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:  # noqa: N802
        pass


def stored_size_loads(stored: float) -> float:
    """What a fresh Store (a second QML engine over a Backend whose saved settings hold `stored`)
    makes of the saved overlay size."""
    import json  # noqa: PLC0415

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    backend = _StubBackend(json.dumps({"overlaySize": stored}))
    engine.rootContext().setContextProperty("Backend", backend)
    component = QQmlComponent(engine)
    qml = b"import QtQuick\nimport App\nQtObject { property real v: Store.overlaySize }"
    component.setData(qml, QUrl())
    obj = component.create()
    value = float(obj.property("v")) if obj is not None else -1.0
    del obj, component
    engine.deleteLater()
    return value


def round6_steps(env: Any) -> list[Step]:
    controller = env.controller
    d = Driver(env)
    full, store = d.full, d.store
    memo: dict = {}
    tag = "round 6"

    def prop(name: str):
        return store.property(name)

    def minutes() -> int:
        return round(int(prop("focusMs")) / 60000)

    def set_lengths(focus_s: int, short_s: int, long_s: int) -> None:
        store.setProperty("focusMs", focus_s * 1000)
        store.setProperty("shortMs", short_s * 1000)
        store.setProperty("longMs", long_s * 1000)
        env.backend.flush_settings()

    def screen_rect() -> QRect:
        return QRect(full.screen().geometry())

    def available() -> QRect:
        return QRect(full.screen().availableGeometry())

    def geometry() -> QRect:
        return QRect(full.geometry())

    def disk():
        return FullWindowState(QSettings(str(controller.settings_path), QSettings.Format.IniFormat))

    def is_fullscreen() -> bool:
        return full.visibility() == SEEN.FullScreen and full.property("zenActive") is True and geometry() == screen_rect()

    def wiggle() -> None:
        """A real mouse move, a few pixels off from the last one each time."""
        memo["n"] = memo.get("n", 0) + 1
        QTest.mouseMove(full, QPoint(300 + 7 * memo["n"], 300 + 5 * memo["n"]))

    def timer_count() -> int:
        return len(js(prop("timers")))

    return [
        # ── Focus page: Reset and Start over ──
        (0, lambda: controller.show_full("focus"), None),
        (600, lambda: (store.focusStartOver(), store.focusSkip(), store.focusSkip(), store.setProperty("focusRunning", True)), None),
        (0, lambda: memo.update(total=int(prop("focusTotal"))), None),
        (0, lambda: d.move(d.probe("focus.reset")), None),
        (800, None, (f"{tag}: hovering the Reset button shows the 'Reset session' hint", lambda: full.property("resetHintShown") is True)),
        (0, lambda: d.move(d.at(40, 600)), None),
        (200, None, (f"{tag}: ...and moving away hides it", lambda: full.property("resetHintShown") is False)),
        (0, lambda: d.click("focus.reset"), None),
        (300, None, (f"{tag}: the Reset button pauses and restores the full length of the current session (phase kept)",
                     lambda: prop("focusRunning") is False and int(prop("focusRemaining")) == memo["total"] and int(prop("focusPhase")) == 2)),
        (0, lambda: store.setProperty("focusRunning", True), None),
        (0, lambda: d.click("focus.gear"), None),
        (300, None, (f"{tag}: the gear popover offers 'Start over from Session 1'", lambda: full.property("lengthsOpen") is True and d.probe("focus.startOver") is not None)),
        (0, lambda: d.click("focus.startOver"), None),
        (300, None, (f"{tag}: Start over pauses, goes to phase 0 'Session 1 of 4' with the full focus length, and closes the popover",
                     lambda: prop("focusRunning") is False and int(prop("focusPhase")) == 0 and prop("focusLabel") == "Session 1 of 4"
                     and int(prop("focusRemaining")) == int(prop("focusTotal")) == int(store.focusDuration(0))
                     and full.property("lengthsOpen") is False)),
        # ── Fullscreen: a click on a control does not leak, calm mode, exit ways ──
        (0, lambda: full.setProperty("zenCalmDelay", 1200), None),
        (0, lambda: memo.update(normal=geometry()), None),
        (0, lambda: d.click("focus.fullscreen"), None),
        (700, None, (f"{tag}: the ⛶ button makes the window fullscreen and shows the zen view",
                     lambda: is_fullscreen() and full.property("zenVisible") is True and controller.full_is_fullscreen()
                     and re.fullmatch(r"\d\d:\d\d", str(full.property("zenTime"))))),
        (0, None, (f"{tag}: the fullscreen geometry is not saved as the window's normal geometry",
                   lambda: disk().geometry == memo["normal"] and not disk().maximized)),
        (0, None, (f"{tag}: the zen view starts with its controls shown", lambda: full.property("zenControlsShown") is True)),
        (1800, None, (f"{tag}: calm mode: 1.2 s without mouse movement hides the controls (and the cursor)",
                     lambda: full.property("zenControlsShown") is False)),
        (0, wiggle, None),
        (150, None, (f"{tag}: a mouse move shows the controls again", lambda: full.property("zenControlsShown") is True)),
        (1800, None, (f"{tag}: ...and calm mode comes back after the next quiet spell", lambda: full.property("zenControlsShown") is False)),
        (0, lambda: d.move(d.probe("zen.reset")), None),
        (150, None, (f"{tag}: moving onto the (hidden) controls' own spot wakes them too", lambda: full.property("zenControlsShown") is True)),
        (800, None, (f"{tag}: hovering a zen control (reset) still shows its hint under the wake-up layer",
                     lambda: full.property("zenResetHintShown") is True and full.property("zenControlsShown") is True)),
        (0, wiggle, None),
        (0, lambda: memo.update(timers=timer_count(), running=prop("focusRunning")), None),
        (0, lambda: d.click("focus.preset", 0), None),
        (200, None, (f"{tag}: click-through: a click in fullscreen over the page's preset chip starts no timer",
                     lambda: timer_count() == memo["timers"] and is_fullscreen())),
        (0, lambda: d.click("zen.play"), None),
        (200, None, (f"{tag}: the zen play button toggles the timer once (no second click leaks through)", lambda: prop("focusRunning") is True)),
        (0, lambda: d.click("zen.play"), None),
        (200, None, (f"{tag}: ...and pauses it again", lambda: prop("focusRunning") is False)),
        (0, lambda: d.click("zen.reset"), None),
        (200, None, (f"{tag}: the zen reset button restores the session length", lambda: int(prop("focusRemaining")) == int(prop("focusTotal")))),
        (0, lambda: d.click("zen.skip"), None),
        (200, None, (f"{tag}: the zen skip button goes to the next phase", lambda: int(prop("focusPhase")) == 1)),
        (0, lambda: (store.focusStartOver(), set_lengths(2, 3, 4), store.setProperty("focusRunning", True), memo.update(toasts=controller.focus_toasts, log=len(controller.notify_log))), None),
        (3300, None, (f"{tag}: a phase end while fullscreen still fires the toast",
                      lambda: controller.focus_toasts == memo["toasts"] + 1 and ("toast", controller.last_focus_message) in controller.notify_log[memo["log"]:]
                      and is_fullscreen())),
        (0, lambda: (store.focusStartOver(), set_lengths(25 * 60, 5 * 60, 15 * 60)), None),
        (0, lambda: controller.toast.dismiss(), None),
        (0, wiggle, None),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (900, None, (f"{tag}: Esc leaves fullscreen and restores the previous normal window (same geometry)",
                     lambda: full.visibility() == SEEN.Windowed and full.property("zenActive") is False and geometry() == memo["normal"]
                     and full.property("zenVisible") is False and not controller.full_is_fullscreen())),
        (0, lambda: d.click("focus.fullscreen"), None),
        (900, None, (f"{tag}: entering fullscreen again works", lambda: is_fullscreen())),
        (0, wiggle, None),
        (0, lambda: d.click("zen.exit"), None),
        (900, None, (f"{tag}: the exit button leaves fullscreen (normal geometry back)",
                     lambda: full.visibility() == SEEN.Windowed and geometry() == memo["normal"] and full.property("zenActive") is False)),
        (0, lambda: d.click("focus.fullscreen"), None),
        (900, lambda: wiggle(), None),
        (0, lambda: QTest.mouseDClick(full, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, d.probe("zen.time")), None),
        (900, None, (f"{tag}: a double-click on the time leaves fullscreen",
                     lambda: full.visibility() == SEEN.Windowed and geometry() == memo["normal"] and full.property("zenActive") is False)),
        # maximized before: Esc returns to maximized, and the saved normal geometry is untouched
        (0, full.showMaximized, None),
        (700, lambda: d.click("focus.fullscreen"), None),
        (900, None, (f"{tag}: a maximized window can go fullscreen too", lambda: is_fullscreen())),
        (0, None, (f"{tag}: ...and the settings still say maximized with the old normal geometry",
                   lambda: disk().maximized and disk().geometry == memo["normal"])),
        (0, wiggle, None),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (900, None, (f"{tag}: Esc from fullscreen returns to the maximized state (available area, not over the taskbar)",
                     lambda: full.visibility() == SEEN.Maximized and geometry() == available() and full.property("zenActive") is False)),
        # the hotkey in fullscreen: exits fullscreen and hides to the tray
        (0, lambda: d.click("focus.fullscreen"), None),
        (900, None, (f"{tag}: fullscreen again (from maximized)", lambda: is_fullscreen())),
        (0, controller.on_hotkey, None),
        (500, None, (f"{tag}: the hotkey in fullscreen leaves the zen view and hides the app to the tray (the overlay stays closed)",
                     lambda: not controller.full_visible() and not controller.overlay_visible() and full.property("zenActive") is False
                     and not controller.full_is_fullscreen())),
        (0, controller.open_full_from_tray, None),
        (900, None, (f"{tag}: showing the Full App again gives the maximized window, not a fullscreen one",
                     lambda: full.visibility() == SEEN.Maximized and geometry() == available() and full.property("zenActive") is False)),
        (0, full.showNormal, None),
        (800, None, (f"{tag}: the window is back to normal afterwards (saved state restored)",
                     lambda: full.visibility() == SEEN.Windowed and not disk().maximized)),
        (0, lambda: full.setProperty("zenCalmDelay", 3000), None),
        (0, controller.hide_full, None),
        # ── overlay size ──
        (0, lambda: None, (f"{tag}: a saved overlay size of 0.95 loads as 0.90 in a fresh Store; 0.7 stays",
                           lambda: abs(stored_size_loads(0.95) - 0.90) < 1e-9 and abs(stored_size_loads(0.7) - 0.7) < 1e-9)),
    ]


