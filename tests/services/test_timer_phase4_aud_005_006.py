from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from productivity_app.bootstrap import create_application
from productivity_app.core.events import DomainEvent, InProcessEventBus
from productivity_app.core.exceptions import DuplicateNameError, NotFoundError
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.schemas.tasks import CreateTask
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    MoveTimerPreset,
    TimerPresetQuery,
    TimerQuery,
    TimerRunQuery,
    UpdateTimer,
    UpdateTimerPreset,
)
from productivity_app.services.timers import TimerService


class SequenceIds:
    def __init__(self, values: list[str]) -> None:
        self._values = iter(values)

    def new(self) -> str:
        return next(self._values)


def _fixed_ids(*values: int) -> SequenceIds:
    return SequenceIds([f"{value:036d}" for value in values])


def _rows(app_config, sql: str, parameters: dict[str, object] | None = None):
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.exec_driver_sql(sql, parameters or {})]
    finally:
        engine.dispose()


def _ids(items) -> list[str]:
    return [item.id for item in items]


def _watch_committed_events(application, app_config) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    def capture(event: DomainEvent) -> None:
        table = "timer_presets" if event.event_type.startswith("TIMER_PRESET_") else "timers"
        expected_version = int(event.event_id.rsplit(":", 1)[1])
        assert _rows(
            app_config,
            f"SELECT version FROM {table} WHERE id=:id",  # noqa: S608 - fixed table allowlist
            {"id": event.entity_id},
        ) == [(expected_version,)]
        events.append(event)

    event_types = (
        "TIMER_CREATED",
        "TIMER_UPDATED",
        "TIMER_STARTED",
        "TIMER_PAUSED",
        "TIMER_RESUMED",
        "TIMER_RESET",
        "TIMER_COMPLETED",
        "TIMER_CANCELLED",
        "TIMER_DELETED",
        "TIMER_RESTORED",
        "TIMER_PRESET_CREATED",
        "TIMER_PRESET_UPDATED",
        "TIMER_PRESET_MOVED",
        "TIMER_PRESET_DELETED",
        "TIMER_PRESET_RESTORED",
    )
    bus = application.unit_of_work_factory()._event_bus
    for event_type in event_types:
        bus.subscribe(event_type, capture)
    return events


def _assert_exact_event(
    events: list[DomainEvent], event_type: str, result, occurred_at, *, preset: bool = False
) -> None:
    assert len(events) == 1
    event = events.pop()
    prefix = "timer_preset" if preset else "timer"
    assert event.event_type == event_type
    assert event.entity_id == result.id
    assert event.event_id == f"{prefix}:{event_type.lower()}:{result.id}:{result.version}"
    assert event.occurred_at_utc == occurred_at
    assert event.payload == {}


def _close_run(application, clock, timer, outcome: str):
    timer = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=10)
    if outcome == "FINISHED_EARLY":
        return application.timers.complete_timer(timer.id, timer.version)
    if outcome == "FINISHED":
        clock.now += timedelta(milliseconds=100)
        result = application.timers.reconcile_expired_timer()
        assert result is not None
        return result
    if outcome == "CANCELLED":
        return application.timers.cancel_timer(timer.id, timer.version)
    return application.timers.reset_timer(timer.id, timer.version)


def test_exact_timer_domain_event_matrix_is_post_commit(application, app_config, clock) -> None:
    events = _watch_committed_events(application, app_config)
    timer = application.timers.create_timer(CreateTimer(name="Events", planned_duration_ms=100))
    _assert_exact_event(events, "TIMER_CREATED", timer, clock.now)
    timer = application.timers.update_timer(
        timer.id, UpdateTimer(expected_version=timer.version, name="Updated")
    )
    _assert_exact_event(events, "TIMER_UPDATED", timer, clock.now)
    timer = application.timers.start_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_STARTED", timer, clock.now)
    timer = application.timers.pause_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_PAUSED", timer, clock.now)
    timer = application.timers.resume_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_RESUMED", timer, clock.now)
    timer = application.timers.reset_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_RESET", timer, clock.now)
    timer = application.timers.start_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_STARTED", timer, clock.now)
    timer = application.timers.complete_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_COMPLETED", timer, clock.now)
    timer = application.timers.reset_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_RESET", timer, clock.now)
    timer = application.timers.delete_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_DELETED", timer, clock.now)
    timer = application.timers.restore_timer(timer.id, timer.version)
    _assert_exact_event(events, "TIMER_RESTORED", timer, clock.now)

    canceller = application.timers.create_timer(
        CreateTimer(name="Canceller", planned_duration_ms=100)
    )
    _assert_exact_event(events, "TIMER_CREATED", canceller, clock.now)
    canceller = application.timers.start_timer(canceller.id, canceller.version)
    _assert_exact_event(events, "TIMER_STARTED", canceller, clock.now)
    canceller = application.timers.cancel_timer(canceller.id, canceller.version)
    _assert_exact_event(events, "TIMER_CANCELLED", canceller, clock.now)


def test_exact_preset_domain_event_matrix_is_post_commit(application, app_config, clock) -> None:
    events = _watch_committed_events(application, app_config)
    preset = application.timers.create_preset(CreateTimerPreset(name="Events", duration_ms=100))
    _assert_exact_event(events, "TIMER_PRESET_CREATED", preset, clock.now, preset=True)
    preset = application.timers.update_preset(
        preset.id, UpdateTimerPreset(expected_version=preset.version, duration_ms=200)
    )
    _assert_exact_event(events, "TIMER_PRESET_UPDATED", preset, clock.now, preset=True)
    other = application.timers.create_preset(CreateTimerPreset(name="Other", duration_ms=300))
    _assert_exact_event(events, "TIMER_PRESET_CREATED", other, clock.now, preset=True)
    preset = application.timers.move_preset(
        preset.id, MoveTimerPreset(expected_version=preset.version, position=1)
    )
    _assert_exact_event(events, "TIMER_PRESET_MOVED", preset, clock.now, preset=True)
    preset = application.timers.delete_preset(preset.id, preset.version)
    _assert_exact_event(events, "TIMER_PRESET_DELETED", preset, clock.now, preset=True)
    preset = application.timers.restore_preset(preset.id, preset.version)
    _assert_exact_event(events, "TIMER_PRESET_RESTORED", preset, clock.now, preset=True)


@pytest.mark.parametrize(
    ("outcome", "final_state"),
    (
        ("FINISHED_EARLY", "FINISHED"),
        ("FINISHED", "FINISHED"),
        ("CANCELLED", "CANCELLED"),
        ("RESET", "READY"),
    ),
)
def test_timer_run_terminal_history_contract(
    application, clock, outcome: str, final_state: str
) -> None:
    task = application.tasks.create_task(CreateTask(title="Snapshot task"))
    timer = application.timers.create_timer(
        CreateTimer(name=outcome, planned_duration_ms=100, task_id=task.id)
    )
    started_at = clock.now
    result = _close_run(application, clock, timer, outcome)
    run = application.timers.list_timer_runs(timer.id)[0]
    assert result.state == final_state
    assert run.timer_id == timer.id and run.task_id == task.id
    assert run.planned_duration_ms == 100 and run.started_at_utc == started_at
    assert run.outcome == outcome
    if outcome == "FINISHED":
        assert run.actual_duration_ms == 100
        assert run.ended_at_utc == started_at + timedelta(milliseconds=100)
    else:
        assert run.actual_duration_ms == 10 and run.ended_at_utc == clock.now


def test_timer_run_start_pause_resume_and_terminal_reset_preserve_one_run(
    application, clock
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="History", planned_duration_ms=100))
    started = application.timers.start_timer(timer.id, timer.version)
    with application.unit_of_work_factory() as uow:
        run = uow.timers.get_open_run(timer.id)
        assert run is not None
        assert run.timer_id == timer.id
        assert run.planned_duration_ms == 100
        assert run.actual_duration_ms == 0
        assert run.started_at_utc == started.run_started_at_utc
        assert run.ended_at_utc is None
        assert run.outcome is None
    clock.now += timedelta(milliseconds=20)
    paused = application.timers.pause_timer(timer.id, started.version)
    with application.unit_of_work_factory() as uow:
        run = uow.timers.get_open_run(timer.id)
        assert run is not None
        run_id = run.id
        assert run.actual_duration_ms == 20 and run.ended_at_utc is None and run.outcome is None
    resumed = application.timers.resume_timer(timer.id, paused.version)
    clock.now += timedelta(milliseconds=15)
    finished = application.timers.complete_timer(timer.id, resumed.version)
    closed = application.timers.list_timer_runs(timer.id)[0]
    assert closed.id == run_id and closed.actual_duration_ms == 35
    before = closed
    ready = application.timers.reset_timer(timer.id, finished.version)
    assert ready.state == "READY"
    assert application.timers.list_timer_runs(timer.id) == (before,)


def test_timer_run_snapshots_survive_later_timer_configuration_changes(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Original"))
    timer = application.timers.create_timer(
        CreateTimer(name="Snapshot", planned_duration_ms=100, task_id=task.id)
    )
    terminal = _close_run(application, clock, timer, "FINISHED_EARLY")
    ready = application.timers.reset_timer(timer.id, terminal.version)
    changed = application.timers.update_timer(
        timer.id,
        UpdateTimer(expected_version=ready.version, task_id=None, planned_duration_ms=250),
    )
    run = application.timers.list_timer_runs(timer.id)[0]
    assert changed.task_id is None and changed.planned_duration_ms == 250
    assert run.task_id == task.id and run.planned_duration_ms == 100


def test_complete_timer_filter_matrix(application, clock) -> None:
    task_a = application.tasks.create_task(CreateTask(title="A"))
    task_b = application.tasks.create_task(CreateTask(title="B"))
    preset = application.timers.create_preset(CreateTimerPreset(name="Preset", duration_ms=100))
    ready_none = application.timers.create_timer(CreateTimer(name="None", planned_duration_ms=100))
    ready_task = application.timers.create_timer(
        CreateTimer(name="Task", planned_duration_ms=100, task_id=task_a.id)
    )
    ready_preset = application.timers.create_timer(CreateTimer(name="Preset", preset_id=preset.id))
    finished = application.timers.create_timer(
        CreateTimer(name="Finished", planned_duration_ms=100)
    )
    finished = _close_run(application, clock, finished, "FINISHED_EARLY")
    end = finished.ended_at_utc
    assert end is not None
    cancelled = application.timers.create_timer(
        CreateTimer(name="Cancelled", planned_duration_ms=100)
    )
    cancelled = _close_run(application, clock, cancelled, "CANCELLED")
    deleted = application.timers.delete_timer(ready_none.id, ready_none.version)

    assert _ids(application.timers.list_timers(TimerQuery(states=("READY",)))) == sorted(
        (ready_task.id, ready_preset.id)
    )
    assert _ids(application.timers.list_timers(TimerQuery(states=("FINISHED",)))) == [finished.id]
    assert _ids(application.timers.list_timers(TimerQuery(states=("CANCELLED",)))) == [cancelled.id]
    assert set(_ids(application.timers.list_timers(TimerQuery(states=("READY", "FINISHED"))))) == {
        ready_task.id,
        ready_preset.id,
        finished.id,
    }
    assert set(_ids(application.timers.list_timers(TimerQuery(task_link="NONE")))) == {
        ready_preset.id,
        finished.id,
        cancelled.id,
    }
    assert _ids(application.timers.list_timers(TimerQuery(task_link="TASK"))) == [ready_task.id]
    assert _ids(
        application.timers.list_timers(TimerQuery(task_link="TASK", task_id=task_a.id))
    ) == [ready_task.id]
    assert application.timers.list_timers(TimerQuery(task_link="TASK", task_id=task_b.id)) == ()
    assert _ids(application.timers.list_timers(TimerQuery(preset_link="PRESET"))) == [
        ready_preset.id
    ]
    assert set(_ids(application.timers.list_timers(TimerQuery(preset_link="NONE")))) == {
        ready_task.id,
        finished.id,
        cancelled.id,
    }
    assert _ids(
        application.timers.list_timers(TimerQuery(preset_link="PRESET", preset_id=preset.id))
    ) == [ready_preset.id]
    assert application.timers.list_timers(TimerQuery(task_link="TASK", task_id="")) == ()
    assert application.timers.list_timers(TimerQuery(preset_link="PRESET", preset_id="")) == ()
    assert (
        application.timers.list_timers(TimerQuery(task_link="TASK", task_id="unknown-task")) == ()
    )
    assert (
        application.timers.list_timers(TimerQuery(preset_link="PRESET", preset_id="unknown-preset"))
        == ()
    )
    assert deleted.id not in _ids(application.timers.list_timers())
    assert deleted.id in _ids(application.timers.list_timers(TimerQuery(include_deleted=True)))
    assert set(_ids(application.timers.list_timers(TimerQuery(ended_from_utc=end)))) == {
        finished.id,
        cancelled.id,
    }
    assert finished.id not in _ids(application.timers.list_timers(TimerQuery(ended_before_utc=end)))
    assert _ids(
        application.timers.list_timers(
            TimerQuery(ended_from_utc=end, ended_before_utc=end + timedelta(microseconds=1))
        )
    ) == [finished.id]
    assert set(_ids(application.timers.list_timers(TimerQuery()))) == {
        ready_task.id,
        ready_preset.id,
        finished.id,
        cancelled.id,
    }
    assert application.timers.list_timers(TimerQuery(ended_before_utc=end)) == ()
    assert _ids(
        application.timers.list_timers(
            TimerQuery(states=("READY",), task_link="TASK", task_id=task_a.id)
        )
    ) == [ready_task.id]
    with pytest.raises(PydanticValidationError):
        TimerQuery(task_link="ANY", task_id=task_a.id)


def test_effective_state_filter_matrix(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Active", planned_duration_ms=100))
    running = application.timers.start_timer(timer.id, timer.version)
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("RUNNING",)))) == [
        timer.id
    ]
    clock.now += timedelta(milliseconds=100)
    assert application.timers.list_timers(TimerQuery(effective_states=("RUNNING",))) == ()
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("FINISHED",)))) == [
        timer.id
    ]
    assert application.timers.get_timer(timer.id).version == running.version
    assert _ids(
        application.timers.list_timers(TimerQuery(effective_states=("RUNNING", "FINISHED")))
    ) == [timer.id]


def test_all_persisted_and_effective_state_filters(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="States", planned_duration_ms=100))
    assert _ids(application.timers.list_timers(TimerQuery(states=("READY",)))) == [timer.id]
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("READY",)))) == [
        timer.id
    ]
    running = application.timers.start_timer(timer.id, timer.version)
    assert _ids(application.timers.list_timers(TimerQuery(states=("RUNNING",)))) == [timer.id]
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("RUNNING",)))) == [
        timer.id
    ]
    clock.now += timedelta(milliseconds=10)
    paused = application.timers.pause_timer(timer.id, running.version)
    assert _ids(application.timers.list_timers(TimerQuery(states=("PAUSED",)))) == [timer.id]
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("PAUSED",)))) == [
        timer.id
    ]
    finished = application.timers.complete_timer(timer.id, paused.version)
    assert _ids(application.timers.list_timers(TimerQuery(states=("FINISHED",)))) == [timer.id]
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("FINISHED",)))) == [
        timer.id
    ]
    ready = application.timers.reset_timer(timer.id, finished.version)
    running = application.timers.start_timer(timer.id, ready.version)
    application.timers.cancel_timer(timer.id, running.version)
    assert _ids(application.timers.list_timers(TimerQuery(states=("CANCELLED",)))) == [timer.id]
    assert _ids(application.timers.list_timers(TimerQuery(effective_states=("CANCELLED",)))) == [
        timer.id
    ]


def test_every_timer_sort_and_pagination_is_deterministic(application, clock) -> None:
    application.timers = TimerService(
        application.unit_of_work_factory, clock, _fixed_ids(40, 10, 30, 20)
    )
    timers = [
        application.timers.create_timer(CreateTimer(name=name, planned_duration_ms=duration))
        for name, duration in (("Same", 200), ("Same", 100), ("Zed", 100), ("Alpha", 300))
    ]
    by_id = sorted(timers, key=lambda item: item.id)
    expected = {
        "created_newest": _ids(by_id),
        "created_oldest": _ids(by_id),
        "updated_newest": _ids(by_id),
        "updated_oldest": _ids(by_id),
        "name": _ids(sorted(timers, key=lambda item: (item.name, item.id))),
        "state": _ids(by_id),
        "duration_shortest": _ids(
            sorted(timers, key=lambda item: (item.planned_duration_ms, item.id))
        ),
        "duration_longest": _ids(
            sorted(timers, key=lambda item: (-item.planned_duration_ms, item.id))
        ),
        "ended_newest": _ids(by_id),
    }
    for sort, ids in expected.items():
        assert _ids(application.timers.list_timers(TimerQuery(sort=sort))) == ids
        first = application.timers.list_timers(TimerQuery(sort=sort, limit=2, offset=0))
        second = application.timers.list_timers(TimerQuery(sort=sort, limit=2, offset=2))
        assert first == application.timers.list_timers(TimerQuery(sort=sort, limit=2, offset=0))
        assert second == application.timers.list_timers(TimerQuery(sort=sort, limit=2, offset=2))
        assert _ids(first + second) == ids


def test_ended_newest_sort_places_null_last_and_uses_id_ties(application) -> None:
    first = application.timers.create_timer(CreateTimer(name="First", planned_duration_ms=100))
    second = application.timers.create_timer(CreateTimer(name="Second", planned_duration_ms=100))
    pending = application.timers.create_timer(CreateTimer(name="Pending", planned_duration_ms=100))
    first = application.timers.start_timer(first.id, first.version)
    first = application.timers.complete_timer(first.id, first.version)
    second = application.timers.start_timer(second.id, second.version)
    application.timers.complete_timer(second.id, second.version)
    expected = [*sorted((first.id, second.id)), pending.id]
    all_items = application.timers.list_timers(TimerQuery(sort="ended_newest"))
    assert _ids(all_items) == expected
    page1 = application.timers.list_timers(TimerQuery(sort="ended_newest", limit=2))
    page2 = application.timers.list_timers(TimerQuery(sort="ended_newest", limit=2, offset=2))
    assert page1 == application.timers.list_timers(TimerQuery(sort="ended_newest", limit=2))
    assert page2 == application.timers.list_timers(
        TimerQuery(sort="ended_newest", limit=2, offset=2)
    )
    assert set(_ids(page1)).isdisjoint(_ids(page2))
    assert _ids(page1 + page2) == expected


def test_timer_run_query_matrix(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Runs"))
    unrelated_task = application.tasks.create_task(CreateTask(title="Unrelated"))
    timer = application.timers.create_timer(
        CreateTimer(name="Runs", planned_duration_ms=100, task_id=task.id)
    )
    runs = []
    for outcome in ("FINISHED_EARLY", "CANCELLED", "RESET", "FINISHED"):
        start = clock.now
        terminal = _close_run(application, clock, timer, outcome)
        run = application.timers.list_timer_runs(timer.id)[0]
        runs.append((run, start))
        if outcome != "RESET":
            timer = application.timers.reset_timer(timer.id, terminal.version)
        else:
            timer = terminal
        clock.now += timedelta(seconds=1)
    for outcome in ("FINISHED", "FINISHED_EARLY", "CANCELLED", "RESET"):
        assert {
            run.outcome
            for run in application.timers.list_timer_runs(
                timer.id, TimerRunQuery(outcomes=(outcome,))
            )
        } == {outcome}
    assert {
        run.outcome
        for run in application.timers.list_timer_runs(
            timer.id, TimerRunQuery(outcomes=("FINISHED", "RESET"))
        )
    } == {"FINISHED", "RESET"}
    assert all(
        run.task_id == task.id
        for run in application.timers.list_timer_runs(timer.id, TimerRunQuery(task_id=task.id))
    )
    assert (
        application.timers.list_timer_runs(timer.id, TimerRunQuery(task_id=unrelated_task.id)) == ()
    )
    newest = application.timers.list_timer_runs(timer.id)
    boundary = newest[-1].started_at_utc
    assert newest[-1].id in _ids(
        application.timers.list_timer_runs(timer.id, TimerRunQuery(started_from_utc=boundary))
    )
    assert newest[-1].id not in _ids(
        application.timers.list_timer_runs(timer.id, TimerRunQuery(started_before_utc=boundary))
    )
    first_start = min(run.started_at_utc for run in newest)
    last_start = max(run.started_at_utc for run in newest)
    middle_started = application.timers.list_timer_runs(
        timer.id,
        TimerRunQuery(
            started_from_utc=first_start,
            started_before_utc=last_start,
            limit=20,
        ),
    )
    assert all(first_start <= run.started_at_utc < last_start for run in middle_started)
    first_end = min(run.ended_at_utc for run in newest)
    last_end = max(run.ended_at_utc for run in newest)
    assert first_end is not None and last_end is not None
    ended_window = application.timers.list_timer_runs(
        timer.id,
        TimerRunQuery(ended_from_utc=first_end, ended_before_utc=last_end, limit=20),
    )
    assert all(first_end <= run.ended_at_utc < last_end for run in ended_window)
    assert _ids(
        application.timers.list_timer_runs(
            timer.id, TimerRunQuery(ended_from_utc=first_end, limit=20)
        )
    ) == _ids(newest)
    assert newest[-1].id not in _ids(
        application.timers.list_timer_runs(
            timer.id, TimerRunQuery(ended_before_utc=first_end, limit=20)
        )
    )
    page1 = application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2))
    page2 = application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2, offset=2))
    assert page1 == application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2))
    assert set(_ids(page1)).isdisjoint(_ids(page2))
    assert _ids(page1 + page2) == _ids(newest)
    deleted = application.timers.delete_timer(timer.id, timer.version)
    with pytest.raises(NotFoundError):
        application.timers.list_timer_runs(deleted.id)
    assert len(application.timers.list_timer_runs(deleted.id, include_deleted_timer=True)) == 4
    with pytest.raises(PydanticValidationError):
        TimerRunQuery(ended_from_utc=clock.now, ended_before_utc=clock.now)


def test_timer_run_ordering_ties_and_pages_are_repeatable(application, clock) -> None:
    application.timers = TimerService(
        application.unit_of_work_factory, clock, _fixed_ids(99, 40, 10, 30, 20)
    )
    timer = application.timers.create_timer(CreateTimer(name="Tie runs", planned_duration_ms=100))
    for _ in range(4):
        started = application.timers.start_timer(timer.id, timer.version)
        timer = application.timers.complete_timer(timer.id, started.version)
        timer = application.timers.reset_timer(timer.id, timer.version)
    runs = application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=20))
    expected = sorted(run.id for run in runs)
    assert _ids(runs) == expected
    page1 = application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2))
    page2 = application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2, offset=2))
    assert page1 == application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2))
    assert page2 == application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=2, offset=2))
    assert set(_ids(page1)).isdisjoint(_ids(page2))
    assert _ids(page1 + page2) == expected


@pytest.mark.parametrize(("source", "target"), ((0, 1), (0, 3), (1, 0), (1, 3), (3, 0), (3, 2)))
def test_every_preset_move_direction_updates_only_changed_rows(
    application, clock, source: int, target: int
) -> None:
    application.timers = TimerService(
        application.unit_of_work_factory, clock, _fixed_ids(40, 10, 30, 20)
    )
    presets = [
        application.timers.create_preset(CreateTimerPreset(name=f"P{i}", duration_ms=100 + i))
        for i in range(4)
    ]
    before = {item.id: item for item in application.timers.list_presets()}
    clock.now += timedelta(microseconds=1)
    moved = application.timers.move_preset(
        presets[source].id,
        MoveTimerPreset(expected_version=presets[source].version, position=target),
    )
    expected = presets.copy()
    expected.insert(target, expected.pop(source))
    after = application.timers.list_presets()
    assert _ids(after) == _ids(expected)
    assert [item.sort_order for item in after] == list(range(4))
    for item in after:
        changed = before[item.id].sort_order != item.sort_order or item.id == moved.id
        assert item.version == before[item.id].version + int(changed)
        assert (item.updated_at_utc == clock.now) is changed


def test_preset_delete_restore_sorts_and_pagination(application, clock) -> None:
    application.timers = TimerService(
        application.unit_of_work_factory, clock, _fixed_ids(30, 10, 20)
    )
    presets = [
        application.timers.create_preset(CreateTimerPreset(name=name, duration_ms=duration))
        for name, duration in (("B", 100), ("A", 200), ("C", 100))
    ]
    before = {item.id: item for item in application.timers.list_presets()}
    clock.now += timedelta(microseconds=1)
    delete_time = clock.now
    deleted = application.timers.delete_preset(presets[1].id, presets[1].version)
    active = application.timers.list_presets()
    assert deleted.version == presets[1].version + 1
    assert deleted.deleted_at_utc == delete_time and deleted.updated_at_utc == delete_time
    assert [item.sort_order for item in active] == [0, 1]
    assert active[0].version == before[active[0].id].version
    assert active[0].updated_at_utc == before[active[0].id].updated_at_utc
    assert active[1].version == before[active[1].id].version + 1
    assert active[1].updated_at_utc == delete_time
    before_restore = {item.id: item for item in active}
    clock.now += timedelta(microseconds=1)
    restore_time = clock.now
    restored = application.timers.restore_preset(deleted.id, deleted.version)
    assert restored.version == deleted.version + 1
    assert restored.deleted_at_utc is None and restored.updated_at_utc == restore_time
    restored_order = application.timers.list_presets()
    assert [item.sort_order for item in restored_order] == [0, 1, 2]
    assert _ids(restored_order) == [presets[0].id, restored.id, presets[2].id]
    for item in restored_order:
        if item.id == restored.id:
            continue
        before_item = before_restore[item.id]
        changed = item.sort_order != before_item.sort_order
        assert item.version == before_item.version + int(changed)
        assert item.updated_at_utc == (restore_time if changed else before_item.updated_at_utc)
    expected = {
        "position": _ids(restored_order),
        "name": _ids(sorted(restored_order, key=lambda item: (item.name, item.id))),
        "duration_shortest": _ids(
            sorted(restored_order, key=lambda item: (item.duration_ms, item.id))
        ),
        "duration_longest": _ids(
            sorted(restored_order, key=lambda item: (-item.duration_ms, item.id))
        ),
    }
    for sort, expected_ids in expected.items():
        all_items = application.timers.list_presets(TimerPresetQuery(sort=sort))
        p1 = application.timers.list_presets(TimerPresetQuery(sort=sort, limit=2))
        p2 = application.timers.list_presets(TimerPresetQuery(sort=sort, limit=2, offset=2))
        assert _ids(all_items) == expected_ids
        assert p1 == application.timers.list_presets(TimerPresetQuery(sort=sort, limit=2))
        assert p2 == application.timers.list_presets(TimerPresetQuery(sort=sort, limit=2, offset=2))
        assert set(_ids(p1)).isdisjoint(_ids(p2))
        assert _ids(p1 + p2) == _ids(all_items)


def test_preset_restore_duplicate_normalized_name_rolls_back(application) -> None:
    deleted = application.timers.create_preset(
        CreateTimerPreset(name="Focus Mode", duration_ms=100)
    )
    deleted = application.timers.delete_preset(deleted.id, deleted.version)
    application.timers.create_preset(CreateTimerPreset(name=" focus   mode ", duration_ms=200))
    before = application.timers.list_presets(TimerPresetQuery(include_deleted=True, limit=20))
    with pytest.raises(DuplicateNameError):
        application.timers.restore_preset(deleted.id, deleted.version)
    after = application.timers.list_presets(TimerPresetQuery(include_deleted=True, limit=20))
    assert after == before


def test_running_restart_is_non_mutating(application, app_config, clock, monkeypatch) -> None:
    running = application.timers.create_timer(
        CreateTimer(name="Running", planned_duration_ms=1_000)
    )
    running = application.timers.start_timer(running.id, running.version)
    original_updated = running.updated_at_utc
    application.close()
    clock.now += timedelta(milliseconds=100)
    published: list[DomainEvent] = []
    original_publish = InProcessEventBus.publish

    def publish_spy(self, event: DomainEvent):
        published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(InProcessEventBus, "publish", publish_spy)
    restarted = create_application(app_config, clock)
    view = restarted.timers.get_timer(running.id)
    assert view.state == "RUNNING" and view.version == running.version
    assert view.updated_at_utc == original_updated and view.effective_elapsed_ms == 100
    assert view.remaining_ms == 900 and view.run_started_at_utc == running.run_started_at_utc
    assert view.deadline_at_utc == running.deadline_at_utc
    assert _rows(
        app_config,
        "SELECT actual_duration_ms,ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
        {"id": running.id},
    ) == [(0, None, None)]
    assert published == []
    restarted.close()


def test_paused_restart_preserves_frozen_elapsed_open_run_and_audit_fields(
    application, app_config, clock, monkeypatch
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Paused", planned_duration_ms=1_000))
    timer = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=125)
    paused = application.timers.pause_timer(timer.id, timer.version)
    application.close()
    clock.now += timedelta(days=1)
    published: list[DomainEvent] = []
    original_publish = InProcessEventBus.publish

    def publish_spy(self, event: DomainEvent):
        published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(InProcessEventBus, "publish", publish_spy)
    restarted = create_application(app_config, clock)
    try:
        view = restarted.timers.get_timer(timer.id)
        assert view.state == "PAUSED" and view.effective_state == "PAUSED"
        assert view.elapsed_before_run_ms == 125 and view.effective_elapsed_ms == 125
        assert view.remaining_ms == 875
        assert view.run_started_at_utc is None and view.deadline_at_utc is None
        assert view.version == paused.version and view.updated_at_utc == paused.updated_at_utc
        assert _rows(
            app_config,
            "SELECT actual_duration_ms,ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
            {"id": timer.id},
        ) == [(125, None, None)]
        assert published == []
    finally:
        restarted.close()


def test_expired_restart_reconciles_once_and_second_restart_is_idempotent(
    application, app_config, clock, monkeypatch
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Expired", planned_duration_ms=100))
    timer = application.timers.start_timer(timer.id, timer.version)
    deadline = timer.deadline_at_utc
    application.close()
    clock.now += timedelta(milliseconds=200)
    calls: list[object] = []
    published: list[DomainEvent] = []
    original = TimerService.reconcile_expired_timer
    original_publish = InProcessEventBus.publish

    def spy(self):
        result = original(self)
        calls.append(result)
        return result

    def publish_spy(self, event: DomainEvent):
        if event.event_type == "TIMER_COMPLETED":
            published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(TimerService, "reconcile_expired_timer", spy)
    monkeypatch.setattr(InProcessEventBus, "publish", publish_spy)
    first = create_application(app_config, clock)
    completed = first.timers.get_timer(timer.id)
    assert completed.state == "FINISHED" and completed.version == timer.version + 1
    assert completed.ended_at_utc == deadline and completed.updated_at_utc == clock.now
    assert calls == [completed]
    assert len(published) == 1
    assert published[0].entity_id == timer.id
    assert published[0].event_id == (f"timer:timer_completed:{timer.id}:{completed.version}")
    assert published[0].occurred_at_utc == clock.now and published[0].payload == {}
    first.close()
    second = create_application(app_config, clock)
    again = second.timers.get_timer(timer.id)
    second.close()
    assert len(calls) == 2
    assert calls[1] is None
    assert len(published) == 1
    assert again.version == completed.version and again.ended_at_utc == deadline
    runs = _rows(
        app_config,
        "SELECT actual_duration_ms,ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
        {"id": timer.id},
    )
    assert len(runs) == 1 and runs[0][0] == 100 and runs[0][2] == "FINISHED"


def test_bootstrap_calls_reconciliation_exactly_once_with_no_active_timer(
    app_config, clock, monkeypatch
) -> None:
    calls = 0
    published: list[DomainEvent] = []
    threads_before = {thread.ident for thread in threading.enumerate()}
    original = TimerService.reconcile_expired_timer
    original_publish = InProcessEventBus.publish

    def spy(self):
        nonlocal calls
        calls += 1
        return original(self)

    def publish_spy(self, event: DomainEvent):
        published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(TimerService, "reconcile_expired_timer", spy)
    monkeypatch.setattr(InProcessEventBus, "publish", publish_spy)
    app = create_application(app_config, clock)
    try:
        assert calls == 1 and app.timers.get_active_timer() is None
        assert published == []
        assert {thread.ident for thread in threading.enumerate()} == threads_before
    finally:
        app.close()
