"""Several timers at once: no single-active rule, `list_active_timers`, focus coexistence."""

from __future__ import annotations

from datetime import timedelta

from productivity_app.schemas.focus import (
    AdvanceFocusSequence,
    CreateFocusSequence,
    CreateFocusSession,
)
from productivity_app.schemas.timers import CreateTimer


def _timer(application, name: str, ms: int = 60_000):
    return application.timers.create_timer(CreateTimer(name=name, planned_duration_ms=ms))


def test_any_number_of_timers_can_run_at_once(application, clock) -> None:
    timers = [_timer(application, f"T{index}") for index in range(5)]
    running = [application.timers.start_timer(item.id, item.version) for item in timers]
    assert all(item.state == "RUNNING" for item in running)
    assert {item.id for item in application.timers.list_active_timers()} == {item.id for item in timers}
    # each has its own open run
    for item in timers:
        clock.now += timedelta(seconds=1)
        paused = application.timers.pause_timer(item.id, 2)
        resumed = application.timers.resume_timer(item.id, paused.version)
        assert resumed.state == "RUNNING"


def test_list_active_timers_has_running_and_paused_only_newest_start_first(application, clock) -> None:
    ready = _timer(application, "ready")
    finished = _timer(application, "finished")
    cancelled = _timer(application, "cancelled")
    deleted = _timer(application, "deleted")
    first = _timer(application, "first")
    second = _timer(application, "second")
    third = _timer(application, "third")

    started = {}
    for item in (finished, cancelled, first, second, third):
        clock.now += timedelta(seconds=1)
        started[item.id] = application.timers.start_timer(item.id, item.version)
    application.timers.complete_timer(finished.id, started[finished.id].version)
    application.timers.cancel_timer(cancelled.id, started[cancelled.id].version)
    application.timers.pause_timer(second.id, started[second.id].version)
    application.timers.delete_timer(deleted.id, deleted.version)

    active = application.timers.list_active_timers()
    assert [(item.id, item.state) for item in active] == [
        (third.id, "RUNNING"),
        (second.id, "PAUSED"),
        (first.id, "RUNNING"),
    ]
    assert ready.id not in {item.id for item in active}
    assert application.timers.list_active_timers()[0].effective_elapsed_ms >= 0


def test_an_expired_timer_is_still_listed_until_it_is_reconciled(application, clock) -> None:
    short = _timer(application, "short", 1_000)
    application.timers.start_timer(short.id, short.version)
    clock.now += timedelta(seconds=5)
    (listed,) = application.timers.list_active_timers()
    assert listed.id == short.id and listed.needs_reconciliation and listed.effective_state == "FINISHED"
    application.timers.reconcile_expired_timers()
    assert application.timers.list_active_timers() == ()


def test_no_active_timers_gives_an_empty_tuple(application) -> None:
    _timer(application, "never started")
    assert application.timers.list_active_timers() == ()


def test_a_focus_sequence_runs_alongside_other_timers(application, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    other = _timer(application, "Laundry", 10_800_000)
    other_run = application.timers.start_timer(other.id, other.version)
    phase = sequence.current_session.timer
    focus_run = application.timers.start_timer(phase.id, phase.version)
    clock.now += timedelta(minutes=10)
    standalone = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
    standalone_run = application.timers.start_timer(standalone.timer.id, standalone.timer.version)
    assert {item.id for item in application.timers.list_active_timers()} == {
        other.id, phase.id, standalone.timer.id
    }

    # the focus phase finishes while the others keep going, and the sequence advances
    clock.now += timedelta(minutes=15)
    (completed,) = application.timers.reconcile_expired_timers()
    assert completed.id == phase.id and completed.state == "FINISHED"
    advanced = application.focus.advance_sequence(
        sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
    )
    assert advanced.current_phase_index == 1
    assert application.timers.get_timer(other.id).state == "RUNNING"
    # the break phase can start while those two are still active
    break_timer = advanced.current_session.timer
    started = application.timers.start_timer(break_timer.id, break_timer.version)
    assert started.state == "RUNNING"
    assert other_run.state == focus_run.state == standalone_run.state == "RUNNING"
    assert {item.id for item in application.timers.list_active_timers()} == {
        other.id, standalone.timer.id, break_timer.id
    }


def test_stats_still_count_a_focus_session_that_ran_beside_another_timer(application, clock) -> None:
    tea = _timer(application, "Tea", 600_000)
    application.timers.start_timer(tea.id, tea.version)
    session = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
    run = application.timers.start_timer(session.timer.id, session.timer.version)
    clock.now += timedelta(minutes=25)
    application.timers.complete_timer(run.id, run.version)
    (today,) = application.focus.daily_totals(1, "UTC")
    assert (today.sessions, today.minutes) == (1, 25)
