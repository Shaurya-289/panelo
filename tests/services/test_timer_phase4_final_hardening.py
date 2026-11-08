from __future__ import annotations

from datetime import timedelta

import pytest

from panelo.core.exceptions import InvalidStateTransitionError, StaleVersionError
from panelo.schemas.timers import CreateTimer, TimerRunQuery, UpdateTimer


def _watch(application):
    events = []
    for kind in (
        "TIMER_UPDATED",
        "TIMER_STARTED",
        "TIMER_PAUSED",
        "TIMER_RESUMED",
        "TIMER_RESET",
        "TIMER_COMPLETED",
        "TIMER_CANCELLED",
        "TIMER_DELETED",
        "TIMER_RESTORED",
    ):
        application.unit_of_work_factory()._event_bus.subscribe(kind, events.append)
    return events


@pytest.mark.parametrize("command", ("pause", "resume", "complete", "cancel", "reset"))
def test_forbidden_ready_commands_are_side_effect_free(application, clock, command: str) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Ready", planned_duration_ms=100))
    before = application.timers.get_timer(timer.id)
    events = _watch(application)
    with pytest.raises(InvalidStateTransitionError):
        getattr(application.timers, f"{command}_timer")(timer.id, timer.version)
    assert application.timers.get_timer(timer.id) == before
    assert application.timers.list_timer_runs(timer.id) == ()
    assert events == []


@pytest.mark.parametrize("command", ("start", "resume", "delete"))
def test_forbidden_running_commands_are_side_effect_free(application, clock, command: str) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Running", planned_duration_ms=100))
    running = application.timers.start_timer(timer.id, timer.version)
    before = application.timers.get_timer(timer.id)
    events = _watch(application)
    with pytest.raises(InvalidStateTransitionError):
        getattr(application.timers, f"{command}_timer")(timer.id, running.version)
    assert application.timers.get_timer(timer.id) == before
    assert events == []


def test_stale_timer_commands_do_not_change_persisted_state(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Stale", planned_duration_ms=100))
    before = application.timers.get_timer(timer.id)
    events = _watch(application)
    with pytest.raises(StaleVersionError):
        application.timers.update_timer(timer.id, UpdateTimer(expected_version=2, name="No"))
    with pytest.raises(StaleVersionError):
        application.timers.start_timer(timer.id, 2)
    assert application.timers.get_timer(timer.id) == before
    assert application.timers.list_timer_runs(timer.id) == ()
    assert events == []


@pytest.mark.parametrize(("terminal", "outcome"), (("cancel", "CANCELLED"), ("reset", "RESET")))
def test_cancel_and_active_reset_close_the_open_run(
    application, clock, terminal: str, outcome: str
) -> None:
    timer = application.timers.create_timer(CreateTimer(name=terminal, planned_duration_ms=100))
    running = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=10)
    result = getattr(application.timers, f"{terminal}_timer")(timer.id, running.version)
    run = application.timers.list_timer_runs(timer.id, TimerRunQuery())[0]
    assert run.outcome == outcome and run.actual_duration_ms == 10
    assert run.ended_at_utc == clock.now
    if terminal == "reset":
        assert result.state == "READY" and result.elapsed_before_run_ms == 0
        assert application.timers.list_timer_runs(timer.id, TimerRunQuery())[0] == run


def test_paused_timer_does_not_advance_after_application_recreation(
    application, app_config, clock
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Restart", planned_duration_ms=100))
    started = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=10)
    paused = application.timers.pause_timer(timer.id, started.version)
    application.close()
    clock.now += timedelta(days=1)
    from panelo.bootstrap import create_application

    restarted = create_application(app_config, clock)
    try:
        view = restarted.timers.get_timer(timer.id)
        assert view.state == "PAUSED" and view.effective_elapsed_ms == paused.effective_elapsed_ms
        assert view.deadline_at_utc is None and view.version == paused.version
    finally:
        restarted.close()
