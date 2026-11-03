from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ActiveTimerConflictError,
    InvalidStateTransitionError,
    StaleVersionError,
)
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    MoveTimerPreset,
    UpdateTimer,
    UpdateTimerPreset,
)

_TIMER_EVENTS = (
    "TIMER_UPDATED",
    "TIMER_STARTED",
    "TIMER_PAUSED",
    "TIMER_RESUMED",
    "TIMER_RESET",
    "TIMER_COMPLETED",
    "TIMER_CANCELLED",
    "TIMER_DELETED",
    "TIMER_RESTORED",
)
_PRESET_EVENTS = (
    "TIMER_PRESET_UPDATED",
    "TIMER_PRESET_MOVED",
    "TIMER_PRESET_DELETED",
    "TIMER_PRESET_RESTORED",
)


def _watch(application, kinds=_TIMER_EVENTS) -> list[DomainEvent]:
    events: list[DomainEvent] = []
    bus = application.unit_of_work_factory()._event_bus
    for kind in kinds:
        bus.subscribe(kind, events.append)
    return events


def _rows(app_config, sql: str, parameters: dict[str, object] | None = None):
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.exec_driver_sql(sql, parameters or {})]
    finally:
        engine.dispose()


def _snapshot(app_config, timer_id: str):
    return (
        _rows(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer_id}),
        _rows(
            app_config,
            "SELECT * FROM timer_runs WHERE timer_id=:id ORDER BY id",
            {"id": timer_id},
        ),
    )


def _race(left: Callable[[], object], right: Callable[[], object]) -> tuple[object, object]:
    barrier = Barrier(2)

    def invoke(operation: Callable[[], object]) -> object:
        barrier.wait()
        try:
            return operation()
        except (ActiveTimerConflictError, InvalidStateTransitionError, StaleVersionError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(invoke, left), executor.submit(invoke, right))
        return futures[0].result(), futures[1].result()


def _assert_active_invariants(app_config) -> None:
    assert (
        _rows(
            app_config,
            "SELECT count(*) FROM timers WHERE deleted_at_utc IS NULL AND state IN ('RUNNING','PAUSED')",
        )[0][0]
        <= 1
    )
    assert (
        _rows(
            app_config,
            "SELECT count(*) FROM timer_runs r JOIN timers t ON t.id=r.timer_id "
            "WHERE r.ended_at_utc IS NULL AND t.deleted_at_utc IS NULL",
        )[0][0]
        <= 1
    )


def test_start_vs_start_has_one_exact_winner(application, app_config) -> None:
    first = application.timers.create_timer(CreateTimer(name="A", planned_duration_ms=100))
    second = application.timers.create_timer(CreateTimer(name="B", planned_duration_ms=100))
    events = _watch(application)
    results = _race(
        lambda: application.timers.start_timer(first.id, first.version),
        lambda: application.timers.start_timer(second.id, second.version),
    )
    winners = [result for result in results if not isinstance(result, Exception)]
    losers = [result for result in results if isinstance(result, Exception)]
    assert len(winners) == 1 and len(losers) == 1
    assert isinstance(losers[0], ActiveTimerConflictError)
    winner = winners[0]
    assert winner.state == "RUNNING" and winner.version == 2
    loser_id = second.id if winner.id == first.id else first.id
    assert _rows(app_config, "SELECT state,version FROM timers WHERE id=:id", {"id": loser_id}) == [
        ("READY", 1)
    ]
    assert [(event.event_type, event.entity_id) for event in events] == [
        ("TIMER_STARTED", winner.id)
    ]
    _assert_active_invariants(app_config)


def test_paused_resume_vs_competing_start(application, app_config) -> None:
    active = application.timers.create_timer(CreateTimer(name="A", planned_duration_ms=100))
    waiting = application.timers.create_timer(CreateTimer(name="B", planned_duration_ms=100))
    active = application.timers.start_timer(active.id, active.version)
    active = application.timers.pause_timer(active.id, active.version)
    events = _watch(application)
    resumed, started = _race(
        lambda: application.timers.resume_timer(active.id, active.version),
        lambda: application.timers.start_timer(waiting.id, waiting.version),
    )
    assert not isinstance(resumed, Exception) and resumed.state == "RUNNING"
    assert isinstance(started, ActiveTimerConflictError)
    assert _rows(
        app_config, "SELECT state,version FROM timers WHERE id=:id", {"id": waiting.id}
    ) == [("READY", 1)]
    assert [(event.event_type, event.entity_id) for event in events] == [
        ("TIMER_RESUMED", active.id)
    ]
    _assert_active_invariants(app_config)


def test_reset_vs_start_has_only_valid_serial_outcomes(application, app_config) -> None:
    active = application.timers.create_timer(CreateTimer(name="A", planned_duration_ms=100))
    waiting = application.timers.create_timer(CreateTimer(name="B", planned_duration_ms=100))
    active = application.timers.start_timer(active.id, active.version)
    events = _watch(application)
    reset, start = _race(
        lambda: application.timers.reset_timer(active.id, active.version),
        lambda: application.timers.start_timer(waiting.id, waiting.version),
    )
    assert not isinstance(reset, Exception) and reset.state == "READY" and reset.version == 3
    if isinstance(start, Exception):
        assert isinstance(start, ActiveTimerConflictError)
        assert [event.event_type for event in events] == ["TIMER_RESET"]
    else:
        assert start.state == "RUNNING" and start.version == 2
        assert [event.event_type for event in events] == ["TIMER_RESET", "TIMER_STARTED"]
    run = _rows(
        app_config,
        "SELECT ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
        {"id": active.id},
    )
    assert run[0][0] is not None and run[0][1] == "RESET"
    _assert_active_invariants(app_config)


@pytest.mark.parametrize("legacy_state", ("RUNNING", "PAUSED"))
def test_deleted_legacy_active_restore_vs_start(
    application, app_config, clock, legacy_state: str
) -> None:
    legacy = application.timers.create_timer(CreateTimer(name="Legacy", planned_duration_ms=100))
    legacy = application.timers.start_timer(legacy.id, legacy.version)
    if legacy_state == "PAUSED":
        legacy = application.timers.pause_timer(legacy.id, legacy.version)
    with application.unit_of_work_factory() as uow:
        uow.acquire_write_lock()
        row = uow.timers.get(legacy.id)
        assert row is not None
        row.deleted_at_utc = clock.now
        uow.commit()
    waiting = application.timers.create_timer(CreateTimer(name="Waiting", planned_duration_ms=100))
    legacy_before = _snapshot(app_config, legacy.id)
    events = _watch(application)
    restored, started = _race(
        lambda: application.timers.restore_timer(legacy.id, legacy.version),
        lambda: application.timers.start_timer(waiting.id, waiting.version),
    )
    assert isinstance(restored, InvalidStateTransitionError)
    assert (
        not isinstance(started, Exception) and started.state == "RUNNING" and started.version == 2
    )
    assert [(event.event_type, event.entity_id) for event in events] == [
        ("TIMER_STARTED", waiting.id)
    ]
    assert (
        _rows(
            app_config,
            "SELECT state,version,deleted_at_utc FROM timers WHERE id=:id",
            {"id": legacy.id},
        )[0][2]
        is not None
    )
    assert _snapshot(app_config, legacy.id) == legacy_before
    _assert_active_invariants(app_config)


@pytest.mark.parametrize(
    ("initial", "left", "right"),
    (
        ("RUNNING", "pause", "reset"),
        ("RUNNING", "pause", "complete"),
        ("PAUSED", "resume", "reset"),
        ("PAUSED", "resume", "cancel"),
        ("RUNNING", "complete", "reset"),
        ("RUNNING", "update", "pause"),
        ("RUNNING", "delete", "pause"),
        ("EXPIRED", "reconcile", "reconcile"),
        ("EXPIRED", "reconcile", "pause"),
        ("EXPIRED", "reconcile", "reset"),
        ("EXPIRED", "reconcile", "cancel"),
        ("EXPIRED", "reconcile", "complete"),
    ),
)
def test_lifecycle_races_preserve_exact_single_mutation(
    application, app_config, clock, initial: str, left: str, right: str
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Race", planned_duration_ms=100))
    current = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=10)
    if initial == "PAUSED":
        current = application.timers.pause_timer(timer.id, current.version)
    elif initial == "EXPIRED":
        clock.now += timedelta(milliseconds=100)
    base_version = current.version
    events = _watch(application)

    def operation(name: str):
        if name == "reconcile":
            return application.timers.reconcile_expired_timer()
        if name == "update":
            return application.timers.update_timer(
                timer.id, UpdateTimer(expected_version=base_version, name="Won")
            )
        return getattr(application.timers, f"{name}_timer")(timer.id, base_version)

    results = _race(lambda: operation(left), lambda: operation(right))
    successful = [
        result for result in results if result is not None and not isinstance(result, Exception)
    ]
    assert len(successful) == 1
    final = application.timers.get_timer(timer.id, include_deleted=True)
    assert final.version == base_version + 1
    assert len(events) == 1 and events[0].entity_id == timer.id
    assert events[0].event_id.endswith(f":{final.version}")
    assert final.updated_at_utc == clock.now
    run_rows = _rows(
        app_config,
        "SELECT actual_duration_ms,ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
        {"id": timer.id},
    )
    assert len(run_rows) == 1
    expected = {
        "TIMER_PAUSED": ("PAUSED", None),
        "TIMER_RESET": ("READY", "RESET"),
        "TIMER_COMPLETED": ("FINISHED", "FINISHED" if initial == "EXPIRED" else "FINISHED_EARLY"),
        "TIMER_UPDATED": ("RUNNING", None),
        "TIMER_RESUMED": ("RUNNING", None),
        "TIMER_CANCELLED": ("CANCELLED", "CANCELLED"),
    }
    expected_state, expected_outcome = expected[events[0].event_type]
    assert final.state == expected_state
    if expected_outcome is None:
        assert run_rows[0][1:] == (None, None)
        assert final.ended_at_utc is None
    else:
        assert run_rows[0][1] is not None and run_rows[0][2] == expected_outcome
        if expected_outcome == "RESET":
            assert final.ended_at_utc is None
        else:
            assert final.ended_at_utc is not None
    if events[0].event_type == "TIMER_PAUSED":
        assert final.elapsed_before_run_ms == 10
        assert final.run_started_at_utc is None and final.deadline_at_utc is None
        assert run_rows[0][0] == 10
    elif events[0].event_type == "TIMER_RESET":
        assert final.elapsed_before_run_ms == 0
        assert final.run_started_at_utc is None and final.deadline_at_utc is None
    elif events[0].event_type == "TIMER_RESUMED":
        assert final.elapsed_before_run_ms == 10
        assert final.run_started_at_utc == clock.now and final.deadline_at_utc is not None
    elif events[0].event_type == "TIMER_COMPLETED" and initial == "EXPIRED":
        assert final.elapsed_before_run_ms == 100 and run_rows[0][0] == 100
    _assert_active_invariants(app_config)


def _timer_for_command(application, clock, command: str):
    timer = application.timers.create_timer(CreateTimer(name=command, planned_duration_ms=100))
    if command in {"pause", "complete", "cancel"}:
        return application.timers.start_timer(timer.id, timer.version)
    if command == "resume":
        running = application.timers.start_timer(timer.id, timer.version)
        return application.timers.pause_timer(timer.id, running.version)
    if command == "reset":
        return application.timers.start_timer(timer.id, timer.version)
    if command == "restore":
        return application.timers.delete_timer(timer.id, timer.version)
    return timer


@pytest.mark.parametrize(
    "command",
    ("update", "start", "pause", "resume", "reset", "complete", "cancel", "delete", "restore"),
)
def test_every_stale_timer_command_is_side_effect_free(
    application, app_config, clock, command: str
) -> None:
    timer = _timer_for_command(application, clock, command)
    before = _snapshot(app_config, timer.id)
    events = _watch(application)
    stale = timer.version + 1
    with pytest.raises(StaleVersionError):
        if command == "update":
            application.timers.update_timer(
                timer.id, UpdateTimer(expected_version=stale, name="No")
            )
        else:
            getattr(application.timers, f"{command}_timer")(timer.id, stale)
    assert _snapshot(app_config, timer.id) == before
    assert events == []


@pytest.mark.parametrize("command", ("update", "move", "delete", "restore"))
def test_every_stale_preset_command_is_side_effect_free(
    application, app_config, command: str
) -> None:
    first = application.timers.create_preset(CreateTimerPreset(name="First", duration_ms=100))
    application.timers.create_preset(CreateTimerPreset(name="Second", duration_ms=200))
    target = first
    if command == "restore":
        target = application.timers.delete_preset(first.id, first.version)
    before = _rows(app_config, "SELECT * FROM timer_presets ORDER BY id")
    events = _watch(application, _PRESET_EVENTS)
    stale = target.version + 1
    with pytest.raises(StaleVersionError):
        if command == "update":
            application.timers.update_preset(
                target.id, UpdateTimerPreset(expected_version=stale, name="No")
            )
        elif command == "move":
            application.timers.move_preset(
                target.id, MoveTimerPreset(expected_version=stale, position=1)
            )
        else:
            getattr(application.timers, f"{command}_preset")(target.id, stale)
    assert _rows(app_config, "SELECT * FROM timer_presets ORDER BY id") == before
    assert events == []


@pytest.mark.parametrize(
    ("state", "command"),
    (
        *(("READY", command) for command in ("pause", "resume", "complete", "cancel", "reset")),
        *(("RUNNING", command) for command in ("start", "resume", "delete")),
        *(("PAUSED", command) for command in ("start", "pause", "delete")),
        *(("FINISHED", command) for command in ("start", "pause", "resume", "complete", "cancel")),
        *(("CANCELLED", command) for command in ("start", "pause", "resume", "complete", "cancel")),
    ),
)
def test_complete_forbidden_transition_matrix(
    application, app_config, clock, state: str, command: str
) -> None:
    timer = application.timers.create_timer(CreateTimer(name=state, planned_duration_ms=100))
    if state != "READY":
        timer = application.timers.start_timer(timer.id, timer.version)
    if state == "PAUSED":
        timer = application.timers.pause_timer(timer.id, timer.version)
    elif state == "FINISHED":
        timer = application.timers.complete_timer(timer.id, timer.version)
    elif state == "CANCELLED":
        timer = application.timers.cancel_timer(timer.id, timer.version)
    before = _snapshot(app_config, timer.id)
    events = _watch(application)
    with pytest.raises(InvalidStateTransitionError):
        getattr(application.timers, f"{command}_timer")(timer.id, timer.version)
    assert _snapshot(app_config, timer.id) == before
    assert events == []
