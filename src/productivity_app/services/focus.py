"""Focus/Pomodoro orchestration over the existing countdown-timer engine."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ConflictError,
    DatabaseCorruptionError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
)
from productivity_app.core.ids import IdGenerator, Uuid4Generator
from productivity_app.db.models.initial import Task, Timer
from productivity_app.db.models.pomodoro import FocusSequence, FocusSession
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.repositories.focus import FocusRepository
from productivity_app.schemas.focus import (
    AdvanceFocusSequence,
    CancelFocusSequence,
    CreateFocusSequence,
    CreateFocusSession,
    DailyFocusTotal,
    FocusPhaseKind,
    FocusSequenceQuery,
    FocusSequenceState,
    FocusSequenceSummary,
    FocusSequenceView,
    FocusSessionQuery,
    FocusSessionSummary,
    FocusSessionView,
    FocusStatsQuery,
    FocusStatsView,
)
from productivity_app.services.timers import _view

_PHASES = ("FOCUS", "SHORT_BREAK", "FOCUS", "SHORT_BREAK", "FOCUS", "SHORT_BREAK", "FOCUS", "LONG_BREAK")
_NAMES = {"FOCUS": "Focus", "SHORT_BREAK": "Short Break", "LONG_BREAK": "Long Break"}


def _translate_create_sequence_integrity_error(exc: IntegrityError) -> None:
    message = str(exc.orig).lower() if exc.orig is not None else ""
    if message == "unique constraint failed: index 'ux_focus_sequences_one_active'":
        raise ConflictError("A focus sequence is already active.") from exc
    raise exc


class FocusService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    @staticmethod
    def _repo(uow: SqlAlchemyUnitOfWork) -> FocusRepository:
        assert uow.focus is not None
        return uow.focus

    @staticmethod
    def _active_task(uow: SqlAlchemyUnitOfWork, task_id: str) -> None:
        assert uow.tasks is not None
        task: Task | None = uow.tasks.get(task_id)
        if task is None or task.deleted_at_utc is not None:
            raise NotFoundError("Task was not found.")

    @staticmethod
    def _timer(uow: SqlAlchemyUnitOfWork, timer_id: str) -> Timer:
        assert uow.timers is not None
        timer = uow.timers.get(timer_id)
        if timer is None:
            raise DatabaseCorruptionError("Focus session has no timer.")
        return timer

    def _timer_event(self, uow: SqlAlchemyUnitOfWork, timer: Timer, now: datetime) -> None:
        uow.collect_event(DomainEvent("TIMER_CREATED", timer.id, now, f"timer:timer_created:{timer.id}:{timer.version}"))

    def _sequence_event(self, uow: SqlAlchemyUnitOfWork, kind: str, sequence: FocusSequence, now: datetime) -> None:
        uow.collect_event(DomainEvent(kind, sequence.id, now, f"focus_sequence:{kind.lower()}:{sequence.id}:{sequence.version}"))

    def _ready_timer(self, uow: SqlAlchemyUnitOfWork, *, name: str, duration: int, task_id: str | None, now: datetime) -> Timer:
        timer = Timer(
            id=self._ids.new(), name=name, preset_id=None, task_id=task_id, state="READY",
            planned_duration_ms=duration, elapsed_before_run_ms=0, run_started_at_utc=None,
            deadline_at_utc=None, first_started_at_utc=None, ended_at_utc=None,
            deleted_at_utc=None, created_at_utc=now, updated_at_utc=now, version=1,
        )
        assert uow.timers is not None
        uow.timers.add(timer)
        self._timer_event(uow, timer, now)
        return timer

    @staticmethod
    def _duration(sequence: FocusSequence, kind: str) -> int:
        return {"FOCUS": sequence.work_duration_ms, "SHORT_BREAK": sequence.short_break_duration_ms, "LONG_BREAK": sequence.long_break_duration_ms}[kind]

    def _session_view(self, uow: SqlAlchemyUnitOfWork, session: FocusSession, now: datetime) -> FocusSessionView:
        timer = self._timer(uow, session.timer_id)
        return FocusSessionView(
            id=session.id,
            sequence_id=session.sequence_id,
            phase_kind=cast(FocusPhaseKind, session.phase_kind),
            phase_index=session.phase_index,
            created_at_utc=session.created_at_utc,
            timer=_view(timer, now),
        )

    def _sequence_view(self, uow: SqlAlchemyUnitOfWork, sequence: FocusSequence, now: datetime) -> FocusSequenceView:
        session = self._repo(uow).get_sequence_session(sequence.id, sequence.current_phase_index)
        if session is None:
            raise DatabaseCorruptionError("Focus sequence has no current session.")
        return FocusSequenceView(
            id=sequence.id, state=cast(FocusSequenceState, sequence.state), task_id=sequence.task_id,
            current_phase_index=sequence.current_phase_index, work_duration_ms=sequence.work_duration_ms,
            short_break_duration_ms=sequence.short_break_duration_ms, long_break_duration_ms=sequence.long_break_duration_ms,
            completed_at_utc=sequence.completed_at_utc, cancelled_at_utc=sequence.cancelled_at_utc,
            created_at_utc=sequence.created_at_utc, updated_at_utc=sequence.updated_at_utc,
            version=sequence.version, current_session=self._session_view(uow, session, now),
        )

    def create_session(self, command: CreateFocusSession) -> FocusSessionView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            if command.task_id is not None:
                self._active_task(uow, command.task_id)
            assert uow.settings is not None
            settings = uow.settings.get()
            if settings is None:
                raise DatabaseCorruptionError("Settings singleton is missing.")
            duration = {"FOCUS": settings.pomodoro_work_duration_ms, "SHORT_BREAK": settings.short_break_duration_ms, "LONG_BREAK": settings.long_break_duration_ms}[command.phase_kind]
            timer = self._ready_timer(uow, name=_NAMES[command.phase_kind], duration=duration, task_id=command.task_id, now=now)
            session = FocusSession(id=self._ids.new(), sequence_id=None, timer_id=timer.id, phase_kind=command.phase_kind, phase_index=None, created_at_utc=now)
            self._repo(uow).add_session(session)
            uow.commit()
            return self._session_view(uow, session, now)

    def create_sequence(self, command: CreateFocusSequence) -> FocusSequenceView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            if repo.get_active_sequence() is not None:
                raise ConflictError("A focus sequence is already active.")
            if command.task_id is not None:
                self._active_task(uow, command.task_id)
            assert uow.settings is not None
            settings = uow.settings.get()
            if settings is None:
                raise DatabaseCorruptionError("Settings singleton is missing.")
            sequence = FocusSequence(id=self._ids.new(), state="ACTIVE", task_id=command.task_id, current_phase_index=0, work_duration_ms=settings.pomodoro_work_duration_ms, short_break_duration_ms=settings.short_break_duration_ms, long_break_duration_ms=settings.long_break_duration_ms, completed_at_utc=None, cancelled_at_utc=None, created_at_utc=now, updated_at_utc=now, version=1)
            repo.add_sequence(sequence)
            timer = self._ready_timer(uow, name="Focus", duration=sequence.work_duration_ms, task_id=command.task_id, now=now)
            repo.add_session(FocusSession(id=self._ids.new(), sequence_id=sequence.id, timer_id=timer.id, phase_kind="FOCUS", phase_index=0, created_at_utc=now))
            self._sequence_event(uow, "FOCUS_SEQUENCE_STARTED", sequence, now)
            try:
                uow.commit()
            except IntegrityError as exc:
                _translate_create_sequence_integrity_error(exc)
            return self._sequence_view(uow, sequence, now)

    def get_session(self, session_id: str) -> FocusSessionView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            session = self._repo(uow).get_session(session_id)
            if session is None:
                raise NotFoundError("Focus session was not found.")
            return self._session_view(uow, session, now)

    def get_sequence(self, sequence_id: str) -> FocusSequenceView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            sequence = self._repo(uow).get_sequence(sequence_id)
            if sequence is None:
                raise NotFoundError("Focus sequence was not found.")
            return self._sequence_view(uow, sequence, now)

    def list_sessions(self, query: FocusSessionQuery | None = None) -> tuple[FocusSessionSummary, ...]:
        now = self._clock.now_utc()
        with self._uows() as uow:
            return tuple(self._session_view(uow, item, now) for item in self._repo(uow).list_sessions(query or FocusSessionQuery()))

    def list_sequences(self, query: FocusSequenceQuery | None = None) -> tuple[FocusSequenceSummary, ...]:
        with self._uows() as uow:
            return tuple(FocusSequenceSummary(
                id=item.id, state=cast(FocusSequenceState, item.state), task_id=item.task_id,
                current_phase_index=item.current_phase_index, work_duration_ms=item.work_duration_ms,
                short_break_duration_ms=item.short_break_duration_ms, long_break_duration_ms=item.long_break_duration_ms,
                completed_at_utc=item.completed_at_utc, cancelled_at_utc=item.cancelled_at_utc,
                created_at_utc=item.created_at_utc, updated_at_utc=item.updated_at_utc, version=item.version,
            ) for item in self._repo(uow).list_sequences(query or FocusSequenceQuery()))

    def advance_sequence(self, sequence_id: str, command: AdvanceFocusSequence) -> FocusSequenceView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            sequence = repo.get_sequence(sequence_id)
            if sequence is None:
                raise NotFoundError("Focus sequence was not found.")
            if sequence.state != "ACTIVE":
                raise InvalidStateTransitionError("Only active focus sequences may advance.")
            if sequence.version != command.expected_version:
                raise StaleVersionError("Focus sequence changed; refresh and retry.")
            session = repo.get_sequence_session(sequence.id, sequence.current_phase_index)
            if session is None:
                raise DatabaseCorruptionError("Focus sequence has no current session.")
            timer = self._timer(uow, session.timer_id)
            if timer.state == "RUNNING" and timer.deadline_at_utc is not None and now >= timer.deadline_at_utc:
                raise InvalidStateTransitionError("Reconcile expired timer first.")
            if timer.state != "FINISHED":
                raise InvalidStateTransitionError("Current focus phase must be finished before advancement.")
            run = repo.latest_closed_run(timer.id)
            if run is None or run.outcome not in {"FINISHED", "FINISHED_EARLY"}:
                raise InvalidStateTransitionError("Current focus phase does not have a completed execution run.")
            if not repo.claim_sequence_version(sequence.id, command.expected_version, now):
                raise StaleVersionError("Focus sequence changed; refresh and retry.")
            if sequence.current_phase_index == 7:
                sequence.state = "COMPLETED"
                sequence.completed_at_utc = timer.ended_at_utc
                self._sequence_event(uow, "FOCUS_SEQUENCE_COMPLETED", sequence, now)
            else:
                sequence.current_phase_index += 1
                kind = _PHASES[sequence.current_phase_index]
                linked_task = sequence.task_id if kind == "FOCUS" else None
                timer = self._ready_timer(uow, name=_NAMES[kind], duration=self._duration(sequence, kind), task_id=linked_task, now=now)
                repo.add_session(FocusSession(id=self._ids.new(), sequence_id=sequence.id, timer_id=timer.id, phase_kind=kind, phase_index=sequence.current_phase_index, created_at_utc=now))
                self._sequence_event(uow, "FOCUS_PHASE_ADVANCED", sequence, now)
            uow.commit()
            return self._sequence_view(uow, sequence, now)

    def cancel_sequence(self, sequence_id: str, command: CancelFocusSequence) -> FocusSequenceView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            repo = self._repo(uow)
            sequence = repo.get_sequence(sequence_id)
            if sequence is None:
                raise NotFoundError("Focus sequence was not found.")
            if sequence.state != "ACTIVE":
                raise InvalidStateTransitionError("Only active focus sequences may be cancelled.")
            if sequence.version != command.expected_version:
                raise StaleVersionError("Focus sequence changed; refresh and retry.")
            session = repo.get_sequence_session(sequence.id, sequence.current_phase_index)
            if session is None:
                raise DatabaseCorruptionError("Focus sequence has no current session.")
            timer = self._timer(uow, session.timer_id)
            if timer.state in {"RUNNING", "PAUSED"}:
                raise InvalidStateTransitionError("Cancel or reset the current timer before cancelling the sequence.")
            if not repo.claim_sequence_version(sequence.id, command.expected_version, now):
                raise StaleVersionError("Focus sequence changed; refresh and retry.")
            sequence.state = "CANCELLED"
            sequence.cancelled_at_utc = now
            self._sequence_event(uow, "FOCUS_SEQUENCE_CANCELLED", sequence, now)
            uow.commit()
            return self._sequence_view(uow, sequence, now)

    def get_stats(self, query: FocusStatsQuery | None = None) -> FocusStatsView:
        with self._uows() as uow:
            completed, duration = self._repo(uow).focus_stats(query or FocusStatsQuery())
            return FocusStatsView(completed_focus_sessions=completed, focused_duration_ms=duration)

    @staticmethod
    def _zone(tz: str | tzinfo) -> tzinfo:
        return ZoneInfo(tz) if isinstance(tz, str) else tz

    def daily_totals(self, days: int, tz: str | tzinfo) -> tuple[DailyFocusTotal, ...]:
        """Completed focus sessions and focused minutes for each of the last ``days`` local days.

        Oldest day first, ending with today; days without focus are zero rows.  A session
        counts on the local day it started and only when it ran its full length, while the
        minutes also include sessions that were finished early (as ``get_stats`` does).
        """
        if days < 1:
            raise ValueError("days must be at least 1.")
        zone = self._zone(tz)
        today = self._clock.now_utc().astimezone(zone).date()
        first = today - timedelta(days=days - 1)
        since = datetime.combine(first, time.min, tzinfo=zone).astimezone(UTC)
        sessions: dict[date, int] = {}
        milliseconds: dict[date, int] = {}
        with self._uows() as uow:
            for started, actual_ms, outcome in self._repo(uow).focus_runs_since(since):
                day = started.astimezone(zone).date()
                milliseconds[day] = milliseconds.get(day, 0) + actual_ms
                if outcome == "FINISHED":
                    sessions[day] = sessions.get(day, 0) + 1
        return tuple(
            DailyFocusTotal(
                day,
                sessions.get(day, 0),
                round(milliseconds.get(day, 0) / 60_000),
            )
            for day in (first + timedelta(days=offset) for offset in range(days))
        )

    def streak(self, tz: str | tzinfo) -> int:
        """Consecutive local days with at least one completed focus session.

        The run ends today, or yesterday while today has no session yet (a streak only
        breaks once a whole day passes without one).
        """
        zone = self._zone(tz)
        today = self._clock.now_utc().astimezone(zone).date()
        expected = today
        counted: date | None = None
        count = 0
        with self._uows() as uow:
            for started in self._repo(uow).completed_focus_starts():
                day = started.astimezone(zone).date()
                if day > today or day == counted:
                    continue  # a future-dated run, or another session on a day already counted
                if counted is None and day == today - timedelta(days=1):
                    expected = day  # nothing yet today: the run may still end yesterday
                if day != expected:
                    break
                count, counted, expected = count + 1, day, day - timedelta(days=1)
        return count
