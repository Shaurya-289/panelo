"""Persistence mechanics for timers, presets, and execution runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import BooleanClauseList, case, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from panelo.db.models.initial import Timer, TimerPreset, TimerRun
from panelo.db.models.pomodoro import FocusSession
from panelo.schemas.timers import TimerPresetQuery, TimerQuery, TimerRunQuery


class TimerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, timer_id: str) -> Timer | None:
        return self._session.get(Timer, timer_id)

    def add(self, timer: Timer) -> None:
        self._session.add(timer)

    def add_run(self, run: TimerRun) -> None:
        self._session.add(run)

    def get_open_run(self, timer_id: str) -> TimerRun | None:
        return self._session.scalar(
            select(TimerRun).where(TimerRun.timer_id == timer_id, TimerRun.ended_at_utc.is_(None))
        )

    def claim_version(
        self, timer_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Timer)
                .where(
                    Timer.id == timer_id,
                    Timer.version == expected_version,
                    Timer.deleted_at_utc.is_not(None)
                    if deleted
                    else Timer.deleted_at_utc.is_(None),
                )
                .values(version=Timer.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1

    def active_timers(self) -> list[Timer]:
        """Running and paused timers, the most recently started first."""
        return list(
            self._session.scalars(
                select(Timer)
                .where(Timer.deleted_at_utc.is_(None), Timer.state.in_(("RUNNING", "PAUSED")))
                .order_by(Timer.first_started_at_utc.desc(), Timer.id)
            )
        )

    def expired_running(self, now_utc: datetime) -> list[Timer]:
        return list(
            self._session.scalars(
                select(Timer)
                .where(
                    Timer.deleted_at_utc.is_(None),
                    Timer.state == "RUNNING",
                    Timer.deadline_at_utc <= now_utc,
                )
                .order_by(Timer.deadline_at_utc, Timer.id)
            )
        )

    def list_timers(self, data: TimerQuery, now_utc: datetime) -> list[Timer]:
        query = select(Timer)
        if not data.include_deleted:
            query = query.where(Timer.deleted_at_utc.is_(None))
        if data.states:
            query = query.where(Timer.state.in_(data.states))
        if data.effective_states:
            clauses: list[BooleanClauseList] = []
            for state in data.effective_states:
                if state == "FINISHED":
                    clauses.append(
                        (Timer.state == "FINISHED")
                        | ((Timer.state == "RUNNING") & (Timer.deadline_at_utc <= now_utc))
                    )
                elif state == "RUNNING":
                    clauses.append((Timer.state == "RUNNING") & (Timer.deadline_at_utc > now_utc))
                else:
                    clauses.append(cast(BooleanClauseList, Timer.state == state))
            query = query.where(or_(*clauses))
        if data.task_link == "NONE":
            query = query.where(Timer.task_id.is_(None))
        elif data.task_link == "TASK":
            query = query.where(
                Timer.task_id.is_not(None),
                *([Timer.task_id == data.task_id] if data.task_id is not None else []),
            )
        if data.preset_link == "NONE":
            query = query.where(Timer.preset_id.is_(None))
        elif data.preset_link == "PRESET":
            query = query.where(
                Timer.preset_id.is_not(None),
                *([Timer.preset_id == data.preset_id] if data.preset_id is not None else []),
            )
        if data.ended_from_utc is not None:
            query = query.where(Timer.ended_at_utc >= data.ended_from_utc)
        if data.ended_before_utc is not None:
            query = query.where(Timer.ended_at_utc < data.ended_before_utc)
        if data.exclude_focus:
            query = query.where(
                ~select(FocusSession.id).where(FocusSession.timer_id == Timer.id).exists()
            )
        return list(
            self._session.scalars(
                query.order_by(*self._timer_order(data.sort, now_utc))
                .offset(data.offset)
                .limit(data.limit)
            )
        )

    def list_runs(self, timer_id: str, data: TimerRunQuery) -> list[TimerRun]:
        query = select(TimerRun).where(
            TimerRun.timer_id == timer_id, TimerRun.ended_at_utc.is_not(None)
        )
        if data.outcomes:
            query = query.where(TimerRun.outcome.in_(data.outcomes))
        if data.task_id is not None:
            query = query.where(TimerRun.task_id == data.task_id)
        if data.started_from_utc is not None:
            query = query.where(TimerRun.started_at_utc >= data.started_from_utc)
        if data.started_before_utc is not None:
            query = query.where(TimerRun.started_at_utc < data.started_before_utc)
        if data.ended_from_utc is not None:
            query = query.where(TimerRun.ended_at_utc >= data.ended_from_utc)
        if data.ended_before_utc is not None:
            query = query.where(TimerRun.ended_at_utc < data.ended_before_utc)
        return list(
            self._session.scalars(
                query.order_by(TimerRun.started_at_utc.desc(), TimerRun.id)
                .offset(data.offset)
                .limit(data.limit)
            )
        )

    def get_preset(self, preset_id: str) -> TimerPreset | None:
        return self._session.get(TimerPreset, preset_id)

    def add_preset(self, preset: TimerPreset) -> None:
        self._session.add(preset)

    def claim_preset_version(
        self, preset_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(TimerPreset)
                .where(
                    TimerPreset.id == preset_id,
                    TimerPreset.version == expected_version,
                    TimerPreset.deleted_at_utc.is_not(None)
                    if deleted
                    else TimerPreset.deleted_at_utc.is_(None),
                )
                .values(version=TimerPreset.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1

    def active_presets(self) -> list[TimerPreset]:
        return list(
            self._session.scalars(
                select(TimerPreset)
                .where(TimerPreset.deleted_at_utc.is_(None))
                .order_by(TimerPreset.sort_order, TimerPreset.id)
            )
        )

    def list_presets(self, data: TimerPresetQuery) -> list[TimerPreset]:
        query = select(TimerPreset)
        if not data.include_deleted:
            query = query.where(TimerPreset.deleted_at_utc.is_(None))
        order = {
            "position": (TimerPreset.sort_order, TimerPreset.id),
            "name": (TimerPreset.name, TimerPreset.id),
            "duration_shortest": (TimerPreset.duration_ms, TimerPreset.id),
            "duration_longest": (TimerPreset.duration_ms.desc(), TimerPreset.id),
        }[data.sort]
        return list(
            self._session.scalars(query.order_by(*order).offset(data.offset).limit(data.limit))
        )

    @staticmethod
    def _timer_order(sort: str, now_utc: datetime) -> tuple[Any, ...]:
        effective_state_rank = case(
            (Timer.state == "READY", 0),
            ((Timer.state == "RUNNING") & (Timer.deadline_at_utc > now_utc), 1),
            (Timer.state == "PAUSED", 2),
            (
                (Timer.state == "FINISHED")
                | ((Timer.state == "RUNNING") & (Timer.deadline_at_utc <= now_utc)),
                3,
            ),
            else_=4,
        )
        return {
            "updated_newest": (Timer.updated_at_utc.desc(), Timer.id),
            "updated_oldest": (Timer.updated_at_utc, Timer.id),
            "created_newest": (Timer.created_at_utc.desc(), Timer.id),
            "created_oldest": (Timer.created_at_utc, Timer.id),
            "name": (Timer.name, Timer.id),
            "state": (effective_state_rank, Timer.id),
            "duration_shortest": (Timer.planned_duration_ms, Timer.id),
            "duration_longest": (Timer.planned_duration_ms.desc(), Timer.id),
            "ended_newest": (Timer.ended_at_utc.desc().nullslast(), Timer.id),
        }[sort]
