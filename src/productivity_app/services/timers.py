"""Countdown timer and preset workflows; elapsed time is derived from UTC anchors."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy.exc import IntegrityError

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ConflictError,
    DatabaseCorruptionError,
    DuplicateNameError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.core.ids import IdGenerator, Uuid4Generator
from productivity_app.core.normalization import normalize_label
from productivity_app.db.models.initial import Task, Timer, TimerPreset, TimerRun
from productivity_app.db.models.pomodoro import FocusSequence, FocusSession
from productivity_app.db.types import datetime_to_epoch_microseconds
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.repositories.timers import TimerRepository
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    MoveTimerPreset,
    TimerPresetQuery,
    TimerPresetView,
    TimerQuery,
    TimerRunOutcomeValue,
    TimerRunQuery,
    TimerRunView,
    TimerStateValue,
    TimerSummary,
    TimerView,
    UpdateTimer,
    UpdateTimerPreset,
)

_ACTIVE = {"RUNNING", "PAUSED"}
_TIMER_CHECKS = (
    "ck_timers_positive_duration",
    "ck_timers_elapsed_range",
    "ck_timers_state_values",
    "ck_timers_state_timestamps",
    "ck_timer_runs_positive_duration",
    "ck_timer_runs_actual_duration",
    "ck_timer_runs_outcome_values",
    "ck_timer_runs_end_outcome",
    "ck_timer_presets_positive_duration",
    "ck_timer_presets_sort_order",
    "ck_timer_presets_is_builtin_bool",
)


def _translate(exc: IntegrityError) -> None:
    message = str(exc.orig).lower() if exc.orig else ""
    if (
        "ux_timer_runs_one_open" in message
        or "unique constraint failed: timer_runs.timer_id" in message
    ):
        raise ConflictError("Timer already has an open execution run.") from exc
    if (
        "ux_timer_presets_active_name" in message
        or "unique constraint failed: timer_presets.normalized_name" in message
    ):
        raise DuplicateNameError("A timer preset with that name already exists.") from exc
    if (
        "ux_timer_presets_active_position" in message
        or "unique constraint failed: timer_presets.sort_order" in message
    ):
        raise ConflictError("Timer preset position conflicts with current data.") from exc
    if any(f"check constraint failed: {name}" in message for name in _TIMER_CHECKS):
        raise ValidationError("The requested timer change violates a domain invariant.") from exc
    if (
        "unique constraint failed: timers.id" in message
        or "unique constraint failed: timer_runs.id" in message
        or "unique constraint failed: timer_presets.id" in message
    ):
        raise ConflictError("The requested timer change conflicts with current data.") from exc
    raise exc


def _ms_between(start: datetime, end: datetime) -> int:
    return max(
        0, (datetime_to_epoch_microseconds(end) - datetime_to_epoch_microseconds(start)) // 1_000
    )


def _elapsed(timer: Timer, now: datetime) -> int:
    if timer.state != "RUNNING":
        return timer.elapsed_before_run_ms
    if timer.run_started_at_utc is None:
        raise DatabaseCorruptionError("Running timer has no run anchor.")
    return min(
        timer.planned_duration_ms,
        timer.elapsed_before_run_ms + _ms_between(timer.run_started_at_utc, now),
    )


def _view(timer: Timer, now: datetime) -> TimerView:
    effective_elapsed = _elapsed(timer, now)
    expired = (
        timer.state == "RUNNING"
        and timer.deadline_at_utc is not None
        and now >= timer.deadline_at_utc
    )
    return TimerView(
        id=timer.id,
        name=timer.name,
        preset_id=timer.preset_id,
        task_id=timer.task_id,
        state=cast(TimerStateValue, timer.state),
        effective_state=cast(TimerStateValue, "FINISHED" if expired else timer.state),
        planned_duration_ms=timer.planned_duration_ms,
        elapsed_before_run_ms=timer.elapsed_before_run_ms,
        effective_elapsed_ms=effective_elapsed,
        remaining_ms=max(0, timer.planned_duration_ms - effective_elapsed),
        run_started_at_utc=timer.run_started_at_utc,
        deadline_at_utc=timer.deadline_at_utc,
        first_started_at_utc=timer.first_started_at_utc,
        ended_at_utc=timer.ended_at_utc,
        needs_reconciliation=expired,
        created_at_utc=timer.created_at_utc,
        updated_at_utc=timer.updated_at_utc,
        version=timer.version,
        deleted_at_utc=timer.deleted_at_utc,
    )


def _preset_view(preset: TimerPreset) -> TimerPresetView:
    return TimerPresetView(
        id=preset.id,
        name=preset.name,
        duration_ms=preset.duration_ms,
        sort_order=preset.sort_order,
        is_builtin=preset.is_builtin,
        created_at_utc=preset.created_at_utc,
        updated_at_utc=preset.updated_at_utc,
        version=preset.version,
        deleted_at_utc=preset.deleted_at_utc,
    )


def _run_view(run: TimerRun) -> TimerRunView:
    assert run.ended_at_utc is not None and run.outcome is not None
    return TimerRunView(
        id=run.id,
        timer_id=run.timer_id,
        task_id=run.task_id,
        planned_duration_ms=run.planned_duration_ms,
        actual_duration_ms=run.actual_duration_ms,
        started_at_utc=run.started_at_utc,
        ended_at_utc=run.ended_at_utc,
        outcome=cast("TimerRunOutcomeValue", run.outcome),
    )


class TimerService:
    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None
    ) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    @staticmethod
    def _repo(uow: SqlAlchemyUnitOfWork) -> TimerRepository:
        assert uow.timers is not None
        return uow.timers

    def _event(
        self,
        uow: SqlAlchemyUnitOfWork,
        kind: str,
        entity: Timer | TimerPreset,
        now: datetime,
        *,
        preset: bool = False,
    ) -> None:
        prefix = "timer_preset" if preset else "timer"
        uow.collect_event(
            DomainEvent(
                kind, entity.id, now, f"{prefix}:{kind.lower()}:{entity.id}:{entity.version}"
            )
        )

    @staticmethod
    def _commit(uow: SqlAlchemyUnitOfWork) -> None:
        try:
            uow.commit()
        except IntegrityError as exc:
            _translate(exc)

    @staticmethod
    def _flush(uow: SqlAlchemyUnitOfWork) -> None:
        assert uow.session is not None
        try:
            uow.session.flush()
        except IntegrityError as exc:
            _translate(exc)

    @staticmethod
    def _active_task(uow: SqlAlchemyUnitOfWork, task_id: str) -> None:
        assert uow.tasks is not None
        task: Task | None = uow.tasks.get(task_id)
        if task is None or task.deleted_at_utc is not None:
            raise NotFoundError("Task was not found.")

    def _timer(self, uow: SqlAlchemyUnitOfWork, timer_id: str, *, deleted: bool = False) -> Timer:
        timer = self._repo(uow).get(timer_id)
        if timer is None or (
            timer.deleted_at_utc is None if deleted else timer.deleted_at_utc is not None
        ):
            raise NotFoundError("Timer was not found.")
        return timer

    def _claim(
        self,
        uow: SqlAlchemyUnitOfWork,
        timer: Timer,
        expected: int,
        now: datetime,
        *,
        deleted: bool = False,
    ) -> None:
        if not self._repo(uow).claim_version(timer.id, expected, now, deleted=deleted):
            raise StaleVersionError("Timer changed; refresh and retry.")

    @staticmethod
    def _focus_membership(
        uow: SqlAlchemyUnitOfWork, timer_id: str
    ) -> tuple[FocusSession, FocusSequence | None] | None:
        """Return Phase 7 metadata when present without affecting ordinary timers."""
        assert uow.focus is not None
        return uow.focus.timer_membership(timer_id)

    def _guard_focus_timer(
        self, uow: SqlAlchemyUnitOfWork, timer: Timer, operation: str, values: set[str] | None = None
    ) -> None:
        membership = self._focus_membership(uow, timer.id)
        if membership is None:
            return
        session, sequence = membership
        if sequence is None:  # A standalone focus session is an ordinary timer.
            return
        if operation == "update":
            if values is not None and values <= {"name"}:
                return
            raise InvalidStateTransitionError("Focus sequence timer configuration is immutable.")
        if operation == "delete" and sequence.state == "ACTIVE":
            raise InvalidStateTransitionError("Current focus sequence timer cannot be deleted.")
        if operation in {"start", "reset"} and (
            sequence.state != "ACTIVE" or session.phase_index != sequence.current_phase_index
        ):
            raise InvalidStateTransitionError("Only the current active focus phase may be changed.")

    @staticmethod
    def _reject_expired(timer: Timer, now: datetime) -> None:
        if (
            timer.state == "RUNNING"
            and timer.deadline_at_utc is not None
            and now >= timer.deadline_at_utc
        ):
            raise InvalidStateTransitionError("Reconcile expired timer first.")

    def _preset(
        self, uow: SqlAlchemyUnitOfWork, preset_id: str, *, active: bool = True
    ) -> TimerPreset:
        preset = self._repo(uow).get_preset(preset_id)
        if preset is None or (active and preset.deleted_at_utc is not None):
            raise NotFoundError("Timer preset was not found.")
        return preset

    def create_timer(self, command: CreateTimer) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            if command.task_id is not None:
                self._active_task(uow, command.task_id)
            preset = self._preset(uow, command.preset_id) if command.preset_id else None
            if command.planned_duration_ms is not None:
                duration = command.planned_duration_ms
            elif preset is not None:
                duration = preset.duration_ms
            else:
                assert uow.settings is not None
                settings = uow.settings.get()
                if settings is None:
                    raise DatabaseCorruptionError("Settings singleton is missing.")
                duration = settings.default_timer_duration_ms
            timer = Timer(
                id=self._ids.new(),
                name=command.name,
                preset_id=command.preset_id,
                task_id=command.task_id,
                state="READY",
                planned_duration_ms=duration,
                elapsed_before_run_ms=0,
                run_started_at_utc=None,
                deadline_at_utc=None,
                first_started_at_utc=None,
                ended_at_utc=None,
                deleted_at_utc=None,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            repo.add(timer)
            self._event(uow, "TIMER_CREATED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def get_timer(self, timer_id: str, *, include_deleted: bool = False) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            timer = self._repo(uow).get(timer_id)
            if timer is None or (timer.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Timer was not found.")
            return _view(timer, now)

    def update_timer(self, timer_id: str, patch: UpdateTimer) -> TimerView:
        values = patch.model_dump(exclude={"expected_version"}, exclude_unset=True)
        if not values:
            raise ValidationError("Timer update must include at least one field.")
        if any(values.get(k) is None for k in ("name", "planned_duration_ms") if k in values):
            raise ValidationError("Timer name and duration cannot be null.")
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            self._reject_expired(timer, now)
            self._guard_focus_timer(uow, timer, "update", set(values))
            config = set(values) - {"name"}
            if config and timer.state != "READY":
                raise InvalidStateTransitionError(
                    "Timer configuration may only change while ready."
                )
            final_preset = values.get("preset_id", timer.preset_id)
            final_task = values.get("task_id", timer.task_id)
            if "task_id" in values and final_task is not None and final_task != timer.task_id:
                self._active_task(uow, final_task)
            preset = (
                self._preset(uow, final_preset)
                if "preset_id" in values
                and final_preset is not None
                and final_preset != timer.preset_id
                else None
            )
            if (
                "planned_duration_ms" in values
                and "preset_id" in values
                and values["planned_duration_ms"] is not None
                and final_preset is not None
            ):
                raise ValidationError("Specify either a preset or an explicit duration.")
            if not any(getattr(timer, k) != v for k, v in values.items()):
                raise ValidationError("Timer update does not change any fields.")
            self._claim(uow, timer, patch.expected_version, now)
            for key, value in values.items():
                setattr(timer, key, value)
            if preset is not None:
                timer.planned_duration_ms = preset.duration_ms
            elif "planned_duration_ms" in values:
                timer.preset_id = None
            self._event(uow, "TIMER_UPDATED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def start_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            timer = self._timer(uow, timer_id)
            self._guard_focus_timer(uow, timer, "start")
            if timer.state != "READY":
                raise InvalidStateTransitionError("Only ready timers may start.")
            self._claim(uow, timer, expected_version, now)
            timer.state = "RUNNING"
            timer.elapsed_before_run_ms = 0
            timer.run_started_at_utc = now
            timer.deadline_at_utc = now + timedelta(milliseconds=timer.planned_duration_ms)
            timer.first_started_at_utc = now
            timer.ended_at_utc = None
            repo.add_run(
                TimerRun(
                    id=self._ids.new(),
                    timer_id=timer.id,
                    task_id=timer.task_id,
                    planned_duration_ms=timer.planned_duration_ms,
                    actual_duration_ms=0,
                    started_at_utc=now,
                    ended_at_utc=None,
                    outcome=None,
                )
            )
            self._event(uow, "TIMER_STARTED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def _close_active(
        self,
        uow: SqlAlchemyUnitOfWork,
        timer: Timer,
        now: datetime,
        outcome: str,
        *,
        ended: datetime | None = None,
        elapsed: int | None = None,
    ) -> None:
        run = self._repo(uow).get_open_run(timer.id)
        if run is None:
            raise DatabaseCorruptionError("Active timer has no open execution run.")
        elapsed = _elapsed(timer, now) if elapsed is None else elapsed
        ended = now if ended is None else ended
        run.actual_duration_ms = elapsed
        run.ended_at_utc = ended
        run.outcome = outcome
        timer.elapsed_before_run_ms = elapsed
        timer.run_started_at_utc = None
        timer.deadline_at_utc = None
        timer.ended_at_utc = ended

    def pause_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            self._reject_expired(timer, now)
            if timer.state != "RUNNING":
                raise InvalidStateTransitionError("Only running timers may pause.")
            elapsed = _elapsed(timer, now)
            self._claim(uow, timer, expected_version, now)
            timer.state = "PAUSED"
            timer.elapsed_before_run_ms = elapsed
            timer.run_started_at_utc = None
            timer.deadline_at_utc = None
            run = self._repo(uow).get_open_run(timer.id)
            if run is None:
                raise DatabaseCorruptionError("Running timer has no open execution run.")
            run.actual_duration_ms = elapsed
            self._event(uow, "TIMER_PAUSED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def resume_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            if timer.state != "PAUSED":
                raise InvalidStateTransitionError("Only paused timers may resume.")
            if self._repo(uow).get_open_run(timer.id) is None:
                raise DatabaseCorruptionError("Paused timer has no open execution run.")
            remaining = timer.planned_duration_ms - timer.elapsed_before_run_ms
            if remaining <= 0:
                raise DatabaseCorruptionError("Paused timer has no remaining duration.")
            self._claim(uow, timer, expected_version, now)
            timer.state = "RUNNING"
            timer.run_started_at_utc = now
            timer.deadline_at_utc = now + timedelta(milliseconds=remaining)
            self._event(uow, "TIMER_RESUMED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def complete_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            if timer.state not in _ACTIVE:
                raise InvalidStateTransitionError("Only active timers may complete.")
            natural = (
                timer.state == "RUNNING"
                and timer.deadline_at_utc is not None
                and now >= timer.deadline_at_utc
            )
            self._claim(uow, timer, expected_version, now)
            if natural:
                assert timer.deadline_at_utc is not None
                self._close_active(
                    uow,
                    timer,
                    now,
                    "FINISHED",
                    ended=timer.deadline_at_utc,
                    elapsed=timer.planned_duration_ms,
                )
            else:
                self._close_active(uow, timer, now, "FINISHED_EARLY")
            timer.state = "FINISHED"
            self._event(uow, "TIMER_COMPLETED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def cancel_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            self._reject_expired(timer, now)
            if timer.state not in _ACTIVE:
                raise InvalidStateTransitionError("Only active timers may cancel.")
            self._claim(uow, timer, expected_version, now)
            self._close_active(uow, timer, now, "CANCELLED")
            timer.state = "CANCELLED"
            self._event(uow, "TIMER_CANCELLED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def reset_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            self._reject_expired(timer, now)
            self._guard_focus_timer(uow, timer, "reset")
            if timer.state == "READY":
                raise InvalidStateTransitionError("Ready timers cannot be reset.")
            self._claim(uow, timer, expected_version, now)
            if timer.state in _ACTIVE:
                self._close_active(uow, timer, now, "RESET")
            timer.state = "READY"
            timer.elapsed_before_run_ms = 0
            timer.run_started_at_utc = None
            timer.deadline_at_utc = None
            timer.first_started_at_utc = None
            timer.ended_at_utc = None
            self._event(uow, "TIMER_RESET", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def delete_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id)
            self._guard_focus_timer(uow, timer, "delete")
            if timer.state in _ACTIVE:
                raise InvalidStateTransitionError("Active timers cannot be deleted.")
            self._claim(uow, timer, expected_version, now)
            timer.deleted_at_utc = now
            self._event(uow, "TIMER_DELETED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def restore_timer(self, timer_id: str, expected_version: int) -> TimerView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            timer = self._timer(uow, timer_id, deleted=True)
            if timer.state in _ACTIVE:
                raise InvalidStateTransitionError("Deleted active timers cannot be restored.")
            self._claim(uow, timer, expected_version, now, deleted=True)
            timer.deleted_at_utc = None
            self._event(uow, "TIMER_RESTORED", timer, now)
            self._commit(uow)
            return _view(timer, now)

    def reconcile_expired_timers(self) -> tuple[TimerView, ...]:
        """Complete every running timer whose deadline has passed."""
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            completed: list[Timer] = []
            for timer in self._repo(uow).expired_running(now):
                deadline = timer.deadline_at_utc
                if deadline is None:
                    raise DatabaseCorruptionError("Running timer has no deadline.")
                self._claim(uow, timer, timer.version, now)
                self._close_active(
                    uow, timer, now, "FINISHED", ended=deadline, elapsed=timer.planned_duration_ms
                )
                timer.state = "FINISHED"
                self._event(uow, "TIMER_COMPLETED", timer, now)
                completed.append(timer)
            if not completed:
                return ()
            self._commit(uow)
            return tuple(_view(timer, now) for timer in completed)

    def list_active_timers(self) -> tuple[TimerView, ...]:
        """Running and paused timers (several may be active at once), newest start first."""
        now = self._clock.now_utc()
        with self._uows() as uow:
            return tuple(_view(timer, now) for timer in self._repo(uow).active_timers())

    def list_timers(self, query: TimerQuery | None = None) -> tuple[TimerSummary, ...]:
        now = self._clock.now_utc()
        data = query or TimerQuery()
        with self._uows() as uow:
            return tuple(_view(t, now) for t in self._repo(uow).list_timers(data, now))

    def list_timer_runs(
        self,
        timer_id: str,
        query: TimerRunQuery | None = None,
        *,
        include_deleted_timer: bool = False,
    ) -> tuple[TimerRunView, ...]:
        with self._uows() as uow:
            timer = self._repo(uow).get(timer_id)
            if timer is None or (timer.deleted_at_utc is not None and not include_deleted_timer):
                raise NotFoundError("Timer was not found.")
            return tuple(
                _run_view(run)
                for run in self._repo(uow).list_runs(timer_id, query or TimerRunQuery())
            )

    # Preset workflows -------------------------------------------------
    def create_preset(self, command: CreateTimerPreset) -> TimerPresetView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            active = repo.active_presets()
            position = (
                min(command.position, len(active)) if command.position is not None else len(active)
            )
            moved = active[position:]
            self._park_preset_positions(uow, moved)
            for index, item in enumerate(moved, start=position + 1):
                item.sort_order = index
                item.version += 1
                item.updated_at_utc = now
            display_name, normalized_name = normalize_label(command.name)
            preset = TimerPreset(
                id=self._ids.new(),
                name=display_name,
                normalized_name=normalized_name,
                duration_ms=command.duration_ms,
                sort_order=position,
                is_builtin=False,
                deleted_at_utc=None,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            repo.add_preset(preset)
            self._event(uow, "TIMER_PRESET_CREATED", preset, now, preset=True)
            self._commit(uow)
            return _preset_view(preset)

    def get_preset(self, preset_id: str, *, include_deleted: bool = False) -> TimerPresetView:
        with self._uows() as uow:
            preset = self._repo(uow).get_preset(preset_id)
            if preset is None or (preset.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Timer preset was not found.")
            return _preset_view(preset)

    def update_preset(self, preset_id: str, patch: UpdateTimerPreset) -> TimerPresetView:
        values = patch.model_dump(exclude={"expected_version"}, exclude_unset=True)
        if not values:
            raise ValidationError("Preset update must include at least one field.")
        if any(v is None for v in values.values()):
            raise ValidationError("Preset name and duration cannot be null.")
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            preset = self._preset(uow, preset_id)
            if not any(getattr(preset, k) != v for k, v in values.items()):
                raise ValidationError("Preset update does not change any fields.")
            self._repo(uow).claim_preset_version(
                preset.id, patch.expected_version, now, deleted=False
            ) or (_ for _ in ()).throw(StaleVersionError("Preset changed; refresh and retry."))
            for k, v in values.items():
                setattr(preset, k, v)
            if "name" in values:
                preset.name, preset.normalized_name = normalize_label(preset.name)
            self._event(uow, "TIMER_PRESET_UPDATED", preset, now, preset=True)
            self._commit(uow)
            return _preset_view(preset)

    def _park_preset_positions(self, uow: SqlAlchemyUnitOfWork, presets: list[TimerPreset]) -> None:
        """Flush collision-free temporary positions before dense reordering."""
        for index, item in enumerate(presets):
            item.sort_order = 1_000_000 + index
        self._flush(uow)

    def move_preset(self, preset_id: str, command: MoveTimerPreset) -> TimerPresetView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            preset = self._preset(uow, preset_id)
            active = repo.active_presets()
            target = min(command.position, len(active) - 1)
            if target == preset.sort_order:
                raise ValidationError("Preset already has that position.")
            if not repo.claim_preset_version(
                preset.id, command.expected_version, now, deleted=False
            ):
                raise StaleVersionError("Preset changed; refresh and retry.")
            original_positions = {item.id: item.sort_order for item in active}
            # A flush at non-overlapping positions avoids SQLite's immediate unique checks.
            self._park_preset_positions(uow, active)
            active.remove(preset)
            active.insert(target, preset)
            for index, item in enumerate(active):
                item.sort_order = index
                if item is not preset and item.sort_order != original_positions[item.id]:
                    item.version += 1
                    item.updated_at_utc = now
            self._event(uow, "TIMER_PRESET_MOVED", preset, now, preset=True)
            self._commit(uow)
            return _preset_view(preset)

    def delete_preset(self, preset_id: str, expected_version: int) -> TimerPresetView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            preset = self._preset(uow, preset_id)
            active = repo.active_presets()
            if not repo.claim_preset_version(preset.id, expected_version, now, deleted=False):
                raise StaleVersionError("Preset changed; refresh and retry.")
            original_positions = {item.id: item.sort_order for item in active}
            deleted_position = preset.sort_order
            self._park_preset_positions(uow, active)
            active.remove(preset)
            preset.deleted_at_utc = now
            preset.sort_order = deleted_position
            for index, item in enumerate(active):
                item.sort_order = index
                if item.sort_order != original_positions[item.id]:
                    item.version += 1
                    item.updated_at_utc = now
            self._event(uow, "TIMER_PRESET_DELETED", preset, now, preset=True)
            self._commit(uow)
            return _preset_view(preset)

    def restore_preset(self, preset_id: str, expected_version: int) -> TimerPresetView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            preset = self._preset(uow, preset_id, active=False)
            if preset.deleted_at_utc is None:
                raise NotFoundError("Deleted timer preset was not found.")
            if not repo.claim_preset_version(preset.id, expected_version, now, deleted=True):
                raise StaleVersionError("Preset changed; refresh and retry.")
            active = repo.active_presets()
            original_positions = {item.id: item.sort_order for item in active}
            self._park_preset_positions(uow, active)
            target = min(preset.sort_order, len(active))
            active.insert(target, preset)
            preset.deleted_at_utc = None
            for index, item in enumerate(active):
                item.sort_order = index
            for item in active:
                if item is not preset and item.sort_order != original_positions[item.id]:
                    item.version += 1
                    item.updated_at_utc = now
            self._event(uow, "TIMER_PRESET_RESTORED", preset, now, preset=True)
            self._commit(uow)
            return _preset_view(preset)

    def list_presets(self, query: TimerPresetQuery | None = None) -> tuple[TimerPresetView, ...]:
        with self._uows() as uow:
            return tuple(
                _preset_view(p) for p in self._repo(uow).list_presets(query or TimerPresetQuery())
            )
