"""The scheduler: one heartbeat that keeps the app's time-driven work going.

Every second (and exactly when a running timer ends) it finishes timers that ran out, moves a
Pomodoro phase on, and raises the alert for reminders that came due.  Once a minute it
refreshes what depends on the date; at start-up and then hourly it purges the Trash (30 days)
and takes the daily automatic backup.  A jump of the wall clock (the PC slept) runs everything
at once, so nothing that came due while asleep is missed.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer

from productivity_app.ui.bridge.backend import Backend

HEARTBEAT_MS = 1000
WAKE_GAP_SECONDS = 10.0
HOUR_MS = 3_600_000
MINUTE_MS = 60_000


class Scheduler(QObject):
    def __init__(self, backend: Backend, *, heartbeat_ms: int = HEARTBEAT_MS) -> None:
        super().__init__()
        self.backend = backend
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(heartbeat_ms)
        self._heartbeat.timeout.connect(self.tick)
        self._precise = QTimer(self)
        self._precise.setSingleShot(True)
        self._precise.timeout.connect(self.tick)
        self._last_wall = time.time()
        self._last_minute = backend.now_ms() // MINUTE_MS
        self._last_hourly: int | None = None

    def start(self) -> None:
        """Run the start-up work, then keep ticking."""
        self.startup()
        self._heartbeat.start()

    def stop(self) -> None:
        self._heartbeat.stop()
        self._precise.stop()

    def startup(self) -> None:
        if self.backend.busy:
            return
        self.backend.reconcile_timers()
        self.backend.focus_catch_up()
        self.hourly()

    def hourly(self) -> None:
        if self.backend.busy:
            return
        self._last_hourly = self.backend.now_ms()
        self.backend.purgeTrash()
        self.backend.auto_backup_if_due()

    def catch_up(self) -> None:
        """Everything that may have happened while the PC slept."""
        if self.backend.busy:
            return
        self.backend.reconcile_timers()
        self.backend.fire_due_reminders()
        self.backend.refresh_clock_dependent()
        self.hourly()

    def tick(self) -> None:
        backend = self.backend
        if backend.busy:
            return                      # a restore is replacing the database
        wall = time.time()
        slept = wall - self._last_wall > WAKE_GAP_SECONDS
        self._last_wall = wall
        if slept:
            self.catch_up()
        else:
            backend.reconcile_timers()
            backend.fire_due_reminders()
            now = backend.now_ms()
            if now // MINUTE_MS != self._last_minute:
                self._last_minute = now // MINUTE_MS
                backend.refresh_clock_dependent()
            if self._last_hourly is None or now - self._last_hourly >= HOUR_MS:
                self.hourly()
        self._arm_precise()

    def _arm_precise(self) -> None:
        """A running timer that ends before the next heartbeat is finished on time."""
        deadline = self.backend.next_deadline_ms()
        if deadline is None:
            return
        delay = deadline - self.backend.now_ms()
        if delay < HEARTBEAT_MS:
            self._precise.start(max(0, delay) + 15)
