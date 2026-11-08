"""The development commands: ``--selftest`` and ``--screenshot``.

``--selftest`` builds the whole app on a temporary, seeded database and library, then drives the
real windows with timed sequences of mouse and key events, asserting what the pages show and what
the services hold.  ``--screenshot`` renders one window state (window-only, with the fake
backdrop) and exits.  Neither can touch your data: see ``harness.py``.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from panelo.ui.selftest import (
    steps_core,
    steps_layout,
    steps_round7,
    steps_views,
    unit_checks,
)
from panelo.ui.selftest.driver import js
from panelo.ui.selftest.harness import SelfTestEnv, make_env

if TYPE_CHECKING:
    from panelo.desktop.main import DesktopOptions

OVERLAY_STATES = [
    "overlay", "overlay-search", "overlay-editor", "overlay-morph", "overlay-carousel", "timepicker",
    "overlay-compose", "files-overlay", "files-search", "focus-break",
    "layout-picker", "layout-drag-swap", "layout-drag-split", "layout-drag-invalid", "layout-topbar",
    "layout-big", "layout-rows", "layout-columns", "overlay-greeting", "overlay-greeting-12h",
]
FULL_STATES = [
    "full", "editor", "drawer", "compose",
    "files-full-grid", "files-full-list", "files-context", "files-drop", "files-move", "files-rename",
    "tasks", "tasks-quickadd", "tasks-drag", "task-editor", "task-datepicker",
    "reminders", "reminder-editor", "focus", "focus-lengths", "focus-newtimer",
    "settings-top", "settings-bottom", "settings-hotkey-recording", "trash", "trash-empty",
    "settings-sound-picker", "settings-appearance", "focus-fullscreen", "focus-fullscreen-calm",
    "reminders-repeat", "reminder-repeat-popup", "search-dropdown", "takenote-noicon",
    "tour-1", "tour-2", "tour-3", "settings-name",
]
STATES = [*OVERLAY_STATES, *FULL_STATES, "toast", "toast-focus"]


# -- Screenshots ----------------------------------------------------------------------------


def run_screenshot(env: SelfTestEnv, state: str, path: Path, delay: int) -> None:
    """Show one state and save the window's own render (never the real screen)."""
    controller, store = env.controller, env.desktop.store
    controller.on_shot = lambda _path: QTimer.singleShot(0, controller.quit)
    if state in OVERLAY_STATES:
        controller.open_overlay()
        if state == "focus-break":
            QTimer.singleShot(500, lambda: _focus_break_demo(env))
            delay = max(delay, 2600)
        else:
            QTimer.singleShot(500, lambda: controller.overlay.demo(state))
        QTimer.singleShot(delay, lambda: controller.overlay.saveShot(str(path)))
    elif state in ("toast", "toast-focus"):
        if state == "toast":
            QTimer.singleShot(300, lambda: controller.on_reminder_fired(
                {"id": "", "title": "Stand up and stretch"}))
        else:
            QTimer.singleShot(300, lambda: controller.on_focus_completed(
                "Focus session done — time for a 5 min break"))
        QTimer.singleShot(delay, lambda: controller.toast.saveShot(str(path)))
    elif state in ("focus-fullscreen", "focus-fullscreen-calm"):
        controller.full.setProperty("zenCalmDelay", 400 if state.endswith("calm") else 600000)
        controller.show_full("focus")
        QTimer.singleShot(500, controller.enter_fullscreen)
        QTimer.singleShot(max(delay, 2200), lambda: controller.full.saveShot(str(path)))
    else:
        if state.startswith("tour-"):
            store.setProperty("tourDone", False)
        controller.show_full("notes")
        QTimer.singleShot(400, lambda: controller.full.demo(state))
        QTimer.singleShot(delay, lambda: controller.full.saveShot(str(path)))


def _focus_break_demo(env: SelfTestEnv) -> None:
    """The overlay's timer panel on a break: skip the first focus session."""
    env.backend.focusSkip()


# -- The self-test --------------------------------------------------------------------------


def basic_steps(env: SelfTestEnv, check: Callable[[str, bool], None]) -> list[tuple[Any, ...]]:
    """The hotkey state machine, the library, the Pomodoro cycle and taking a note."""
    controller, store = env.controller, env.desktop.store
    backend = env.backend
    overlay = controller.overlay

    def set_lengths(focus_s: int, short_s: int, long_s: int) -> None:
        store.setProperty("focusMs", focus_s * 1000)
        store.setProperty("shortMs", short_s * 1000)
        store.setProperty("longMs", long_s * 1000)
        backend.flush_settings()

    def start_pomodoro() -> None:
        set_lengths(2, 3, 4)
        store.focusStartOver()
        store.setProperty("focusRunning", True)

    def pomodoro_advanced() -> bool:
        return bool(
            store.property("focusPhase") == 1 and store.property("focusLabel") == "Short break"
            and store.property("focusRunning") is False
            and store.property("focusRemaining") == store.focusDuration(1) == 3
            and controller.focus_toasts == 1
            and controller.last_focus_message.startswith("Focus session done")
        )

    def skip_to_6() -> None:
        for _ in range(5):
            store.focusSkip()

    def new_note_ok() -> bool:
        notes = js(store.property("notes"))
        first = notes[0] if notes else {}
        return bool(first.get("title") == "Selftest title" and first.get("body") == "Selftest body")

    def restore_lengths() -> None:
        store.focusStartOver()
        set_lengths(25 * 60, 5 * 60, 15 * 60)

    return [
        (0, controller.on_hotkey, None),
        (300, None, ("press -> overlay open", lambda: controller.overlay_visible() and controller.overlay_state == "open")),
        (500, controller.on_hotkey, None),
        (500, None, ("press after 500 ms -> overlay closed", lambda: not controller.overlay_visible() and controller.overlay_state == "hidden")),
        (200, controller.on_hotkey, None),
        (200, controller.on_hotkey, None),
        (900, None, ("double-press (200 ms) -> full app visible, overlay hidden", lambda: controller.full_visible() and not controller.overlay_visible() and controller.full.property("section") == "notes")),
        (200, controller.on_hotkey, None),
        (200, None, ("press with full app visible -> full app hidden", lambda: not controller.full_visible() and not controller.overlay_visible())),
        # file library
        (0, lambda: steps_core.library_checks(check), None),
        # pomodoro (short lengths: 2 s focus, 3 s short break, 4 s long break)
        (0, start_pomodoro, None),
        (3200, None, ("pomodoro: phase 0 runs out -> phase 1 'Short break', paused, 3 s left, toast signal fired", pomodoro_advanced)),
        (0, skip_to_6, ("pomodoro: skip through the cycle reaches phase 6 (the last focus session)", lambda: store.property("focusPhase") == 6)),
        (0, store.focusSkip, ("pomodoro: skip 6 -> 7 shows 'Long break' with a fresh 4 s", lambda: store.property("focusPhase") == 7 and store.property("focusLabel") == "Long break" and store.property("focusRemaining") == 4)),
        (0, store.focusSkip, ("pomodoro: skip 7 -> 0 shows 'Session 1 of 4' (the cycle is over)", lambda: store.property("focusPhase") == 0 and store.property("focusLabel") == "Session 1 of 4" and store.property("focusRemaining") == 2)),
        (0, restore_lengths, None),
        # take a note with a title and a body
        (0, lambda: overlay.captureNote("Selftest title", "Selftest body"), ("take note: capture path saves title + body as the newest note", new_note_ok)),
    ]


def build_groups(env: SelfTestEnv, check: Callable[[str, bool], None]) -> dict[str, Callable[[], list[tuple[Any, ...]]]]:
    controller = env.controller
    return {
        "basic": lambda: basic_steps(env, check),
        "ui": lambda: steps_core.ui_steps(env),
        "layout-unit": lambda: [(0, lambda: unit_checks.layout_checks(check, controller.overlay), None)],
        "layout": lambda: steps_layout.layout_steps(env),
        "unit": lambda: [(0, lambda: unit_checks.smartparse_and_dates_checks(check, controller.full), None)],
        "tasks": lambda: steps_views.tasks_steps(env),
        "reminders": lambda: steps_views.reminders_steps(env),
        "focus": lambda: steps_views.focus_steps(env),
        "trash": lambda: steps_views.trash_steps(env),
        "settings": lambda: steps_views.settings_steps(env),
        "round6": lambda: steps_views.round6_steps(env),
        "window": lambda: steps_core.window_state_steps(env),
        "recurrence": lambda: [(0, lambda: unit_checks.recurrence_checks(check, controller.full), None)],
        "repeat": lambda: steps_round7.repeat_steps(env),
        "checklist": lambda: steps_round7.checklist_steps(env),
        "search": lambda: steps_round7.search_steps(env),
        "greeting": lambda: steps_round7.greeting_steps(env),
        "tour": lambda: steps_round7.tour_steps(env),
    }


def run_selftest(env: SelfTestEnv, qt_app: QApplication) -> None:
    """Drive the app with timed steps; the process exit code says whether every check passed."""
    controller = env.controller
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}: {name}", flush=True)

    def state() -> str:
        return (
            f"overlay={controller.overlay_state}/{'visible' if controller.overlay_visible() else 'hidden'}"
            f" full={'visible' if controller.full_visible() else 'hidden'}"
        )

    only = {name for name in os.environ.get("PANELO_SELFTEST_ONLY", "").split(",") if name}
    steps: list[tuple[Any, ...]] = []
    for name, build in build_groups(env, check).items():
        if not only or name in only:
            steps += build()

    def run(index: int) -> None:
        if index >= len(steps):
            controller.quit()
            return
        delay, action, assertion = steps[index]

        def go() -> None:
            try:
                if action is not None:
                    action()
                if assertion is not None:
                    name, predicate = assertion
                    check(name, bool(predicate()))
                    if not results[-1]:
                        print(f"      state: {state()}", flush=True)
            except Exception as exc:
                check(f"step {index} crashed: {exc!r}", False)
            run(index + 1)

        QTimer.singleShot(delay, go)

    def finish() -> None:
        passed = sum(results)
        print(f"selftest: {passed}/{len(results)} passed", flush=True)
        if passed != len(results):
            qt_app.setProperty("selftest_failed", True)

    qt_app.aboutToQuit.connect(finish)
    run(0)


def run_dev_command(options: DesktopOptions, qt_app: QApplication) -> int:
    """``--selftest`` / ``--screenshot``: always on a temporary seeded copy of everything."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = make_env(qt_app, biggest=options.selftest)
    controller = env.controller
    try:
        if options.selftest:
            QTimer.singleShot(0, lambda: controller.prewarm_full(lambda: QTimer.singleShot(100, lambda: run_selftest(env, qt_app))))
        else:
            assert options.screenshot is not None
            state = options.state or "full"
            shot = options.screenshot
            QTimer.singleShot(0, lambda: run_screenshot(env, state, shot, options.delay))
        if options.quit_after:
            QTimer.singleShot(options.quit_after, controller.quit)
        started = time.monotonic()
        code = qt_app.exec()
        QCoreApplication.processEvents()
        if options.selftest:
            print(f"(took {time.monotonic() - started:.0f} s)")
    finally:
        env.cleanup()
    if qt_app.property("selftest_failed"):
        return 1
    return int(code)
