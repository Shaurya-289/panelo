"""Persistence queries for reminders and their durable audit history."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import Reminder, ReminderEvent
from productivity_app.schemas.reminders import ReminderQuery

_ACTIVE = ("PENDING", "TRIGGERED", "OVERDUE")
_STATUS_RANK = case(
    (Reminder.status == "PENDING", 0),
    (Reminder.status == "TRIGGERED", 1),
    (Reminder.status == "OVERDUE", 2),
    (Reminder.status == "DISMISSED", 3),
    (Reminder.status == "COMPLETED", 4),
    else_=5,
)
_TERMINAL_AT = case(
    (Reminder.status == "DISMISSED", Reminder.dismissed_at_utc),
    (Reminder.status == "COMPLETED", Reminder.completed_at_utc),
    (Reminder.status == "CANCELLED", Reminder.cancelled_at_utc),
    else_=None,
)


class ReminderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, reminder_id: str) -> Reminder | None:
        """Return the row regardless of soft-deletion state."""
        return self._session.get(Reminder, reminder_id)

    def add(self, reminder: Reminder) -> None:
        self._session.add(reminder)

    def add_event(self, event: ReminderEvent) -> None:
        self._session.add(event)

    def claim_version(
        self, reminder_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        """Atomically claim one expected version; ``True`` means one row changed."""
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Reminder)
                .where(
                    Reminder.id == reminder_id,
                    Reminder.version == expected_version,
                    Reminder.deleted_at_utc.is_not(None)
                    if deleted
                    else Reminder.deleted_at_utc.is_(None),
                )
                .values(version=Reminder.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1

    def list_reminders(self, data: ReminderQuery, now_utc: datetime) -> list[Reminder]:
        query = select(Reminder)
        if not data.include_deleted:
            query = query.where(Reminder.deleted_at_utc.is_(None))
        if data.statuses:
            query = query.where(Reminder.status.in_(data.statuses))
        if data.timing_states:
            timing_predicates = []
            if "UPCOMING" in data.timing_states:
                timing_predicates.append(Reminder.due_at_utc > now_utc)
            if "DUE" in data.timing_states:
                timing_predicates.append(Reminder.due_at_utc == now_utc)
            if "OVERDUE" in data.timing_states:
                timing_predicates.append(Reminder.due_at_utc < now_utc)
            query = query.where(Reminder.status.in_(_ACTIVE), or_(*timing_predicates))
        if data.link_type == "NONE":
            query = query.where(Reminder.task_id.is_(None), Reminder.note_id.is_(None))
        elif data.link_type == "TASK":
            query = query.where(Reminder.task_id.is_not(None))
            if data.linked_id is not None:
                query = query.where(Reminder.task_id == data.linked_id)
        elif data.link_type == "NOTE":
            query = query.where(Reminder.note_id.is_not(None))
            if data.linked_id is not None:
                query = query.where(Reminder.note_id == data.linked_id)
        if data.due_from_utc is not None:
            query = query.where(Reminder.due_at_utc >= data.due_from_utc)
        if data.due_before_utc is not None:
            query = query.where(Reminder.due_at_utc < data.due_before_utc)
        return list(
            self._session.scalars(
                query.order_by(*self._general_ordering(data.sort)).offset(data.offset).limit(data.limit)
            )
        )

    def list_upcoming(self, now_utc: datetime, *, limit: int, offset: int) -> list[Reminder]:
        return self._timing_list(Reminder.due_at_utc > now_utc, limit=limit, offset=offset)

    def list_due(self, now_utc: datetime, *, limit: int, offset: int) -> list[Reminder]:
        return self._timing_list(Reminder.due_at_utc <= now_utc, limit=limit, offset=offset)

    def list_overdue(self, now_utc: datetime, *, limit: int, offset: int) -> list[Reminder]:
        return self._timing_list(Reminder.due_at_utc < now_utc, limit=limit, offset=offset)

    def list_history(self, *, limit: int, offset: int) -> list[Reminder]:
        return list(
            self._session.scalars(
                select(Reminder)
                .where(
                    Reminder.deleted_at_utc.is_(None),
                    Reminder.status.in_(("DISMISSED", "COMPLETED", "CANCELLED")),
                )
                .order_by(_TERMINAL_AT.desc(), Reminder.updated_at_utc.desc(), Reminder.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def list_events(self, reminder_id: str, *, limit: int, offset: int) -> list[ReminderEvent]:
        return list(
            self._session.scalars(
                select(ReminderEvent)
                .where(ReminderEvent.reminder_id == reminder_id)
                .order_by(ReminderEvent.occurred_at_utc.desc(), ReminderEvent.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def _timing_list(self, predicate: Any, *, limit: int, offset: int) -> list[Reminder]:
        return list(
            self._session.scalars(
                select(Reminder)
                .where(
                    Reminder.deleted_at_utc.is_(None),
                    Reminder.status.in_(_ACTIVE),
                    predicate,
                )
                .order_by(Reminder.due_at_utc, Reminder.id)
                .offset(offset)
                .limit(limit)
            )
        )

    @staticmethod
    def _general_ordering(sort: str) -> tuple[Any, ...]:
        orderings: dict[str, tuple[Any, ...]] = {
            "due_soonest": (Reminder.due_at_utc, Reminder.id),
            "due_latest": (Reminder.due_at_utc.desc(), Reminder.id),
            "created_newest": (Reminder.created_at_utc.desc(), Reminder.id),
            "created_oldest": (Reminder.created_at_utc, Reminder.id),
            "updated_newest": (Reminder.updated_at_utc.desc(), Reminder.id),
            "updated_oldest": (Reminder.updated_at_utc, Reminder.id),
            "title": (Reminder.title, Reminder.id),
            "status": (_STATUS_RANK, Reminder.due_at_utc, Reminder.id),
        }
        return orderings[sort]
