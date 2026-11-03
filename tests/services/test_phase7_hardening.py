from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    StaleVersionError,
)
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork
from productivity_app.repositories.focus import FocusRepository
from productivity_app.schemas.focus import (
    AdvanceFocusSequence,
    CancelFocusSequence,
    CreateFocusSequence,
)
from productivity_app.schemas.timers import UpdateTimer
from productivity_app.services.focus import FocusService


def _rows(app_config, sql: str, params: dict[str, object] | None = None) -> list[tuple[object, ...]]:
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.exec_driver_sql(sql, params or {})]
    finally:
        engine.dispose()


def _finished_first_phase(application, clock):
    sequence = application.focus.create_sequence(CreateFocusSequence())
    timer = application.timers.start_timer(
        sequence.current_session.timer.id, sequence.current_session.timer.version
    )
    clock.now += timedelta(milliseconds=1)
    application.timers.complete_timer(timer.id, timer.version)
    return sequence


def test_advance_is_stale_safe_and_only_creates_one_next_phase(application, clock) -> None:
    sequence = _finished_first_phase(application, clock)
    events = []
    subscription = application.subscribe("FOCUS_PHASE_ADVANCED", events.append)

    advanced = application.focus.advance_sequence(
        sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
    )
    with pytest.raises(StaleVersionError):
        application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        )
    subscription.close()

    assert advanced.version == 2
    assert advanced.current_phase_index == 1
    assert advanced.current_session.phase_kind == "SHORT_BREAK"
    assert len(events) == 1
    assert events[0].event_id == f"focus_sequence:focus_phase_advanced:{sequence.id}:2"
    assert events[0].payload == {}
    assert len(application.focus.list_sessions()) == 2


def test_final_phase_completes_only_after_explicit_advance(application, clock) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    events: list[DomainEvent] = []
    subscription = application.subscribe("FOCUS_SEQUENCE_COMPLETED", events.append)
    for index in range(8):
        current = application.focus.get_sequence(sequence.id)
        running = application.timers.start_timer(
            current.current_session.timer.id, current.current_session.timer.version
        )
        clock.now += timedelta(milliseconds=1)
        application.timers.complete_timer(running.id, running.version)
        sequence = application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=current.version)
        )
        if index < 7:
            assert sequence.state == "ACTIVE"
    assert sequence.state == "COMPLETED"
    assert sequence.current_phase_index == 7
    assert sequence.completed_at_utc is not None
    subscription.close()
    assert len(events) == 1
    assert events[0].event_id == f"focus_sequence:focus_sequence_completed:{sequence.id}:9"
    assert events[0].entity_id == sequence.id and events[0].payload == {}
    with pytest.raises(InvalidStateTransitionError):
        application.focus.cancel_sequence(
            sequence.id, CancelFocusSequence(expected_version=sequence.version)
        )


def test_cancelled_sequence_cannot_advance_and_publishes_only_its_event(application) -> None:
    sequence = application.focus.create_sequence(CreateFocusSequence())
    events = []
    subscription = application.subscribe("FOCUS_SEQUENCE_CANCELLED", events.append)
    cancelled = application.focus.cancel_sequence(
        sequence.id, CancelFocusSequence(expected_version=sequence.version)
    )
    subscription.close()

    assert cancelled.state == "CANCELLED"
    assert cancelled.version == 2
    assert len(events) == 1
    assert events[0].event_id == f"focus_sequence:focus_sequence_cancelled:{sequence.id}:2"
    with pytest.raises(InvalidStateTransitionError):
        application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=cancelled.version)
        )


def test_sequence_timer_failure_rolls_back_every_pending_row(application, monkeypatch) -> None:
    original = FocusService._ready_timer

    def fail_after_timer(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected timer failure")

    monkeypatch.setattr(FocusService, "_ready_timer", fail_after_timer)
    with pytest.raises(RuntimeError, match="injected"):
        application.focus.create_sequence(CreateFocusSequence())

    assert application.focus.list_sequences() == ()
    assert application.focus.list_sessions() == ()
    assert application.timers.list_timers() == ()


def test_timer_insert_then_session_failure_rolls_back_fresh_connection(application, app_config, monkeypatch) -> None:
    original = FocusRepository.add_session

    def fail_after_session(self, session):
        original(self, session)
        raise RuntimeError("injected session failure")

    monkeypatch.setattr(FocusRepository, "add_session", fail_after_session)
    with pytest.raises(RuntimeError, match="session failure"):
        application.focus.create_sequence(CreateFocusSequence())
    assert _rows(app_config, "SELECT count(*) FROM focus_sequences") == [(0,)]
    assert _rows(app_config, "SELECT count(*) FROM focus_sessions") == [(0,)]
    assert _rows(app_config, "SELECT count(*) FROM timers") == [(0,)]


def test_advance_failure_after_version_claim_rolls_back_everything(application, app_config, clock, monkeypatch) -> None:
    sequence = _finished_first_phase(application, clock)
    original = FocusService._ready_timer

    def fail_after_timer(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected advance failure")

    monkeypatch.setattr(FocusService, "_ready_timer", fail_after_timer)
    with pytest.raises(RuntimeError, match="advance failure"):
        application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        )
    assert _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id}) == [(0, 1)]
    assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id}) == [(1,)]
    assert _rows(app_config, "SELECT count(*) FROM timers") == [(1,)]


@pytest.mark.parametrize("operation", ("create", "advance"))
def test_focus_commit_failure_rolls_back_and_publishes_nothing(application, app_config, clock, monkeypatch, operation) -> None:
    sequence = _finished_first_phase(application, clock) if operation == "advance" else None
    events: list[DomainEvent] = []
    subscription = application.subscribe("FOCUS_SEQUENCE_STARTED", events.append)
    original = SqlAlchemyUnitOfWork.commit

    def fail_commit(self):
        assert self.session is not None
        self.session.rollback()
        self._events.clear()
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit failure"):
        if sequence is None:
            application.focus.create_sequence(CreateFocusSequence())
        else:
            application.focus.advance_sequence(
                sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
            )
    subscription.close()
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", original)
    if sequence is None:
        assert _rows(app_config, "SELECT count(*) FROM focus_sequences") == [(0,)]
    else:
        assert _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id}) == [(0, 1)]
        assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id}) == [(1,)]
    assert events == []


@pytest.mark.parametrize("operation", ("create", "advance"))
def test_subscriber_failure_never_reverses_committed_focus_mutation(application, app_config, clock, operation) -> None:
    sequence = _finished_first_phase(application, clock) if operation == "advance" else None
    observed: list[DomainEvent] = []

    def fail_after_observing(event: DomainEvent) -> None:
        observed.append(event)
        raise RuntimeError("subscriber failed")

    kind = "FOCUS_PHASE_ADVANCED" if operation == "advance" else "FOCUS_SEQUENCE_STARTED"
    subscription = application.subscribe(kind, fail_after_observing)
    if sequence is None:
        result = application.focus.create_sequence(CreateFocusSequence())
        assert _rows(app_config, "SELECT version FROM focus_sequences WHERE id=:id", {"id": result.id}) == [(1,)]
        assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": result.id}) == [(1,)]
    else:
        result = application.focus.advance_sequence(
            sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
        )
        assert _rows(app_config, "SELECT current_phase_index, version FROM focus_sequences WHERE id=:id", {"id": sequence.id}) == [(1, 2)]
        assert _rows(app_config, "SELECT count(*) FROM focus_sessions WHERE sequence_id=:id", {"id": sequence.id}) == [(2,)]
    subscription.close()
    assert observed and observed[0].entity_id == result.id


def test_focus_events_have_exact_contract_and_stale_cancel_has_no_event(application, clock) -> None:
    events: list[DomainEvent] = []
    subscriptions = [
        application.subscribe(kind, events.append)
        for kind in (
            "TIMER_CREATED", "FOCUS_SEQUENCE_STARTED", "FOCUS_PHASE_ADVANCED",
            "FOCUS_SEQUENCE_COMPLETED", "FOCUS_SEQUENCE_CANCELLED",
        )
    ]
    sequence = application.focus.create_sequence(CreateFocusSequence())
    assert [event.event_type for event in events[:2]] == ["TIMER_CREATED", "FOCUS_SEQUENCE_STARTED"]
    started = events[1]
    assert started.event_id == f"focus_sequence:focus_sequence_started:{sequence.id}:1"
    assert started.entity_id == sequence.id and started.payload == {} and started.occurred_at_utc == clock.now
    running = application.timers.start_timer(sequence.current_session.timer.id, sequence.current_session.timer.version)
    clock.now += timedelta(milliseconds=1)
    application.timers.complete_timer(running.id, running.version)
    advanced = application.focus.advance_sequence(sequence.id, AdvanceFocusSequence(expected_version=1))
    advanced_event = [event for event in events if event.event_type == "FOCUS_PHASE_ADVANCED"][-1]
    assert advanced_event.event_id == f"focus_sequence:focus_phase_advanced:{sequence.id}:2"
    with pytest.raises(StaleVersionError):
        application.focus.cancel_sequence(sequence.id, CancelFocusSequence(expected_version=1))
    assert [event for event in events if event.event_type == "FOCUS_SEQUENCE_CANCELLED"] == []
    current = advanced
    active_break = application.timers.start_timer(
        current.current_session.timer.id, current.current_session.timer.version
    )
    application.timers.cancel_timer(active_break.id, active_break.version)
    application.focus.cancel_sequence(sequence.id, CancelFocusSequence(expected_version=2))
    cancelled_event = [event for event in events if event.event_type == "FOCUS_SEQUENCE_CANCELLED"][-1]
    assert cancelled_event.event_id == f"focus_sequence:focus_sequence_cancelled:{sequence.id}:3"
    assert cancelled_event.entity_id == sequence.id and cancelled_event.payload == {}
    for subscription in subscriptions:
        subscription.close()


def test_historical_phase_rejects_all_configuration_mutations_without_drift(application, clock) -> None:
    sequence = _finished_first_phase(application, clock)
    historical = application.focus.get_sequence(sequence.id).current_session.timer
    application.focus.advance_sequence(sequence.id, AdvanceFocusSequence(expected_version=sequence.version))
    original = application.timers.get_timer(historical.id)
    events: list[DomainEvent] = []
    subscription = application.subscribe("TIMER_UPDATED", events.append)
    for patch in (
        UpdateTimer(expected_version=original.version, planned_duration_ms=2_000),
        UpdateTimer(expected_version=original.version, preset_id=None),
        UpdateTimer(expected_version=original.version, task_id=None),
    ):
        with pytest.raises(InvalidStateTransitionError):
            application.timers.update_timer(original.id, patch)
    with pytest.raises(InvalidStateTransitionError):
        application.timers.delete_timer(original.id, original.version)
    subscription.close()
    persisted = application.timers.get_timer(original.id)
    assert (persisted.version, persisted.updated_at_utc, persisted.planned_duration_ms) == (
        original.version, original.updated_at_utc, original.planned_duration_ms
    )
    assert events == []


def test_create_sequence_translates_only_exact_active_sequence_constraint(
    application, monkeypatch
) -> None:
    injected = IntegrityError(
        "INSERT focus_sequences",
        {},
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: index 'ux_focus_sequences_one_active'"
        ),
    )

    def fail_commit(_self) -> None:
        raise injected

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)
    with pytest.raises(ConflictError, match="already active") as captured:
        application.focus.create_sequence(CreateFocusSequence())
    assert captured.value.__cause__ is injected


def test_create_sequence_preserves_unknown_integrity_error(application, monkeypatch) -> None:
    injected = IntegrityError(
        "INSERT unrelated",
        {},
        sqlite3.IntegrityError("UNIQUE constraint failed: unrelated.value"),
    )

    def fail_commit(_self) -> None:
        raise injected

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)
    with pytest.raises(IntegrityError) as captured:
        application.focus.create_sequence(CreateFocusSequence())
    assert captured.value is injected
