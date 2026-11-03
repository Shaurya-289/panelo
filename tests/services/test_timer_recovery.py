from __future__ import annotations

from datetime import timedelta

from productivity_app.schemas.timers import CreateTimer


def test_running_timer_recovers_natural_completion_on_bounded_reconcile(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Recover", planned_duration_ms=1_000))
    started = application.timers.start_timer(timer.id, timer.version)
    deadline = started.deadline_at_utc
    assert deadline is not None
    clock.now += timedelta(milliseconds=2_000)
    recovered = application.timers.reconcile_expired_timer()
    assert recovered is not None
    assert recovered.effective_elapsed_ms == 1_000
    assert recovered.ended_at_utc == deadline
    assert application.timers.reconcile_expired_timer() is None
