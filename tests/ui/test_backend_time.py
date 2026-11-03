"""Timers, the focus cycle, the scheduler, backups and settings through the bridge."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from PySide6.QtWidgets import QApplication

from productivity_app.bootstrap import Application
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.reminders import CreateReminder
from productivity_app.ui.bridge.backend import Backend
from productivity_app.ui.bridge.scheduler import Scheduler
from tests.conftest import FakeClock
from tests.ui.conftest import listing, ms


def timer(backend: Backend, name: str) -> dict[str, Any]:
    return next(t for t in listing(backend, "timers") if t["name"] == name)


def focus(backend: Backend) -> dict[str, Any]:
    result: dict[str, Any] = listing(backend, "focus")  # type: ignore[assignment]
    return result


# -- Timers ---------------------------------------------------------------------------------


def test_several_timers_run_at_once(backend: Backend, clock: FakeClock) -> None:
    backend.addTimer("Tea", 300, True)
    clock.now += timedelta(seconds=1)
    backend.addTimer("Laundry", 2700, True)
    clock.now += timedelta(seconds=1)
    backend.addTimer("Pasta", 600, False)
    clock.now += timedelta(seconds=100)
    backend.refresh("timers")
    tea, laundry, pasta = timer(backend, "Tea"), timer(backend, "Laundry"), timer(backend, "Pasta")
    assert (tea["running"], laundry["running"], pasta["running"]) == (True, True, False)
    assert (tea["remaining"], laundry["remaining"], pasta["remaining"]) == (198, 2599, 600)
    assert tea["total"] == 300 and tea["deadline"] is not None and pasta["deadline"] is None
    assert [t["name"] for t in listing(backend, "timers")] == ["Tea", "Laundry", "Pasta"]


def test_pause_resume_reset_and_delete_a_running_timer(backend: Backend, clock: FakeClock) -> None:
    timer_id = backend.addTimer("Tea", 300, True)
    clock.now += timedelta(seconds=60)
    backend.toggleTimer(timer_id)
    paused = timer(backend, "Tea")
    assert (paused["running"], paused["remaining"], paused["state"]) == (False, 240, "PAUSED")
    clock.now += timedelta(seconds=500)
    backend.toggleTimer(timer_id)
    assert timer(backend, "Tea")["running"] is True and timer(backend, "Tea")["remaining"] == 240
    backend.resetTimer(timer_id)
    reset = timer(backend, "Tea")
    assert (reset["running"], reset["remaining"], reset["state"]) == (False, 300, "READY")
    backend.toggleTimer(timer_id)
    backend.deleteTimer(timer_id)  # a running timer is cancelled, then deleted
    assert listing(backend, "timers") == []


def test_presets_are_seeded_and_start_a_named_timer(backend: Backend) -> None:
    presets = listing(backend, "presets")
    assert [(p["label"], p["ms"]) for p in presets] == [
        ("5m", 300000), ("10m", 600000), ("15m", 900000), ("1h", 3600000)
    ]
    backend.startPreset(presets[0]["id"])
    backend.startPreset(presets[3]["id"])
    assert (timer(backend, "5 min timer")["total"], timer(backend, "5 min timer")["running"]) == (300, True)
    assert timer(backend, "1 h timer")["total"] == 3600


def test_a_finished_timer_is_announced_and_can_restart(backend: Backend, clock: FakeClock) -> None:
    announced: list[str] = []
    backend.timerFinished.connect(announced.append)
    timer_id = backend.addTimer("Tea", 60, True)
    backend.addTimer("Long", 3600, True)
    clock.now += timedelta(seconds=61)
    backend.reconcile_timers()
    assert announced == ["Tea"]
    done = timer(backend, "Tea")
    assert (done["state"], done["remaining"], done["running"]) == ("FINISHED", 0, False)
    assert timer(backend, "Long")["running"] is True
    backend.reconcile_timers()
    assert announced == ["Tea"]  # once
    backend.toggleTimer(timer_id)  # starting a finished timer runs it again from the top
    again = timer(backend, "Tea")
    assert (again["running"], again["remaining"]) == (True, 60)


# -- Focus ----------------------------------------------------------------------------------


def test_focus_starts_with_no_cycle_and_the_settings_length(backend: Backend) -> None:
    state = focus(backend)
    assert (state["active"], state["phase"], state["total"], state["running"]) == (False, 0, 1500, False)


def test_focus_cycle_runs_phase_by_phase(backend: Backend, clock: FakeClock) -> None:
    messages: list[str] = []
    backend.focusCompleted.connect(messages.append)
    backend.focusSetRunning(True)
    state = focus(backend)
    assert (state["active"], state["running"], state["phase"], state["remaining"]) == (True, True, 0, 1500)
    assert listing(backend, "timers") == []  # a focus phase is not one of the timers
    backend.focusSetRunning(False)
    assert focus(backend)["running"] is False
    clock.now += timedelta(minutes=10)  # paused: no time passes for it
    backend.focusSetRunning(True)
    assert focus(backend)["remaining"] == 1500
    clock.now += timedelta(minutes=26)
    backend.reconcile_timers()
    assert messages == ["Focus session done — time for a 5 min break"]
    state = focus(backend)
    assert (state["phase"], state["running"], state["total"], state["remaining"]) == (1, False, 300, 300)
    stats = listing(backend, "stats")
    assert (stats["sessionsToday"], stats["minutesToday"], stats["weekMinutes"], stats["streak"]) == (  # type: ignore[call-overload]
        1, 25, 25, 1
    )


def test_focus_skip_goes_through_all_eight_phases(backend: Backend) -> None:
    seen = []
    for _ in range(8):
        backend.focusSkip()
        seen.append(focus(backend)["phase"] if focus(backend)["active"] else 0)
    assert seen == [1, 2, 3, 4, 5, 6, 7, 0]
    assert focus(backend)["active"] is False


def test_skipping_a_running_phase_keeps_running(backend: Backend) -> None:
    backend.focusSetRunning(True)
    backend.focusSkip()
    state = focus(backend)
    assert (state["phase"], state["running"], state["total"]) == (1, True, 300)


def test_focus_reset_and_start_over(backend: Backend, clock: FakeClock) -> None:
    backend.focusSkip()
    backend.focusSkip()  # phase 2, a fresh focus session
    backend.focusSetRunning(True)
    clock.now += timedelta(minutes=5)
    backend.focusReset()
    state = focus(backend)
    assert (state["phase"], state["running"], state["remaining"]) == (2, False, 1500)
    backend.focusStartOver()
    state = focus(backend)
    assert (state["active"], state["phase"], state["running"]) == (False, 0, False)


def test_a_focus_phase_that_ended_while_closed_moves_on_at_start(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    messages: list[str] = []
    backend.focusCompleted.connect(messages.append)
    backend.focusSetRunning(True)
    clock.now += timedelta(minutes=30)
    application.timers.reconcile_expired_timers()  # what start-up does before the bridge exists
    Scheduler(backend).startup()
    assert messages == ["Focus session done — time for a 5 min break"]
    assert focus(backend)["phase"] == 1


def test_lengths_of_a_new_cycle_follow_the_settings(backend: Backend) -> None:
    backend.set_setting_now("focusMs", 10 * 60000)
    assert focus(backend)["total"] == 600
    backend.focusSetRunning(True)
    assert focus(backend)["total"] == 600


# -- The scheduler --------------------------------------------------------------------------


def reminder_due(application: Application, clock: FakeClock, title: str, seconds: int) -> None:
    application.reminders.create_reminder(
        CreateReminder(title=title, due_at_utc=clock.now + timedelta(seconds=seconds), scheduled_timezone="UTC")
    )


def test_scheduler_fires_a_due_reminder_once(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    fired: list[dict[str, Any]] = []
    backend.reminderFired.connect(fired.append)
    scheduler = Scheduler(backend)
    reminder_due(application, clock, "Soon", 60)
    scheduler.tick()
    assert fired == []
    clock.now += timedelta(seconds=61)
    scheduler.tick()
    assert [r["title"] for r in fired] == ["Soon"]
    scheduler.tick()
    assert len(fired) == 1


def test_a_reminder_created_already_due_shows_without_an_alert(
    backend: Backend, clock: FakeClock
) -> None:
    fired: list[dict[str, Any]] = []
    backend.reminderFired.connect(fired.append)
    backend.addReminder("Late", ms(clock.now - timedelta(minutes=1)), {})
    Scheduler(backend).tick()
    assert fired == []
    assert listing(backend, "reminders")[0]["due"] < ms(clock.now)


def test_a_snoozed_reminder_alerts_again(backend: Backend, clock: FakeClock) -> None:
    fired: list[dict[str, Any]] = []
    backend.reminderFired.connect(fired.append)
    scheduler = Scheduler(backend)
    reminder_id = backend.addReminder("Again", ms(clock.now + timedelta(seconds=30)), {})
    clock.now += timedelta(seconds=31)
    scheduler.tick()
    backend.snoozeReminder(reminder_id)
    clock.now += timedelta(minutes=11)
    scheduler.tick()
    assert [r["title"] for r in fired] == ["Again", "Again"]


def test_waking_up_catches_up_on_everything(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    fired: list[dict[str, Any]] = []
    finished: list[str] = []
    backend.reminderFired.connect(fired.append)
    backend.timerFinished.connect(finished.append)
    scheduler = Scheduler(backend)
    reminder_due(application, clock, "Slept through", 600)
    backend.addTimer("Tea", 300, True)
    clock.now += timedelta(hours=3)  # the PC was asleep
    scheduler.catch_up()
    assert [r["title"] for r in fired] == ["Slept through"] and finished == ["Tea"]


def test_a_precise_wake_up_is_armed_for_a_close_deadline(backend: Backend, clock: FakeClock) -> None:
    scheduler = Scheduler(backend)
    backend.addTimer("Soon", 1, True)
    backend.addTimer("Far", 3600, True)
    scheduler.tick()
    assert not scheduler._precise.isActive()  # a full second away: the heartbeat will do
    clock.now += timedelta(milliseconds=400)
    scheduler.tick()
    assert scheduler._precise.isActive()


def test_startup_purges_the_trash_and_makes_the_daily_backup(
    backend: Backend, application: Application, clock: FakeClock
) -> None:
    note_id = backend.addNote("ancient", "", "none")
    clock.now -= timedelta(days=45)
    backend.deleteNote(note_id)
    clock.now += timedelta(days=45)
    scheduler = Scheduler(backend)
    scheduler.startup()
    assert listing(backend, "trash") == []
    backups = listing(backend, "backups")
    assert [b["kind"] for b in backups] == ["auto"]
    scheduler.hourly()
    assert len(listing(backend, "backups")) == 1  # not due again
    clock.now += timedelta(hours=25)
    scheduler.hourly()
    assert [b["kind"] for b in listing(backend, "backups")] == ["auto", "auto"]


def test_auto_backups_are_pruned_to_the_number_kept(backend: Backend, clock: FakeClock) -> None:
    backend.set_setting_now("keepBackups", 2)
    scheduler = Scheduler(backend)
    for _ in range(4):
        scheduler.hourly()
        clock.now += timedelta(hours=25)
    assert len(listing(backend, "backups")) == 2


def test_auto_backup_can_be_switched_off(backend: Backend) -> None:
    backend.set_setting_now("autoBackup", False)
    Scheduler(backend).hourly()
    assert listing(backend, "backups") == []


# -- Backups --------------------------------------------------------------------------------


def test_manual_backup_trims_to_the_number_kept(backend: Backend, clock: FakeClock) -> None:
    backend.set_setting_now("keepBackups", 2)
    for _ in range(3):
        backend.backUpNow()
        clock.now += timedelta(minutes=1)
    backups = listing(backend, "backups")
    assert len(backups) == 2 and backups[0]["at"] > backups[1]["at"]
    backend.deleteBackup(backups[0]["id"])
    assert len(listing(backend, "backups")) == 1


def test_restore_replaces_the_data_and_keeps_events_flowing(
    backend: Backend, application: Application, clock: FakeClock, qapp: QApplication
) -> None:
    restored: list[str] = []
    backend.backupRestored.connect(restored.append)
    backend.addNote("kept", "", "none")
    backend.backUpNow()
    clock.now += timedelta(minutes=1)
    backend.addNote("lost", "", "none")
    backend.restoreBackup(listing(backend, "backups")[0]["id"])
    assert [n["title"] for n in listing(backend, "notes")] == ["kept"]
    assert len(restored) == 1 and "restored" in restored[0]
    assert backend.busy is False
    application.notes.create_note(CreateNote(title="after"))  # the new event bus is subscribed
    qapp.processEvents()
    assert {n["title"] for n in listing(backend, "notes")} == {"kept", "after"}
    assert len(listing(backend, "backups")) >= 2  # the safety backup made before the restore


# -- Settings -------------------------------------------------------------------------------


def test_settings_are_mirrors_that_save_after_a_pause(
    backend: Backend, application: Application, qapp: QApplication
) -> None:
    saved: dict[str, Any] = listing(backend, "settings")  # type: ignore[assignment]
    assert saved["hotkey"] == "Ctrl+Alt+Space" and saved["backdropBlur"] == 1.0
    backend.setSetting("backdropBlur", 0.5)
    backend.setSetting("backdropBlur", 0.55)
    backend.setSetting("closeHidesToTray", False)
    assert application.settings.get_settings().backdrop_blur == 100  # not yet
    assert backend._settings_timer.isActive()
    backend.flush_settings()
    stored = application.settings.get_settings()
    assert (stored.backdrop_blur, stored.minimize_to_tray) == (55, False)
    assert listing(backend, "settings")["backdropBlur"] == 0.55  # type: ignore[call-overload]


def test_setting_values_are_converted_and_validated(backend: Backend, application: Application) -> None:
    for key, value in (
        ("notificationStyle", "both"), ("overlaySize", 0.6), ("filesViewMode", "grid"),
        ("userName", "Riya"), ("tourDone", True), ("keepBackups", 14), ("shortMs", 7 * 60000),
        ("reminderAlerts", False), ("overlayLayoutJson", '{"a":1}'),
    ):
        backend.set_setting_now(key, value)
    s = application.settings.get_settings()
    assert (s.notification_style.value, s.overlay_size_pct, s.files_view_mode.value) == ("BOTH", 60, "GRID")
    assert (s.user_name, s.tour_done, s.keep_backups) == ("Riya", True, 14)
    assert (s.short_break_duration_ms, s.notifications_enabled, s.overlay_layout_json) == (
        420000, False, '{"a":1}'
    )
    backend.set_setting_now("noSuchSetting", 1)  # ignored


def test_start_with_windows_goes_through_the_shell_handler(backend: Backend) -> None:
    notices: list[str] = []
    backend.notice.connect(notices.append)
    backend.startup_handler = lambda enabled: "no launcher"
    backend.setSetting("startWithWindows", True)
    assert notices == ["no launcher"]
    assert listing(backend, "settings")["startWithWindows"] is False  # type: ignore[call-overload]
    seen: list[bool] = []
    backend.startup_handler = lambda enabled: (seen.append(enabled), "")[1]
    backend.setSetting("startWithWindows", True)
    assert seen == [True]


def test_settings_changed_elsewhere_reach_the_pages(
    backend: Backend, application: Application, qapp: QApplication
) -> None:
    from productivity_app.schemas.settings import SettingsPatch

    version = application.settings.get_settings().version
    application.settings.update_settings(SettingsPatch(expected_version=version, user_name="Mira"))
    qapp.processEvents()
    assert listing(backend, "settings")["userName"] == "Mira"  # type: ignore[call-overload]
