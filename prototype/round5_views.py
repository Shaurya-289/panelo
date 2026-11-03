"""Self-test steps for round 5: Reminders, Focus, Trash and Settings (see round5_tests.py)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from round5_tests import Driver, Step, day_at, js


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


# ── Reminders ────────────────────────────────────────────────────────


def reminders_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store = d.full, d.store
    memo: dict = {}
    tag = "reminders"

    def newest() -> dict:
        return max(d.reminders(), key=lambda r: r["id"])

    def add_due_now(title: str) -> None:
        memo["due_id"] = int(store.addReminder(title, now_ms() - 2000, {}))

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
                     and d.reminder(1)["done"] is False and all(r["id"] != 1 for r in d.rgroup("dueNow"))
                     and any(r["id"] == 1 for r in d.rgroup("today") + d.rgroup("tomorrow")))),
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
                     lambda: full.property("taskEditorOpen") is True and full.property("taskEditorId") == 8
                     and full.property("reminderEditorOpen") is False)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (500, lambda: d.click("reminderLink", 4), None),
        (600, None, (f"{tag}: the link chip of a note reminder opens that note's editor",
                     lambda: full.property("editorOpen") is True and full.property("editorNoteId") == 1)),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (500, None, None),
        # the reminder pop-out
        (0, lambda: d.click("reminderRow", 2), None),
        (600, None, (f"{tag}: clicking a row opens the reminder pop-out",
                     lambda: full.property("reminderEditorOpen") is True and full.property("reminderEditorId") == 2)),
        (0, lambda: d.type_text("!"), None),
        (0, lambda: click_outside(5), None),
        (100, None, (f"{tag}: click-through: clicking outside the pop-out does not open the reminder under the click",
                     lambda: full.property("reminderEditorId") in (2, -1))),
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
                     lambda: full.property("reminderEditorOpen") is False and all(r["id"] != 5 for r in d.reminders()))),
        (0, controller.hide_full, None),
    ]




# ── Focus ────────────────────────────────────────────────────────────


def focus_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store = d.full, d.store
    memo: dict = {}
    tag = "focus"

    def timers() -> list[dict]:
        return js(store.property("timers"))

    def timer(timer_id: int) -> dict:
        return next(t for t in timers() if t["id"] == timer_id)

    def newest_timer() -> dict:
        return max(timers(), key=lambda t: t["id"])

    def history() -> list[dict]:
        return js(store.property("focusHistory"))

    def stats() -> dict:
        return js(store.property("focusStats"))

    def finish_a_session() -> None:
        memo["history"] = len(history())
        memo["stats"] = stats()
        store.focusSetPhase(0)
        store.setProperty("focusRemaining", 1)
        store.setProperty("focusRunning", True)

    def minutes() -> int:
        return round(int(store.property("focusMs")) / 60000)

    return [
        (0, lambda: controller.show_full("focus"), None),
        (600, None, (f"{tag}: the Focus page shows, with the stats strip from the focus history",
                     lambda: full.property("section") == "focus" and stats()["streak"] >= 4 and stats()["sessionsToday"] >= 3
                     and stats()["weekMinutes"] >= 75)),
        # the history grows when a focus phase completes
        (0, finish_a_session, None),
        (1700, None, (f"{tag}: completing a focus phase adds a session to the history and updates the stats",
                      lambda: len(history()) == memo["history"] + 1
                      and stats()["sessionsToday"] == memo["stats"]["sessionsToday"] + 1
                      and stats()["minutesToday"] > memo["stats"]["minutesToday"]
                      and stats()["weekMinutes"] > memo["stats"]["weekMinutes"] and store.property("focusPhase") == 1)),
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
        (300, lambda: store.setProperty("focusRemaining", 10), None),
        (0, lambda: d.click("focus.reset"), None),
        (300, None, (f"{tag}: click-through: clicking outside the new-timer popup closes it and does not press the Reset button under it",
                     lambda: full.property("newTimerOpen") is False and store.property("focusRemaining") == 10)),
        # several timers run at once
        (0, lambda: memo.update(tea=timer(1)["remaining"], laundry=timer(2)["remaining"], pasta=newest_timer()["remaining"]), None),
        (1300, None, (f"{tag}: several timers run at once (Tea, Laundry, the preset and Pasta all counting down)",
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
        (1700, None, (f"{tag}: a finished timer fires the toast path ('Zed timer done')",
                      lambda: controller.timer_toasts == memo["toasts"] + 1 and controller.last_timer_message == "Zed timer done")),
        (0, lambda: controller.toast.dismiss(), None),
        (0, controller.hide_full, None),
    ]


# ── Trash ────────────────────────────────────────────────────────────


def trash_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store, overlay = d.full, d.store, controller.overlay
    memo: dict = {}
    tag = "trash"

    def trash() -> list[dict]:
        return js(store.property("trashItems"))

    def notes_shown() -> int:
        return len(js(full.property("visibleNotes")))

    def ids(kind: str) -> list[int]:
        return [t["id"] for t in trash() if t["kind"] == kind]

    return [
        (0, lambda: controller.show_full("trash"), None),
        (600, None, (f"{tag}: the Trash page lists the trashed notes and tasks (newest first, days left)",
                     lambda: full.property("section") == "trash" and len(trash()) == 4
                     and [t["deletedAt"] for t in trash()] == sorted((t["deletedAt"] for t in trash()), reverse=True)
                     and {t["daysLeft"] for t in trash()} == {28, 25, 10, 3})),
        (0, None, (f"{tag}: items older than 30 days were purged on load",
                   lambda: all(t["id"] != 22 for t in d.tasks()))),
        # deleting a task in its pop-out sends it to the Trash
        (0, lambda: full.setProperty("section", "tasks"), None),
        (300, lambda: full.openTaskById(14), None),
        (600, lambda: d.click("taskEditor.delete"), None),
        (400, None, (f"{tag}: deleting a task in its pop-out moves it to the Trash (gone from the lists)",
                     lambda: 14 in ids("task") and d.task(14)["deletedAt"] is not None
                     and all(t["id"] != 14 for t in d.group("none")) and full.property("taskEditorOpen") is False)),
        # deleting a note in the editor sends it to the Trash too
        (0, lambda: (memo.update(notes=notes_shown(), overlay_notes=int(overlay.property("shownNotes"))),
                     full.setProperty("section", "notes")), None),
        (400, lambda: d.click("note", 0), None),
        (600, None, (f"{tag}: a note opens in the editor", lambda: full.property("editorOpen") is True)),
        (0, lambda: d.click("noteEditorTrash"), None),
        (400, None, (f"{tag}: deleting a note in the editor moves it to the Trash (hidden on the Notes page and in the overlay)",
                     lambda: 1 in ids("note") and notes_shown() == memo["notes"] - 1 and full.property("editorOpen") is False
                     and int(overlay.property("shownNotes")) == memo["overlay_notes"] - 1)),
        # restore and delete forever
        (0, lambda: full.setProperty("section", "trash"), None),
        (400, None, (f"{tag}: the trashed note and task are listed, newest first",
                     lambda: len(trash()) == 6 and trash()[0]["kind"] == "note" and trash()[1]["id"] == 14)),
        (0, lambda: d.click("trashRestore", 0), None),
        (300, None, (f"{tag}: Restore brings the note back (Notes page again, same pin, out of the Trash)",
                     lambda: 1 not in ids("note") and notes_shown() == memo["notes"]
                     and next(n for n in d.notes() if n["id"] == 1)["pinned"] is True
                     and next(n for n in d.notes() if n["id"] == 1)["deletedAt"] is None)),
        (0, lambda: d.click("trashRestore", 0), None),
        (300, None, (f"{tag}: Restore brings the task back into its group (No date)",
                     lambda: 14 not in ids("task") and any(t["id"] == 14 for t in d.group("none")))),
        (0, lambda: store.deleteTask(14), None),
        (200, lambda: d.click("trashForever", 0), None),
        (300, None, (f"{tag}: Delete forever removes the item for good",
                     lambda: all(t["id"] != 14 for t in d.tasks()) and 14 not in ids("task") and len(trash()) == 4)),
        # purge: older than 30 days goes, 29 days stays
        (0, lambda: (store.updateTask(13, {"deletedAt": now_ms() - 31 * 86400000}), store.updateTask(12, {"deletedAt": now_ms() - 29 * 86400000})), None),
        (0, lambda: store.purgeTrash(), None),
        (200, None, (f"{tag}: the purge deletes items older than 30 days and keeps newer ones",
                     lambda: all(t["id"] != 13 for t in d.tasks()) and any(t["id"] == 12 for t in d.tasks()) and 12 in ids("task"))),
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
