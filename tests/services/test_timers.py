from __future__ import annotations

from datetime import timedelta

import pytest

from productivity_app.core.exceptions import ActiveTimerConflictError, InvalidStateTransitionError
from productivity_app.schemas.timers import CreateTimer


def test_timer_lifecycle_uses_anchor_elapsed_time(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Focus", planned_duration_ms=10_000))
    assert timer.state == "READY"
    started = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=2_500, microseconds=999)
    paused = application.timers.pause_timer(started.id, started.version)
    assert paused.state == "PAUSED"
    assert paused.effective_elapsed_ms == 2_500
    clock.now += timedelta(hours=2)
    assert application.timers.get_timer(timer.id).effective_elapsed_ms == 2_500
    resumed = application.timers.resume_timer(timer.id, paused.version)
    clock.now += timedelta(milliseconds=7_500)
    finished = application.timers.complete_timer(timer.id, resumed.version)
    assert finished.state == "FINISHED"
    assert finished.effective_elapsed_ms == 10_000
    runs = application.timers.list_timer_runs(timer.id)
    assert len(runs) == 1 and runs[0].outcome == "FINISHED"


def test_timer_active_slot_and_derived_expiry_are_not_writes(application, clock) -> None:
    first = application.timers.create_timer(CreateTimer(name="First", planned_duration_ms=1_000))
    second = application.timers.create_timer(CreateTimer(name="Second", planned_duration_ms=1_000))
    started = application.timers.start_timer(first.id, first.version)
    with pytest.raises(ActiveTimerConflictError):
        application.timers.start_timer(second.id, second.version)
    clock.now += timedelta(milliseconds=1_000)
    read = application.timers.get_timer(first.id)
    assert read.state == "RUNNING" and read.effective_state == "FINISHED"
    assert read.needs_reconciliation and read.version == started.version
    with pytest.raises(InvalidStateTransitionError):
        application.timers.pause_timer(first.id, started.version)
    reconciled = application.timers.reconcile_expired_timer()
    assert reconciled is not None and reconciled.state == "FINISHED"
    assert reconciled.ended_at_utc == started.deadline_at_utc


def test_cancel_reset_delete_restore(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Focus", planned_duration_ms=5_000))
    started = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=10)
    cancelled = application.timers.cancel_timer(timer.id, started.version)
    reset = application.timers.reset_timer(timer.id, cancelled.version)
    assert reset.state == "READY" and reset.elapsed_before_run_ms == 0
    deleted = application.timers.delete_timer(timer.id, reset.version)
    restored = application.timers.restore_timer(timer.id, deleted.version)
    assert restored.deleted_at_utc is None and restored.state == "READY"
