from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from panelo.core.events import DomainEvent
from panelo.core.exceptions import (
    ConflictError,
    DuplicateNameError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from panelo.db.engine import create_sqlite_engine
from panelo.db.types import datetime_to_epoch_microseconds
from panelo.db.unit_of_work import SqlAlchemyUnitOfWork
from panelo.repositories.timers import TimerRepository
from panelo.schemas.tasks import CreateTask
from panelo.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    MoveTimerPreset,
    TimerPresetQuery,
    TimerQuery,
    TimerRunQuery,
    UpdateTimer,
    UpdateTimerPreset,
)
from panelo.services.timers import _translate

_TIMER_EVENTS = (
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
)


def _watch(application) -> list[DomainEvent]:
    observed: list[DomainEvent] = []
    bus = application.unit_of_work_factory()._event_bus
    for event_type in _TIMER_EVENTS:
        bus.subscribe(event_type, observed.append)
    return observed


def _fresh(app_config, statement: str, parameters: dict[str, object] | None = None):
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.exec_driver_sql(statement, parameters or {})]
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_timer_duration_rejects_non_positive_or_non_integer_values(value: object) -> None:
    with pytest.raises(PydanticValidationError):
        CreateTimer(name="Focus", planned_duration_ms=value)  # type: ignore[arg-type]


def test_timer_schema_has_no_notification_command() -> None:
    assert "notification" not in " ".join(CreateTimer.model_fields).lower()


@pytest.mark.parametrize(
    ("advance", "elapsed", "remaining", "effective", "needs_reconciliation"),
    (
        (timedelta(microseconds=999), 0, 10, "RUNNING", False),
        (timedelta(milliseconds=1), 1, 9, "RUNNING", False),
        (timedelta(milliseconds=9, microseconds=999), 9, 1, "RUNNING", False),
        (timedelta(milliseconds=10), 10, 0, "FINISHED", True),
        (timedelta(milliseconds=10, microseconds=1), 10, 0, "FINISHED", True),
        (timedelta(days=100), 10, 0, "FINISHED", True),
        (timedelta(seconds=-1), 0, 10, "RUNNING", False),
    ),
)
def test_elapsed_boundaries_use_exact_integer_milliseconds(
    application, clock, advance, elapsed, remaining, effective, needs_reconciliation
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Exact", planned_duration_ms=10))
    running = application.timers.start_timer(timer.id, timer.version)
    clock.now += advance
    view = application.timers.get_timer(timer.id)
    assert (view.effective_elapsed_ms, view.remaining_ms) == (elapsed, remaining)
    assert (view.effective_state, view.needs_reconciliation) == (effective, needs_reconciliation)
    assert view.state == "RUNNING" and view.version == running.version


def test_expiry_reads_do_not_mutate_and_reconcile_closes_one_run(
    application, app_config, clock
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Expiry", planned_duration_ms=1_000))
    started = application.timers.start_timer(timer.id, timer.version)
    deadline = started.deadline_at_utc
    assert deadline is not None
    clock.now += timedelta(seconds=5)
    before = _fresh(
        app_config,
        "SELECT state,version,updated_at_utc,deadline_at_utc FROM timers WHERE id=:id",
        {"id": timer.id},
    )
    observed = _watch(application)
    view = application.timers.get_timer(timer.id)
    assert view.state == "RUNNING" and view.effective_state == "FINISHED"
    assert view.needs_reconciliation and view.remaining_ms == 0
    assert (
        _fresh(
            app_config,
            "SELECT state,version,updated_at_utc,deadline_at_utc FROM timers WHERE id=:id",
            {"id": timer.id},
        )
        == before
    )
    (reconciled,) = application.timers.reconcile_expired_timers()
    assert reconciled.ended_at_utc == deadline
    epoch_deadline = datetime_to_epoch_microseconds(deadline)
    assert _fresh(
        app_config, "SELECT state,version,ended_at_utc FROM timers WHERE id=:id", {"id": timer.id}
    ) == [("FINISHED", started.version + 1, epoch_deadline)]
    assert _fresh(
        app_config,
        "SELECT actual_duration_ms,ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
        {"id": timer.id},
    ) == [(1_000, epoch_deadline, "FINISHED")]
    assert [event.event_type for event in observed] == ["TIMER_COMPLETED"]
    assert application.timers.reconcile_expired_timers() == ()
    assert [event.event_type for event in observed] == ["TIMER_COMPLETED"]


def test_effective_running_filter_excludes_expired_persisted_timer(application, clock) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Filter", planned_duration_ms=1))
    application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=1)
    assert application.timers.list_timers(TimerQuery(effective_states=("RUNNING",))) == ()
    assert [
        item.id
        for item in application.timers.list_timers(TimerQuery(effective_states=("FINISHED",)))
    ] == [timer.id]


def test_state_sort_uses_effective_expiry_state_and_deterministic_id_ties(
    application, clock
) -> None:
    finished = application.timers.create_timer(CreateTimer(name="Finished", planned_duration_ms=1))
    application.timers.start_timer(finished.id, finished.version)
    clock.now += timedelta(milliseconds=1)
    application.timers.reconcile_expired_timers()
    expired = application.timers.create_timer(CreateTimer(name="Expired", planned_duration_ms=1))
    application.timers.start_timer(expired.id, expired.version)
    clock.now += timedelta(milliseconds=1)
    ready = application.timers.create_timer(CreateTimer(name="Ready", planned_duration_ms=1))

    ordered = application.timers.list_timers(TimerQuery(sort="state"))
    assert [item.id for item in ordered] == [
        ready.id,
        *sorted((finished.id, expired.id)),
    ]
    expired_view = next(item for item in ordered if item.id == expired.id)
    assert expired_view.state == "RUNNING" and expired_view.effective_state == "FINISHED"


def test_start_race_runs_both_timers_with_one_open_run_each(application, app_config) -> None:
    first = application.timers.create_timer(CreateTimer(name="First", planned_duration_ms=1_000))
    second = application.timers.create_timer(CreateTimer(name="Second", planned_duration_ms=1_000))
    barrier = Barrier(2)
    observed = _watch(application)

    def start(timer):
        barrier.wait()
        return application.timers.start_timer(timer.id, timer.version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in (executor.submit(start, first), executor.submit(start, second))
        ]
    assert all(result.state == "RUNNING" for result in results)
    assert _fresh(
        app_config,
        "SELECT count(*) FROM timers WHERE deleted_at_utc IS NULL AND state IN ('RUNNING','PAUSED')",
    ) == [(2,)]
    assert _fresh(
        app_config,
        "SELECT count(DISTINCT timer_id), count(*) FROM timer_runs WHERE ended_at_utc IS NULL",
    ) == [(2, 2)]
    assert sorted(event.event_type for event in observed) == ["TIMER_STARTED", "TIMER_STARTED"]


@pytest.mark.parametrize("operation", ("pause", "complete", "cancel", "reset"))
def test_failed_active_mutation_rolls_back_timer_run_and_event(
    application, app_config, clock, monkeypatch, operation: str
) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Rollback", planned_duration_ms=1_000))
    running = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=100)
    before_timer = _fresh(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer.id})
    before_run = _fresh(app_config, "SELECT * FROM timer_runs WHERE timer_id=:id", {"id": timer.id})
    observed = _watch(application)

    def fail_after_flush(self: SqlAlchemyUnitOfWork) -> None:
        assert self.session is not None
        self.session.flush()
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_after_flush)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        getattr(application.timers, f"{operation}_timer")(timer.id, running.version)
    assert _fresh(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer.id}) == before_timer
    assert (
        _fresh(app_config, "SELECT * FROM timer_runs WHERE timer_id=:id", {"id": timer.id})
        == before_run
    )
    assert observed == []


def test_start_failure_after_run_insert_rolls_back(application, app_config, monkeypatch) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Start rollback", planned_duration_ms=1_000)
    )
    observed = _watch(application)
    original = TimerRepository.add_run

    def add_then_fail(self, run) -> None:
        original(self, run)
        raise RuntimeError("after TimerRun insertion")

    monkeypatch.setattr(TimerRepository, "add_run", add_then_fail)
    with pytest.raises(RuntimeError, match="after TimerRun insertion"):
        application.timers.start_timer(timer.id, timer.version)
    assert _fresh(
        app_config,
        "SELECT state,version,elapsed_before_run_ms,run_started_at_utc,deadline_at_utc FROM timers WHERE id=:id",
        {"id": timer.id},
    ) == [("READY", 1, 0, None, None)]
    assert _fresh(
        app_config, "SELECT count(*) FROM timer_runs WHERE timer_id=:id", {"id": timer.id}
    ) == [(0,)]
    assert observed == []


def test_stale_and_forbidden_commands_are_side_effect_free(application, app_config) -> None:
    timer = application.timers.create_timer(CreateTimer(name="State", planned_duration_ms=1_000))
    before = _fresh(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer.id})
    observed = _watch(application)
    with pytest.raises(StaleVersionError):
        application.timers.update_timer(timer.id, UpdateTimer(expected_version=2, name="No"))
    with pytest.raises(InvalidStateTransitionError):
        application.timers.pause_timer(timer.id, timer.version)
    assert _fresh(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer.id}) == before
    assert _fresh(
        app_config, "SELECT count(*) FROM timer_runs WHERE timer_id=:id", {"id": timer.id}
    ) == [(0,)]
    assert observed == []


def test_subscriber_failure_does_not_undo_committed_timer_run(
    application, app_config, clock
) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Subscriber", planned_duration_ms=1_000)
    )

    def fail(_: DomainEvent) -> None:
        raise RuntimeError("subscriber failure")

    application.unit_of_work_factory()._event_bus.subscribe("TIMER_STARTED", fail)
    clock.now += timedelta(microseconds=1)
    started = application.timers.start_timer(timer.id, timer.version)
    assert _fresh(
        app_config,
        "SELECT state,version,updated_at_utc FROM timers WHERE id=:id",
        {"id": timer.id},
    ) == [("RUNNING", started.version, datetime_to_epoch_microseconds(clock.now))]
    assert _fresh(
        app_config,
        "SELECT timer_id,actual_duration_ms,ended_at_utc,outcome FROM timer_runs WHERE timer_id=:id",
        {"id": timer.id},
    ) == [(timer.id, 0, None, None)]


def test_timer_integrity_translation_is_narrow() -> None:
    for message, expected in (
        ("UNIQUE constraint failed: timer_runs.timer_id", ConflictError),
        ("UNIQUE constraint failed: timer_presets.normalized_name", DuplicateNameError),
        ("UNIQUE constraint failed: timer_presets.sort_order", ConflictError),
        ("CHECK constraint failed: ck_timers_elapsed_range", ValidationError),
    ):
        with pytest.raises(expected):
            _translate(IntegrityError("insert", {}, sqlite3.IntegrityError(message)))
    unknown = IntegrityError(
        "insert", {}, sqlite3.IntegrityError("CHECK constraint failed: new_timer_check")
    )
    with pytest.raises(IntegrityError) as captured:
        _translate(unknown)
    assert captured.value is unknown


def test_timer_run_history_uses_one_execution_across_pause_resume(application, clock) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Run history", planned_duration_ms=100)
    )
    current = application.timers.start_timer(timer.id, timer.version)
    clock.now += timedelta(milliseconds=20)
    current = application.timers.pause_timer(timer.id, current.version)
    clock.now += timedelta(hours=1)
    current = application.timers.resume_timer(timer.id, current.version)
    clock.now += timedelta(milliseconds=30)
    current = application.timers.complete_timer(timer.id, current.version)
    runs = application.timers.list_timer_runs(timer.id, TimerRunQuery(limit=10))
    assert len(runs) == 1
    assert (runs[0].actual_duration_ms, runs[0].outcome, runs[0].ended_at_utc) == (
        50,
        "FINISHED_EARLY",
        current.ended_at_utc,
    )
    with pytest.raises(PydanticValidationError):
        TimerRunQuery(started_from_utc=clock.now, started_before_utc=clock.now)


def test_preset_dense_ordering_preserves_unchanged_versions(application, clock) -> None:
    first = application.timers.create_preset(CreateTimerPreset(name="First", duration_ms=1_000))
    middle = application.timers.create_preset(CreateTimerPreset(name="Middle", duration_ms=2_000))
    last = application.timers.create_preset(CreateTimerPreset(name="Last", duration_ms=3_000))
    before_first = application.timers.get_preset(first.id)
    before_last = application.timers.get_preset(last.id)
    moved = application.timers.move_preset(
        middle.id, MoveTimerPreset(expected_version=middle.version, position=0)
    )
    ordered = application.timers.list_presets()
    assert [item.id for item in ordered] == [middle.id, first.id, last.id]
    assert [item.sort_order for item in ordered] == [0, 1, 2]
    assert application.timers.get_preset(first.id).version == before_first.version + 1
    assert application.timers.get_preset(last.id).version == before_last.version
    updated = application.timers.update_preset(
        moved.id, UpdateTimerPreset(expected_version=moved.version, duration_ms=4_000)
    )
    timer = application.timers.create_timer(CreateTimer(name="Snapshot", preset_id=updated.id))
    deleted = application.timers.delete_preset(updated.id, updated.version)
    assert timer.planned_duration_ms == 4_000 and timer.preset_id == updated.id
    assert [item.id for item in application.timers.list_presets()] == [first.id, last.id]
    restored = application.timers.restore_preset(deleted.id, deleted.version)
    assert restored.sort_order == 0
    assert [item.sort_order for item in application.timers.list_presets()] == [0, 1, 2]
    assert [item.id for item in application.timers.list_presets(TimerPresetQuery(sort="name"))] == [
        first.id,
        last.id,
        restored.id,
    ]


def test_timer_query_link_filters_deleted_visibility_and_pagination(application) -> None:
    preset = application.timers.create_preset(CreateTimerPreset(name="Preset", duration_ms=1_000))
    linked = application.timers.create_timer(CreateTimer(name="Linked", preset_id=preset.id))
    unlinked = application.timers.create_timer(
        CreateTimer(name="Unlinked", planned_duration_ms=2_000)
    )
    deleted = application.timers.delete_timer(unlinked.id, unlinked.version)
    assert [
        item.id
        for item in application.timers.list_timers(
            TimerQuery(preset_link="PRESET", preset_id=preset.id)
        )
    ] == [linked.id]
    assert [
        item.id for item in application.timers.list_timers(TimerQuery(preset_link="NONE"))
    ] == []
    assert [
        item.id
        for item in application.timers.list_timers(
            TimerQuery(include_deleted=True, preset_link="NONE")
        )
    ] == [deleted.id]
    page_one = application.timers.list_timers(TimerQuery(sort="name", limit=1, offset=0))
    page_two = application.timers.list_timers(TimerQuery(sort="name", limit=1, offset=1))
    assert page_one == application.timers.list_timers(TimerQuery(sort="name", limit=1, offset=0))
    assert {item.id for item in page_one}.isdisjoint({item.id for item in page_two})
    with pytest.raises(PydanticValidationError):
        TimerQuery(task_link="ANY", task_id="invalid")


def test_exact_empty_or_unknown_link_ids_never_broaden_timer_queries(application) -> None:
    preset = application.timers.create_preset(CreateTimerPreset(name="Exact", duration_ms=1_000))
    task = application.tasks.create_task(CreateTask(title="Exact task"))
    linked = application.timers.create_timer(
        CreateTimer(name="Linked", preset_id=preset.id, task_id=task.id)
    )
    assert application.timers.list_timers(TimerQuery(task_link="TASK", task_id="")) == ()
    assert application.timers.list_timers(TimerQuery(preset_link="PRESET", preset_id="")) == ()
    assert (
        application.timers.list_timers(TimerQuery(task_link="TASK", task_id="missing-task")) == ()
    )
    assert (
        application.timers.list_timers(TimerQuery(preset_link="PRESET", preset_id="missing-preset"))
        == ()
    )
    assert [item.id for item in application.timers.list_timers(TimerQuery(task_link="TASK"))] == [
        linked.id
    ]


def test_task_links_are_historical_but_new_deleted_links_are_rejected(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Linked task"))
    timer = application.timers.create_timer(
        CreateTimer(name="Historical task", planned_duration_ms=1_000, task_id=task.id)
    )
    deleted_task = application.tasks.delete_task(task.id, task.version)
    renamed = application.timers.update_timer(
        timer.id, UpdateTimer(expected_version=timer.version, name="Still linked", task_id=task.id)
    )
    assert renamed.task_id == task.id
    other = application.timers.create_timer(CreateTimer(name="Other", planned_duration_ms=1_000))
    with pytest.raises(NotFoundError):
        application.timers.update_timer(
            other.id, UpdateTimer(expected_version=other.version, task_id=deleted_task.id)
        )
    started = application.timers.start_timer(renamed.id, renamed.version)
    completed = application.timers.complete_timer(renamed.id, started.version)
    assert application.tasks.get_task(task.id, include_deleted=True).version == deleted_task.version
    assert application.timers.list_timer_runs(renamed.id)[0].task_id == task.id
    assert completed.task_id == task.id


@pytest.mark.parametrize("operation", ("resume", "reconcile"))
def test_resume_and_reconcile_post_mutation_failures_rollback_everything(
    application, app_config, clock, monkeypatch, operation: str
) -> None:
    timer = application.timers.create_timer(CreateTimer(name=operation, planned_duration_ms=100))
    current = application.timers.start_timer(timer.id, timer.version)
    if operation == "resume":
        clock.now += timedelta(milliseconds=10)
        current = application.timers.pause_timer(timer.id, current.version)
    else:
        clock.now += timedelta(milliseconds=100)
    before_timer = _fresh(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer.id})
    before_run = _fresh(app_config, "SELECT * FROM timer_runs WHERE timer_id=:id", {"id": timer.id})
    observed = _watch(application)

    def fail_after_flush(self: SqlAlchemyUnitOfWork) -> None:
        assert self.session is not None
        self.session.flush()
        raise RuntimeError("post-mutation failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_after_flush)
    with pytest.raises(RuntimeError, match="post-mutation failure"):
        if operation == "resume":
            application.timers.resume_timer(timer.id, current.version)
        else:
            application.timers.reconcile_expired_timers()
    assert _fresh(app_config, "SELECT * FROM timers WHERE id=:id", {"id": timer.id}) == before_timer
    assert (
        _fresh(app_config, "SELECT * FROM timer_runs WHERE timer_id=:id", {"id": timer.id})
        == before_run
    )
    assert observed == []


def test_every_timer_and_preset_event_has_the_exact_public_contract(application, clock) -> None:
    observed: list[DomainEvent] = []
    bus = application.unit_of_work_factory()._event_bus
    timer_kinds = _TIMER_EVENTS
    preset_kinds = (
        "TIMER_PRESET_CREATED",
        "TIMER_PRESET_UPDATED",
        "TIMER_PRESET_MOVED",
        "TIMER_PRESET_DELETED",
        "TIMER_PRESET_RESTORED",
    )
    for kind in (*timer_kinds, *preset_kinds):
        bus.subscribe(kind, observed.append)

    preset = application.timers.create_preset(CreateTimerPreset(name="Events", duration_ms=100))
    preset = application.timers.update_preset(
        preset.id, UpdateTimerPreset(expected_version=preset.version, duration_ms=200)
    )
    other = application.timers.create_preset(CreateTimerPreset(name="Other", duration_ms=300))
    preset = application.timers.move_preset(
        preset.id, MoveTimerPreset(expected_version=preset.version, position=1)
    )
    deleted_preset = application.timers.delete_preset(preset.id, preset.version)
    application.timers.restore_preset(deleted_preset.id, deleted_preset.version)

    timer = application.timers.create_timer(CreateTimer(name="Events", planned_duration_ms=100))
    timer = application.timers.update_timer(
        timer.id, UpdateTimer(expected_version=timer.version, name="Renamed")
    )
    timer = application.timers.start_timer(timer.id, timer.version)
    timer = application.timers.pause_timer(timer.id, timer.version)
    timer = application.timers.resume_timer(timer.id, timer.version)
    timer = application.timers.reset_timer(timer.id, timer.version)
    timer = application.timers.start_timer(timer.id, timer.version)
    timer = application.timers.complete_timer(timer.id, timer.version)
    timer = application.timers.reset_timer(timer.id, timer.version)
    timer = application.timers.delete_timer(timer.id, timer.version)
    application.timers.restore_timer(timer.id, timer.version)
    canceller = application.timers.create_timer(CreateTimer(name="Cancel", planned_duration_ms=100))
    canceller = application.timers.start_timer(canceller.id, canceller.version)
    application.timers.cancel_timer(canceller.id, canceller.version)

    expected = set(timer_kinds) | set(preset_kinds)
    assert {event.event_type for event in observed} == expected
    for event in observed:
        version = int(event.event_id.rsplit(":", 1)[1])
        prefix = "timer_preset" if event.event_type.startswith("TIMER_PRESET_") else "timer"
        assert event.event_id == f"{prefix}:{event.event_type.lower()}:{event.entity_id}:{version}"
        assert event.occurred_at_utc == clock.now
        assert event.payload == {}
    assert other.id != preset.id
