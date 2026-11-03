from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from productivity_app.core.exceptions import (
    ActiveTimerConflictError,
    InvalidStateTransitionError,
    StaleVersionError,
)
from productivity_app.schemas.timers import CreateTimer


def test_paused_timer_occupies_global_active_slot(application, clock) -> None:
    first = application.timers.create_timer(CreateTimer(name="A", planned_duration_ms=1_000))
    second = application.timers.create_timer(CreateTimer(name="B", planned_duration_ms=1_000))
    started = application.timers.start_timer(first.id, first.version)
    paused = application.timers.pause_timer(started.id, started.version)
    try:
        application.timers.start_timer(second.id, second.version)
    except ActiveTimerConflictError:
        pass
    else:
        raise AssertionError("A paused timer must occupy the active slot.")
    assert paused.state == "PAUSED"


def test_reset_of_active_timer_serializes_with_competing_start(application) -> None:
    active = application.timers.create_timer(CreateTimer(name="Active", planned_duration_ms=1_000))
    waiting = application.timers.create_timer(
        CreateTimer(name="Waiting", planned_duration_ms=1_000)
    )
    active = application.timers.start_timer(active.id, active.version)
    barrier = Barrier(2)

    def reset() -> object:
        barrier.wait()
        try:
            return application.timers.reset_timer(active.id, active.version)
        except (InvalidStateTransitionError, StaleVersionError) as exc:
            return exc

    def start() -> object:
        barrier.wait()
        try:
            return application.timers.start_timer(waiting.id, waiting.version)
        except (ActiveTimerConflictError, InvalidStateTransitionError, StaleVersionError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        reset_result, start_result = [
            future.result() for future in (executor.submit(reset), executor.submit(start))
        ]
    assert not (isinstance(reset_result, Exception) and isinstance(start_result, Exception))
    active_rows = application.timers.list_timers()
    assert sum(timer.state in {"RUNNING", "PAUSED"} for timer in active_rows) <= 1


def test_pause_complete_and_reconcile_races_leave_single_closed_run(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Race", planned_duration_ms=1_000))
    running = application.timers.start_timer(timer.id, timer.version)
    barrier = Barrier(2)

    def invoke(method: str) -> object:
        barrier.wait()
        try:
            return getattr(application.timers, method)(timer.id, running.version)
        except (InvalidStateTransitionError, StaleVersionError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        pause, complete = [
            future.result()
            for future in (
                executor.submit(invoke, "pause_timer"),
                executor.submit(invoke, "complete_timer"),
            )
        ]
    assert sum(not isinstance(result, Exception) for result in (pause, complete)) == 1
    state = application.timers.get_timer(timer.id)
    if state.state == "PAUSED":
        assert application.timers.list_timer_runs(timer.id) == ()
        state = application.timers.complete_timer(timer.id, state.version)
    assert state.state == "FINISHED"
    assert len(application.timers.list_timer_runs(timer.id)) == 1

    ready = application.timers.reset_timer(timer.id, state.version)
    running = application.timers.start_timer(timer.id, ready.version)
    clock.now += timedelta(seconds=2)
    barrier = Barrier(2)

    def reconcile() -> object:
        barrier.wait()
        return application.timers.reconcile_expired_timer()

    def complete_expired() -> object:
        barrier.wait()
        try:
            return application.timers.complete_timer(timer.id, running.version)
        except (InvalidStateTransitionError, StaleVersionError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in (executor.submit(reconcile), executor.submit(complete_expired))
        ]
    assert sum(result is not None and not isinstance(result, Exception) for result in results) == 1
    assert application.timers.get_timer(timer.id).state == "FINISHED"
    assert len(application.timers.list_timer_runs(timer.id)) == 2
