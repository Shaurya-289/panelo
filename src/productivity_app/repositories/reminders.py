"""Persistence queries for reminders and their durable audit history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, delete, func, or_, select, update
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

    def claim_version(self, reminder_id: str, expected_version: int, now_utc: datetime) -> bool:
        """Atomically claim one expected version; ``True`` means one row changed."""
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Reminder)
                .where(Reminder.id == reminder_id, Reminder.version == expected_version)
                .values(version=Reminder.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1

    def delete(self, reminder_id: str) -> None:
        """Remove the row for good; its audit events go with it (``ON DELETE CASCADE``)."""
        self._session.execute(delete(ReminderEvent).where(ReminderEvent.reminder_id == reminder_id))
        self._session.execute(delete(Reminder).where(Reminder.id == reminder_id))

    def completion_counts(self, reminder_ids: Sequence[str]) -> dict[str, int]:
        """How many times Done advanced each repeating reminder to its next occurrence."""
        if not reminder_ids:
            return {}
        rows = self._session.execute(
            select(ReminderEvent.reminder_id, func.count())
            .where(
                ReminderEvent.reminder_id.in_(reminder_ids),
                ReminderEvent.event_type == "COMPLETED_OCCURRENCE",
            )
            .group_by(ReminderEvent.reminder_id)
        ).all()
        return {str(reminder_id): int(count) for reminder_id, count in rows}

    def clear_links(
        self, *, task_id: str | None = None, note_id: str | None = None, now_utc: datetime
    ) -> list[str]:
        """Detach every reminder linked to a task or note that is being deleted for good.

        Each detached reminder gets a new version, so cached copies are seen as stale.
        """
        column = Reminder.task_id if task_id is not None else Reminder.note_id
        ids = list(
            self._session.scalars(
                select(Reminder.id).where(column == (task_id if task_id is not None else note_id))
            )
        )
        if ids:
            values: dict[str, Any] = {
                "task_id" if task_id is not None else "note_id": None,
                "version": Reminder.version + 1,
                "updated_at_utc": now_utc,
            }
            self._session.execute(update(Reminder).where(Reminder.id.in_(ids)).values(**values))
        return ids

    def list_reminders(self, data: ReminderQuery, now_utc: datetime) -> list[Reminder]:
        query = select(Reminder)
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
                .where(Reminder.status.in_(("DISMISSED", "COMPLETED", "CANCELLED")))
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
                .where(Reminder.status.in_(_ACTIVE), predicate)
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
