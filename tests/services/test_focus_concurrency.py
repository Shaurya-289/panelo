from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from panelo.core.events import DomainEvent
from panelo.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
)
from panelo.db.engine import create_sqlite_engine
from panelo.schemas.focus import (
    AdvanceFocusSequence,
    CancelFocusSequence,
    CreateFocusSequence,
)
from panelo.schemas.tasks import CreateTask


def _rows(app_config, sql: str, params: dict[str, object] | None = None) -> list[tuple[object, ...]]:
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.exec_driver_sql(sql, params or {})]
    finally:
        engine.dispose()


def _race(*operations):
    barrier = Barrier(len(operations))

    def invoke(operation):
        barrier.wait()
        try:
            return operation()
        except (ConflictError, InvalidStateTransitionError, NotFoundError, StaleVersionError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        return [future.result() for future in [executor.submit(invoke, op) for op in operations]]


def _finished_sequence(application, clock):
    sequence = application.focus.create_sequence(CreateFocusSequence())
    current = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=1)
    application.timers.complete_timer(current.id, current.version)
    return sequence


def test_create_sequence_race_has_one_persisted_winner(application, app_config) -> None:
    events: list[DomainEvent] = []
    subscription = application.subscribe("FOCUS_SEQUENCE_STARTED", events.append)
    outcomes = _race(
        lambda: application.focus.create_sequence(CreateFocusSequence()),
        lambda: application.focus.create_sequence(CreateFocusSequence()),
    )
    subscription.close()

    winners = [item for item in outcomes if not isinstance(item, Exception)]
    losers = [item for item in outcomes if isinstance(item, Exception)]
    assert len(winners) == len(losers) == 1
    assert isinstance(losers[0], ConflictError)
    assert winners[0].version == 1
    assert _rows(app_config, "SELECT id, version FROM focus_sequences WHERE state='ACTIVE'") == [
        (winners[0].id, 1)
    ]
    assert _rows(app_config, "SELECT count(*) FROM focus_sessions") == [(1,)]
    assert _rows(app_config, "SELECT count(*) FROM timers WHERE state='READY'") == [(1,)]
    assert [event.entity_id for event in events] == [winners[0].id]


def test_duplicate_advance_race_claims_sequence_once(application, app_config, clock) -> None:
    sequence = _finished_sequence(application, clock)
    advanced_events: list[DomainEvent] = []
    created_events: list[DomainEvent] = []
    first = application.subscribe("FOCUS_PHASE_ADVANCED", advanced_events.append)
    second = application.subscribe("TIMER_CREATED", created_events.append)
    outcomes = _race(
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
    )
    first.close()
    second.close()

    winners = [item for item in outcomes if not isinstance(item, Exception)]
    losers = [item for item in outcomes if isinstance(item, Exception)]
    assert len(winners) == len(losers) == 1
    assert isinstance(losers[0], StaleVersionError)
    assert winners[0].version == 2
    assert _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id}) == [(1, 2)]
    assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id}) == [(2,)]
    assert len(advanced_events) == 1
    assert len(created_events) == 1


def test_advance_cancel_race_has_only_serial_outcome(application, app_config, clock) -> None:
    sequence = _finished_sequence(application, clock)
    outcomes = _race(
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
        lambda: application.focus.cancel_sequence(
            sequence.id, CancelFocusSequence(expected_version=sequence.version)
        ),
    )
    winners = [item for item in outcomes if not isinstance(item, Exception)]
    assert len(winners) == 1
    row = _rows(app_config, "SELECT state,current_phase_index,version FROM focus_sequences WHERE id=:id", {"id": sequence.id})[0]
    assert row in {("ACTIVE", 1, 2), ("CANCELLED", 0, 2)}
    sessions = _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id})[0][0]
    assert sessions == (2 if row[0] == "ACTIVE" else 1)


def test_advance_reset_race_never_creates_an_invalid_next_phase(application, app_config, clock) -> None:
    sequence = _finished_sequence(application, clock)
    timer = application.focus.get_sequence(sequence.id).current_session.timer
    events: list[DomainEvent] = []
    subscription = application.subscribe("FOCUS_PHASE_ADVANCED", events.append)
    outcomes = _race(
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
        lambda: application.timers.reset_timer(timer.id, timer.version),
    )
    subscription.close()

    row = _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id})[0]
    sessions = _rows(app_config, "SELECT phase_index FROM focus_sessions WHERE sequence_id=:id ORDER BY phase_index", {"id": sequence.id})
    if row == (1, 2):
        assert sessions == [(0,), (1,)]
        assert len(events) == 1
    else:
        assert row == (0, 1)
        assert sessions == [(0,)]
        assert len(events) == 0
    assert len([item for item in outcomes if not isinstance(item, Exception)]) == 1


def test_advance_cancel_running_timer_race_never_advances_from_cancelled_run(application, app_config, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    timer = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    events: list[DomainEvent] = []
    subscription = application.subscribe("FOCUS_PHASE_ADVANCED", events.append)
    outcomes = _race(
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
        lambda: application.timers.cancel_timer(timer.id, timer.version),
    )
    subscription.close()

    assert any(not isinstance(item, Exception) for item in outcomes)
    assert _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id}) == [(0, 1)]
    assert _rows(app_config, "SELECT state FROM timers WHERE id=:id", {"id": timer.id}) == [("CANCELLED",)]
    assert _rows(app_config, "SELECT outcome FROM timer_runs WHERE timer_id=:id", {"id": timer.id}) == [("CANCELLED",)]
    assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id}) == [(1,)]
    assert events == []


def test_complete_advance_race_requires_persisted_finished_timer_before_later_advance(application, app_config, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    timer = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    outcomes = _race(
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
        lambda: application.timers.complete_timer(timer.id, timer.version),
    )
    assert 1 <= sum(not isinstance(item, Exception) for item in outcomes) <= 2
    persisted = application.focus.get_sequence(sequence.id)
    if persisted.current_phase_index == 0:
        assert persisted.version == 1
        advanced = application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=persisted.version)
        )
        assert advanced.current_phase_index == 1
    else:
        assert persisted.current_phase_index == 1 and persisted.version == 2
    assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id}) == [(2,)]


def test_expiry_reconcile_advance_race_requires_explicit_post_reconcile_advance(application, app_config, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    timer = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=1_500_000)
    completed: list[DomainEvent] = []
    subscription = application.subscribe("TIMER_COMPLETED", completed.append)
    outcomes = _race(
        application.timers.reconcile_expired_timers,
        lambda: application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        ),
    )
    subscription.close()
    assert 1 <= sum(not isinstance(item, Exception) for item in outcomes) <= 2
    sequence_row = _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id})[0]
    assert sequence_row in {(0, 1), (1, 2)}
    assert _rows(app_config, "SELECT state FROM timers WHERE id=:id", {"id": timer.id}) == [("FINISHED",)]
    assert len(completed) == 1
    if sequence_row == (0, 1):
        advanced = application.focus.advance_sequence(sequence.id, AdvanceFocusSequence(expected_version=1))
        assert advanced.current_phase_index == 1


def test_sequence_creation_task_delete_race_keeps_only_serially_valid_link(application, app_config) -> None:
    task = application.tasks.create_task(CreateTask(title="Focus task"))
    outcomes = _race(
        lambda: application.focus.create_sequence(CreateFocusSequence(task_id=task.id)),
        lambda: application.tasks.delete_task(task.id, task.version),
    )
    sequences = _rows(app_config, "SELECT task_id FROM focus_sequences")
    if sequences:
        assert sequences == [(task.id,)]
        assert _rows(app_config, "SELECT count(*) FROM focus_sessions") == [(1,)]
    else:
        assert _rows(app_config, "SELECT count(*) FROM focus_sessions") == [(0,)]
    assert any(not isinstance(item, Exception) for item in outcomes)
