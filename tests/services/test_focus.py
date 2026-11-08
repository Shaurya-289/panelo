from __future__ import annotations

from datetime import timedelta

import pytest

from panelo.bootstrap import create_application
from panelo.core.exceptions import ConflictError, InvalidStateTransitionError
from panelo.schemas.focus import (
    AdvanceFocusSequence,
    CancelFocusSequence,
    CreateFocusSequence,
    CreateFocusSession,
    FocusStatsQuery,
)
from panelo.schemas.tasks import CreateTask


def test_standalone_focus_session_creates_ready_timer(application) -> None:
    session = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))

    assert session.sequence_id is None
    assert session.phase_index is None
    assert session.timer.state == "READY"
    assert session.timer.planned_duration_ms == 1_500_000


def test_break_session_cannot_link_a_task() -> None:
    with pytest.raises(ValueError, match="Break sessions"):
        CreateFocusSession(phase_kind="SHORT_BREAK", task_id="task")


def test_sequence_uses_fixed_cadence_and_requires_explicit_advance(application, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    assert sequence.current_session.phase_kind == "FOCUS"
    assert sequence.current_session.timer.state == "READY"

    running = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=1)
    finished = application.timers.complete_timer(running.id, running.version)
    advanced = application.focus.advance_sequence(
        sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
    )

    assert finished.state == "FINISHED"
    assert advanced.current_phase_index == 1
    assert advanced.current_session.phase_kind == "SHORT_BREAK"
    assert advanced.current_session.timer.state == "READY"
    assert advanced.current_session.timer.task_id is None


def test_only_one_active_sequence_and_cancel_requires_inactive_timer(application) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    with pytest.raises(ConflictError):
        application.focus.create_sequence(CreateFocusSequence())

    started = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    with pytest.raises(InvalidStateTransitionError):
        application.focus.cancel_sequence(sequence.id, CancelFocusSequence(expected_version=sequence.version))

    cancelled_timer = application.timers.cancel_timer(started.id, started.version)
    cancelled = application.focus.cancel_sequence(
        sequence.id, CancelFocusSequence(expected_version=sequence.version)
    )
    assert cancelled_timer.state == "CANCELLED"
    assert cancelled.state == "CANCELLED"


def test_historical_phase_timer_cannot_restart_or_reset(application, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    first = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=1)
    completed = application.timers.complete_timer(first.id, first.version)
    advanced = application.focus.advance_sequence(
        sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
    )

    with pytest.raises(InvalidStateTransitionError):
        application.timers.reset_timer(completed.id, completed.version)
    with pytest.raises(InvalidStateTransitionError):
        application.timers.start_timer(completed.id, completed.version)
    assert advanced.current_session.phase_index == 1


def test_focus_stats_uses_closed_run_outcomes_and_excludes_breaks(application, clock) -> None:
    focus = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
    run = application.timers.start_timer(focus.timer.id, focus.timer.version)
    clock.now += timedelta(milliseconds=7)
    application.timers.complete_timer(run.id, run.version)

    short_break = application.focus.create_session(CreateFocusSession(phase_kind="SHORT_BREAK"))
    run = application.timers.start_timer(short_break.timer.id, short_break.timer.version)
    clock.now += timedelta(milliseconds=11)
    application.timers.complete_timer(run.id, run.version)

    stats = application.focus.get_stats(FocusStatsQuery())
    assert stats.completed_focus_sessions == 0
    assert stats.focused_duration_ms == 7


def test_focus_restart_preserves_phase_state_and_only_timer_reconciles(application, app_config, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    paused = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=9)
    paused = application.timers.pause_timer(paused.id, paused.version)
    application.close()
    clock.now += timedelta(hours=1)
    reopened = create_application(app_config, clock)
    try:
        restored = reopened.focus.get_sequence(sequence.id)
        assert restored.state == "ACTIVE"
        assert restored.current_phase_index == 0
        assert restored.current_session.timer.state == "PAUSED"
        assert restored.current_session.timer.effective_elapsed_ms == 9
    finally:
        reopened.close()


def test_focus_commands_never_change_linked_task(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Unchanged task"))
    before = application.tasks.get_task(task.id)
    sequence = application.focus.create_sequence(CreateFocusSequence(task_id=task.id))
    running = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=1)
    application.timers.complete_timer(running.id, running.version)
    application.focus.advance_sequence(sequence.id, AdvanceFocusSequence(expected_version=sequence.version))
    current = application.focus.get_sequence(sequence.id)
    application.focus.cancel_sequence(current.id, CancelFocusSequence(expected_version=current.version))
    after = application.tasks.get_task(task.id)
    assert (after.status, after.version, after.updated_at_utc, after.deleted_at_utc) == (
        before.status, before.version, before.updated_at_utc, before.deleted_at_utc
    )


def test_focus_stats_mixed_outcomes_ranges_and_task_snapshots(application, clock) -> None:
    first_task = application.tasks.create_task(CreateTask(title="First"))
    second_task = application.tasks.create_task(CreateTask(title="Second"))
    started_at = clock.now

    natural = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS", task_id=first_task.id))
    run = application.timers.start_timer(natural.timer.id, natural.timer.version)
    clock.now += timedelta(milliseconds=1_500_000)
    application.timers.complete_timer(run.id, run.version)

    early = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS", task_id=first_task.id))
    run = application.timers.start_timer(early.timer.id, early.timer.version)
    clock.now += timedelta(milliseconds=17)
    application.timers.complete_timer(run.id, run.version)

    other = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS", task_id=second_task.id))
    run = application.timers.start_timer(other.timer.id, other.timer.version)
    clock.now += timedelta(milliseconds=5)
    application.timers.complete_timer(run.id, run.version)

    short = application.focus.create_session(CreateFocusSession(phase_kind="SHORT_BREAK"))
    run = application.timers.start_timer(short.timer.id, short.timer.version)
    clock.now += timedelta(milliseconds=4)
    application.timers.complete_timer(run.id, run.version)

    long_break = application.focus.create_session(CreateFocusSession(phase_kind="LONG_BREAK"))
    run = application.timers.start_timer(long_break.timer.id, long_break.timer.version)
    clock.now += timedelta(milliseconds=4)
    application.timers.complete_timer(run.id, run.version)

    cancelled = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS", task_id=first_task.id))
    run = application.timers.start_timer(cancelled.timer.id, cancelled.timer.version)
    clock.now += timedelta(milliseconds=3)
    application.timers.cancel_timer(run.id, run.version)

    reset = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS", task_id=first_task.id))
    run = application.timers.start_timer(reset.timer.id, reset.timer.version)
    clock.now += timedelta(milliseconds=3)
    application.timers.reset_timer(run.id, run.version)

    application.tasks.delete_task(first_task.id, first_task.version)
    all_stats = application.focus.get_stats(FocusStatsQuery())
    first_stats = application.focus.get_stats(FocusStatsQuery(task_id=first_task.id))
    window_stats = application.focus.get_stats(
        FocusStatsQuery(started_from_utc=started_at, started_before_utc=started_at + timedelta(milliseconds=1_500_000))
    )
    assert (all_stats.completed_focus_sessions, all_stats.focused_duration_ms) == (1, 1_500_022)
    assert (first_stats.completed_focus_sessions, first_stats.focused_duration_ms) == (1, 1_500_017)
    assert (window_stats.completed_focus_sessions, window_stats.focused_duration_ms) == (1, 1_500_000)
