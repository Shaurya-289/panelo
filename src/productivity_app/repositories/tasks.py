"""Persistence queries for tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import Task
from productivity_app.schemas.tasks import TaskQuery

_ACTIVE = ("TODO", "IN_PROGRESS")
_PRIORITY_RANK = case(
    (Task.priority == "URGENT", 0),
    (Task.priority == "HIGH", 1),
    (Task.priority == "NORMAL", 2),
    else_=3,
)
_STATUS_RANK = case(
    (Task.status == "TODO", 0),
    (Task.status == "IN_PROGRESS", 1),
    (Task.status == "COMPLETED", 2),
    else_=3,
)


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: str) -> Task | None:
        """Return the row regardless of soft-deletion state.

        The service owns visibility policy because restore workflows require
        deleted rows while ordinary task methods reject them.
        """
        return self._session.get(Task, task_id)

    def add(self, task: Task) -> None:
        self._session.add(task)

    def list_deleted(self, *, deleted_through_utc: datetime | None = None) -> list[Task]:
        """Soft-deleted tasks, newest deletion first (optionally only those deleted by an instant)."""
        query = select(Task).where(Task.deleted_at_utc.is_not(None))
        if deleted_through_utc is not None:
            query = query.where(Task.deleted_at_utc <= deleted_through_utc)
        return list(self._session.scalars(query.order_by(Task.deleted_at_utc.desc(), Task.id)))

    def delete(self, task_id: str) -> None:
        """Remove the row for good (the caller clears reminder links first)."""
        self._session.execute(delete(Task).where(Task.id == task_id))

    def claim_version(
        self, task_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        """Atomically claim one expected version; ``True`` means one row changed."""
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.version == expected_version,
                    Task.deleted_at_utc.is_not(None) if deleted else Task.deleted_at_utc.is_(None),
                )
                .values(version=Task.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1

    def list_tasks(self, data: TaskQuery) -> list[Task]:
        query = select(Task)
        if not data.include_deleted:
            query = query.where(Task.deleted_at_utc.is_(None))
        if data.statuses:
            query = query.where(Task.status.in_(data.statuses))
        if data.priorities:
            query = query.where(Task.priority.in_(data.priorities))
        for column, lower, upper in (
            (Task.start_at_utc, data.start_from_utc, data.start_before_utc),
            (Task.due_at_utc, data.due_from_utc, data.due_before_utc),
            (Task.completed_at_utc, data.completed_from_utc, data.completed_before_utc),
        ):
            if lower is not None:
                query = query.where(column >= lower)
            if upper is not None:
                query = query.where(column < upper)
        return list(
            self._session.scalars(
                query.order_by(*self._general_ordering(data.sort)).offset(data.offset).limit(data.limit)
            )
        )

    def search_like(
        self, literal_query: str, escaped_query: str, *, include_terminal: bool, limit: int
    ) -> list[Task]:
        pattern = f"%{escaped_query}%"
        rank = case(
            (Task.title == literal_query, 0),
            (Task.title.like(f"{escaped_query}%", escape="\\"), 1),
            else_=2,
        )
        query = select(Task).where(
            Task.deleted_at_utc.is_(None),
            Task.title.like(pattern, escape="\\") | Task.description.like(pattern, escape="\\"),
        )
        if not include_terminal:
            query = query.where(Task.status.in_(_ACTIVE))
        return list(self._session.scalars(query.order_by(rank, Task.updated_at_utc.desc(), Task.id).limit(limit)))

    def list_today(
        self, window_start_utc: datetime, window_end_utc: datetime, *, limit: int, offset: int
    ) -> list[Task]:
        return self._list_window(window_start_utc, window_end_utc, limit=limit, offset=offset)

    def list_next_seven_days(
        self, window_start_utc: datetime, window_end_utc: datetime, *, limit: int, offset: int
    ) -> list[Task]:
        return self._list_window(window_start_utc, window_end_utc, limit=limit, offset=offset)

    def list_upcoming(self, now_utc: datetime, *, limit: int, offset: int) -> list[Task]:
        next_actionable = case(
            (Task.start_at_utc.is_not(None) & (Task.start_at_utc > now_utc), Task.start_at_utc),
            (Task.due_at_utc.is_not(None) & (Task.due_at_utc > now_utc), Task.due_at_utc),
            else_=None,
        )
        query = select(Task).where(
            Task.deleted_at_utc.is_(None), Task.status.in_(_ACTIVE), next_actionable.is_not(None)
        )
        return list(
            self._session.scalars(
                query.order_by(next_actionable, Task.due_at_utc.is_(None), Task.due_at_utc, Task.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def list_overdue(self, now_utc: datetime, *, limit: int, offset: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(
                    Task.deleted_at_utc.is_(None),
                    Task.status.in_(_ACTIVE),
                    Task.due_at_utc.is_not(None),
                    Task.due_at_utc < now_utc,
                )
                .order_by(Task.due_at_utc, _PRIORITY_RANK, Task.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def list_completed(self, *, limit: int, offset: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(Task.deleted_at_utc.is_(None), Task.status == "COMPLETED")
                .order_by(Task.completed_at_utc.desc(), Task.updated_at_utc.desc(), Task.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def list_important(self, *, limit: int, offset: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(
                    Task.deleted_at_utc.is_(None),
                    Task.status.in_(_ACTIVE),
                    Task.priority.in_(("HIGH", "URGENT")),
                )
                .order_by(_PRIORITY_RANK, Task.due_at_utc.is_(None), Task.due_at_utc, Task.updated_at_utc.desc(), Task.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def _list_window(
        self, start: datetime, end: datetime, *, limit: int, offset: int
    ) -> list[Task]:
        intersects = (
            (Task.start_at_utc.is_(None) & (Task.due_at_utc >= start) & (Task.due_at_utc < end))
            | (Task.due_at_utc.is_(None) & (Task.start_at_utc >= start) & (Task.start_at_utc < end))
            | (
                Task.start_at_utc.is_not(None)
                & Task.due_at_utc.is_not(None)
                & (Task.start_at_utc < end)
                & (Task.due_at_utc >= start)
            )
        )
        return list(
            self._session.scalars(
                select(Task)
                .where(Task.deleted_at_utc.is_(None), Task.status.in_(_ACTIVE), intersects)
                .order_by(Task.due_at_utc.is_(None), Task.due_at_utc, Task.start_at_utc.is_(None), Task.start_at_utc, _PRIORITY_RANK, Task.id)
                .offset(offset)
                .limit(limit)
            )
        )

    @staticmethod
    def _general_ordering(sort: str) -> tuple[Any, ...]:
        orderings: dict[str, tuple[Any, ...]] = {
            "due_soonest": (Task.due_at_utc.is_(None), Task.due_at_utc, Task.id),
            "due_latest": (Task.due_at_utc.is_(None), Task.due_at_utc.desc(), Task.id),
            "priority": (_PRIORITY_RANK, Task.due_at_utc.is_(None), Task.due_at_utc, Task.id),
            "created_newest": (Task.created_at_utc.desc(), Task.id),
            "created_oldest": (Task.created_at_utc, Task.id),
            "updated_newest": (Task.updated_at_utc.desc(), Task.id),
            "updated_oldest": (Task.updated_at_utc, Task.id),
            "title": (Task.title, Task.id),
            "status": (_STATUS_RANK, Task.due_at_utc.is_(None), Task.due_at_utc, Task.id),
        }
        return orderings[sort]
