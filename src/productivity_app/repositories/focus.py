"""Persistence operations for Focus/Pomodoro metadata."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import TimerRun
from productivity_app.db.models.pomodoro import FocusSequence, FocusSession
from productivity_app.schemas.focus import FocusSequenceQuery, FocusSessionQuery, FocusStatsQuery


class FocusRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_sequence(self, sequence_id: str) -> FocusSequence | None:
        return self._session.get(FocusSequence, sequence_id)

    def get_active_sequence(self) -> FocusSequence | None:
        return self._session.scalar(select(FocusSequence).where(FocusSequence.state == "ACTIVE"))

    def add_sequence(self, sequence: FocusSequence) -> None:
        self._session.add(sequence)

    def claim_sequence_version(self, sequence_id: str, expected_version: int, now_utc: datetime) -> bool:
        result = cast(CursorResult[Any], self._session.execute(
            update(FocusSequence).where(
                FocusSequence.id == sequence_id,
                FocusSequence.state == "ACTIVE",
                FocusSequence.version == expected_version,
            ).values(version=FocusSequence.version + 1, updated_at_utc=now_utc)
        ))
        return result.rowcount == 1

    def add_session(self, focus_session: FocusSession) -> None:
        self._session.add(focus_session)

    def get_session(self, session_id: str) -> FocusSession | None:
        return self._session.get(FocusSession, session_id)

    def get_session_by_timer(self, timer_id: str) -> FocusSession | None:
        return self._session.scalar(select(FocusSession).where(FocusSession.timer_id == timer_id))

    def get_sequence_session(self, sequence_id: str, phase_index: int) -> FocusSession | None:
        return self._session.scalar(select(FocusSession).where(
            FocusSession.sequence_id == sequence_id, FocusSession.phase_index == phase_index
        ))

    def list_sequences(self, query: FocusSequenceQuery) -> list[FocusSequence]:
        statement = select(FocusSequence)
        if query.states:
            statement = statement.where(FocusSequence.state.in_(query.states))
        return list(self._session.scalars(
            statement.order_by(FocusSequence.updated_at_utc.desc(), FocusSequence.id)
            .offset(query.offset).limit(query.limit)
        ))

    def list_sessions(self, query: FocusSessionQuery) -> list[FocusSession]:
        statement = select(FocusSession)
        if query.phase_kinds:
            statement = statement.where(FocusSession.phase_kind.in_(query.phase_kinds))
        if query.sequence_id is not None:
            statement = statement.where(FocusSession.sequence_id == query.sequence_id)
        return list(self._session.scalars(
            statement.order_by(FocusSession.created_at_utc.desc(), FocusSession.id)
            .offset(query.offset).limit(query.limit)
        ))

    def latest_closed_run(self, timer_id: str) -> TimerRun | None:
        return self._session.scalar(
            select(TimerRun).where(TimerRun.timer_id == timer_id, TimerRun.ended_at_utc.is_not(None))
            .order_by(TimerRun.ended_at_utc.desc(), TimerRun.id).limit(1)
        )

    def timer_membership(self, timer_id: str) -> tuple[FocusSession, FocusSequence | None] | None:
        session = self.get_session_by_timer(timer_id)
        if session is None:
            return None
        return session, self.get_sequence(session.sequence_id) if session.sequence_id else None

    def focus_stats(self, query: FocusStatsQuery) -> tuple[int, int]:
        statement = (
            select(
                func.coalesce(func.sum(TimerRun.actual_duration_ms), 0),
                func.coalesce(func.sum(TimerRun.outcome == "FINISHED"), 0),
            )
            .join(FocusSession, FocusSession.timer_id == TimerRun.timer_id)
            .where(FocusSession.phase_kind == "FOCUS", TimerRun.outcome.in_(("FINISHED", "FINISHED_EARLY")))
        )
        if query.task_id is not None:
            statement = statement.where(TimerRun.task_id == query.task_id)
        if query.started_from_utc is not None:
            statement = statement.where(TimerRun.started_at_utc >= query.started_from_utc)
        if query.started_before_utc is not None:
            statement = statement.where(TimerRun.started_at_utc < query.started_before_utc)
        duration, completed = self._session.execute(statement).one()
        return int(completed or 0), int(duration or 0)

    def focus_runs_since(self, started_from_utc: datetime) -> list[tuple[datetime, int, str]]:
        """``(started, actual milliseconds, outcome)`` of finished focus runs from an instant on."""
        rows = self._session.execute(
            select(TimerRun.started_at_utc, TimerRun.actual_duration_ms, TimerRun.outcome)
            .join(FocusSession, FocusSession.timer_id == TimerRun.timer_id)
            .where(
                FocusSession.phase_kind == "FOCUS",
                TimerRun.outcome.in_(("FINISHED", "FINISHED_EARLY")),
                TimerRun.started_at_utc >= started_from_utc,
            )
        ).all()
        return [(started, int(actual), str(outcome)) for started, actual, outcome in rows]

    def completed_focus_starts(self) -> Iterator[datetime]:
        """Start times of the focus sessions that ran their full length, newest first."""
        return iter(
            self._session.scalars(
                select(TimerRun.started_at_utc)
                .join(FocusSession, FocusSession.timer_id == TimerRun.timer_id)
                .where(FocusSession.phase_kind == "FOCUS", TimerRun.outcome == "FINISHED")
                .order_by(TimerRun.started_at_utc.desc())
            )
        )
