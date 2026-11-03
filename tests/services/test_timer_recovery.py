from __future__ import annotations

from datetime import timedelta

from productivity_app.schemas.timers import CreateTimer


def test_running_timer_recovers_natural_completion_on_bounded_reconcile(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Recover", planned_duration_ms=1_000))
    started = application.timers.start_timer(timer.id, timer.version)
    deadline = started.deadline_at_utc
    assert deadline is not None
    clock.now += timedelta(milliseconds=2_000)
    (recovered,) = application.timers.reconcile_expired_timers()
    assert recovered.effective_elapsed_ms == 1_000
    assert recovered.ended_at_utc == deadline
    assert application.timers.reconcile_expired_timers() == ()


def test_every_expired_timer_is_recovered_in_one_pass(application, clock) -> None:
    short = application.timers.create_timer(CreateTimer(name="Short", planned_duration_ms=1_000))
    middle = application.timers.create_timer(CreateTimer(name="Middle", planned_duration_ms=2_000))
    long = application.timers.create_timer(CreateTimer(name="Long", planned_duration_ms=60_000))
    paused = application.timers.create_timer(CreateTimer(name="Paused", planned_duration_ms=1_000))
    for timer in (short, middle, long, paused):
        application.timers.start_timer(timer.id, timer.version)
    paused = application.timers.pause_timer(paused.id, 2)
    clock.now += timedelta(seconds=5)

    recovered = application.timers.reconcile_expired_timers()

    assert [timer.id for timer in recovered] == [short.id, middle.id]
    assert all(timer.state == "FINISHED" and timer.ended_at_utc is not None for timer in recovered)
    assert {timer.id: timer.state for timer in application.timers.list_active_timers()} == {
        long.id: "RUNNING",
        paused.id: "PAUSED",
    }
    assert len(application.timers.list_timer_runs(short.id)) == 1
    assert application.timers.reconcile_expired_timers() == ()


def test_restart_reconciles_timers_that_expired_while_closed(app_config, clock) -> None:
    from productivity_app.bootstrap import create_application

    first = create_application(app_config, clock)
    try:
        timer = first.timers.create_timer(CreateTimer(name="Closed", planned_duration_ms=1_000))
        first.timers.start_timer(timer.id, timer.version)
    finally:
        first.close()
    clock.now += timedelta(hours=1)
    second = create_application(app_config, clock)
    try:
        assert second.timers.get_timer(timer.id).state == "FINISHED"
        assert second.timers.list_active_timers() == ()
    finally:
        second.close()
