"""Reminder workflows, derived timing, lifecycle, and durable audit history."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast

from sqlalchemy.exc import IntegrityError

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.core.ids import IdGenerator, Uuid4Generator
from productivity_app.db.models.initial import Reminder, ReminderEvent
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.schemas.reminders import (
    CreateReminder,
    ReminderEventView,
    ReminderPageQuery,
    ReminderQuery,
    ReminderStatusValue,
    ReminderSummary,
    ReminderTimingValue,
    ReminderView,
    SnoozeReminder,
    UpdateReminder,
)

_ACTIVE = {"PENDING", "TRIGGERED", "OVERDUE"}
_TERMINAL = {"DISMISSED", "COMPLETED", "CANCELLED"}
_REMINDER_CHECKS = frozenset(
    {
        "ck_reminders_title_length",
        "ck_reminders_one_link",
        "ck_reminders_snooze_count",
        "ck_reminders_status_values",
        "ck_reminders_terminal_timestamps",
    }
)


def _translate_reminder_integrity_error(exc: IntegrityError) -> None:
    """Translate only known reminder invariants; preserve unknown database failures."""
    message = str(exc.orig).lower() if exc.orig is not None else ""
    if "unique constraint failed: reminders.id" in message or (
        "unique constraint failed: reminder_events.id" in message
    ):
        raise ConflictError("The requested reminder change conflicts with current data.") from exc
    if any(f"check constraint failed: {name}" in message for name in _REMINDER_CHECKS):
        raise ValidationError("The requested reminder change violates a domain invariant.") from exc
    raise exc


def _timing_state(reminder: Reminder, now: datetime) -> ReminderTimingValue | None:
    if reminder.status not in _ACTIVE:
        return None
    if reminder.due_at_utc > now:
        return "UPCOMING"
    if reminder.due_at_utc == now:
        return "DUE"
    return "OVERDUE"


def _summary(reminder: Reminder, now: datetime) -> ReminderSummary:
    return ReminderSummary(
        id=reminder.id,
        title=reminder.title,
        status=cast(ReminderStatusValue, reminder.status),
        timing_state=_timing_state(reminder, now),
        due_at_utc=reminder.due_at_utc,
        scheduled_timezone=reminder.scheduled_timezone,
        task_id=reminder.task_id,
        note_id=reminder.note_id,
        last_triggered_at_utc=reminder.last_triggered_at_utc,
        dismissed_at_utc=reminder.dismissed_at_utc,
        completed_at_utc=reminder.completed_at_utc,
        cancelled_at_utc=reminder.cancelled_at_utc,
        snooze_count=reminder.snooze_count,
        created_at_utc=reminder.created_at_utc,
        updated_at_utc=reminder.updated_at_utc,
        version=reminder.version,
        deleted_at_utc=reminder.deleted_at_utc,
    )


def _view(reminder: Reminder, now: datetime) -> ReminderView:
    return ReminderView(**_summary(reminder, now).model_dump(), message=reminder.message)


def _event_view(event: ReminderEvent) -> ReminderEventView:
    return ReminderEventView(
        id=event.id,
        reminder_id=event.reminder_id,
        event_type=event.event_type,
        occurred_at_utc=event.occurred_at_utc,
        previous_status=cast(ReminderStatusValue | None, event.previous_status),
        new_status=cast(ReminderStatusValue | None, event.new_status),
        old_due_at_utc=event.old_due_at_utc,
        new_due_at_utc=event.new_due_at_utc,
        metadata_json=event.metadata_json,
    )


class ReminderService:
    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None
    ) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    def _domain_event(
        self, uow: SqlAlchemyUnitOfWork, kind: str, reminder: Reminder, now: datetime
    ) -> None:
        uow.collect_event(
            DomainEvent(
                kind,
                reminder.id,
                now,
                f"reminder:{kind.lower()}:{reminder.id}:{reminder.version}",
            )
        )

    def _audit_event(
        self,
        uow: SqlAlchemyUnitOfWork,
        kind: str,
        reminder: Reminder,
        now: datetime,
        previous_status: str | None,
        old_due_at_utc: datetime | None,
    ) -> None:
        assert uow.reminders is not None
        uow.reminders.add_event(
            ReminderEvent(
                id=self._ids.new(),
                reminder_id=reminder.id,
                event_type=kind,
                occurred_at_utc=now,
                previous_status=previous_status,
                new_status=reminder.status,
                old_due_at_utc=old_due_at_utc,
                new_due_at_utc=reminder.due_at_utc,
                metadata_json=None,
            )
        )

    @staticmethod
    def _active_task(uow: SqlAlchemyUnitOfWork, task_id: str | None) -> None:
        if task_id is None:
            return
        assert uow.tasks is not None
        task = uow.tasks.get(task_id)
        if task is None or task.deleted_at_utc is not None:
            raise NotFoundError("Task was not found.")

    @staticmethod
    def _active_note(uow: SqlAlchemyUnitOfWork, note_id: str | None) -> None:
        if note_id is None:
            return
        assert uow.notes is not None
        note = uow.notes.get(note_id)
        if note is None or note.deleted_at_utc is not None:
            raise NotFoundError("Note was not found.")

    @staticmethod
    def _existing(uow: SqlAlchemyUnitOfWork, reminder_id: str) -> Reminder:
        assert uow.reminders is not None
        reminder = uow.reminders.get(reminder_id)
        if reminder is None:
            raise NotFoundError("Reminder was not found.")
        return reminder

    @staticmethod
    def _active_reminder(uow: SqlAlchemyUnitOfWork, reminder_id: str) -> Reminder:
        reminder = ReminderService._existing(uow, reminder_id)
        if reminder.deleted_at_utc is not None:
            raise NotFoundError("Reminder was not found.")
        return reminder

    @staticmethod
    def _claim(
        uow: SqlAlchemyUnitOfWork, reminder: Reminder, expected_version: int, now: datetime, *, deleted: bool
    ) -> None:
        assert uow.reminders is not None
        if not uow.reminders.claim_version(reminder.id, expected_version, now, deleted=deleted):
            raise StaleVersionError("Reminder changed; refresh and retry.")

    @staticmethod
    def _flush(uow: SqlAlchemyUnitOfWork) -> None:
        assert uow.session is not None
        try:
            uow.session.flush()
        except IntegrityError as exc:
            _translate_reminder_integrity_error(exc)

    @staticmethod
    def _commit(uow: SqlAlchemyUnitOfWork) -> None:
        try:
            uow.commit()
        except IntegrityError as exc:
            _translate_reminder_integrity_error(exc)

    @staticmethod
    def _canonicalize_active(reminder: Reminder) -> None:
        if reminder.status in {"TRIGGERED", "OVERDUE"}:
            reminder.status = "PENDING"

    def create_reminder(self, command: CreateReminder) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            uow.acquire_write_lock()
            self._active_task(uow, command.task_id)
            self._active_note(uow, command.note_id)
            reminder = Reminder(
                id=self._ids.new(),
                title=command.title,
                message=command.message,
                due_at_utc=command.due_at_utc,
                scheduled_timezone=command.scheduled_timezone,
                status="PENDING",
                task_id=command.task_id,
                note_id=command.note_id,
                last_triggered_at_utc=None,
                dismissed_at_utc=None,
                completed_at_utc=None,
                cancelled_at_utc=None,
                snooze_count=0,
                deleted_at_utc=None,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            uow.reminders.add(reminder)
            self._flush(uow)
            self._audit_event(uow, "REMINDER_CREATED", reminder, now, None, None)
            self._domain_event(uow, "REMINDER_CREATED", reminder, now)
            self._commit(uow)
            return _view(reminder, now)

    def get_reminder(self, reminder_id: str, *, include_deleted: bool = False) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            reminder = self._existing(uow, reminder_id)
            if reminder.deleted_at_utc is not None and not include_deleted:
                raise NotFoundError("Reminder was not found.")
            return _view(reminder, now)

    def update_reminder(self, reminder_id: str, patch: UpdateReminder) -> ReminderView:
        values = patch.model_dump(exclude={"expected_version"}, exclude_unset=True)
        if not values:
            raise ValidationError("Reminder update must include at least one field.")
        if any(values.get(key) is None for key in ("title", "message", "due_at_utc", "scheduled_timezone") if key in values):
            raise ValidationError("Reminder title, message, due time, and timezone cannot be null.")
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._active_reminder(uow, reminder_id)
            if reminder.status not in _ACTIVE:
                raise InvalidStateTransitionError("Terminal reminders must be reopened before editing.")
            final_task = values.get("task_id", reminder.task_id)
            final_note = values.get("note_id", reminder.note_id)
            if final_task is not None and final_note is not None:
                raise ValidationError("A reminder may link to either a task or a note, not both.")
            if (
                "task_id" in values
                and final_task is not None
                and final_task != reminder.task_id
            ):
                self._active_task(uow, final_task)
            if (
                "note_id" in values
                and final_note is not None
                and final_note != reminder.note_id
            ):
                self._active_note(uow, final_note)
            changed = any(getattr(reminder, key) != value for key, value in values.items())
            if not changed and reminder.status == "PENDING":
                raise ValidationError("Reminder update does not change any fields.")
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, patch.expected_version, now, deleted=False)
            for key, value in values.items():
                setattr(reminder, key, value)
            self._canonicalize_active(reminder)
            self._audit_event(uow, "REMINDER_UPDATED", reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_UPDATED", reminder, now)
            self._commit(uow)
            return _view(reminder, now)

    def snooze_reminder(self, reminder_id: str, command: SnoozeReminder) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._active_reminder(uow, reminder_id)
            if reminder.status not in _ACTIVE or reminder.due_at_utc > now:
                raise InvalidStateTransitionError("Only due or overdue reminders may be snoozed.")
            if command.due_at_utc <= now:
                raise ValidationError("Snoozed reminders must be scheduled in the future.")
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, command.expected_version, now, deleted=False)
            reminder.due_at_utc = command.due_at_utc
            if command.scheduled_timezone is not None:
                reminder.scheduled_timezone = command.scheduled_timezone
            reminder.status = "PENDING"
            reminder.snooze_count += 1
            self._audit_event(uow, "REMINDER_SNOOZED", reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_SNOOZED", reminder, now)
            self._commit(uow)
            return _view(reminder, now)

    def _transition(
        self,
        reminder_id: str,
        expected_version: int,
        event_type: str,
        required: set[str],
        apply: Callable[[Reminder, datetime], None],
    ) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._active_reminder(uow, reminder_id)
            if reminder.status not in required:
                raise InvalidStateTransitionError(
                    "Reminder cannot make that transition from its current state."
                )
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, expected_version, now, deleted=False)
            apply(reminder, now)
            self._audit_event(uow, event_type, reminder, now, previous_status, old_due)
            self._domain_event(uow, event_type, reminder, now)
            self._commit(uow)
            return _view(reminder, now)

    def complete_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        def apply(reminder: Reminder, now: datetime) -> None:
            reminder.status = "COMPLETED"
            reminder.completed_at_utc = now
            reminder.dismissed_at_utc = None
            reminder.cancelled_at_utc = None

        return self._transition(reminder_id, expected_version, "REMINDER_COMPLETED", _ACTIVE, apply)

    def dismiss_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        def apply(reminder: Reminder, now: datetime) -> None:
            reminder.status = "DISMISSED"
            reminder.dismissed_at_utc = now
            reminder.completed_at_utc = None
            reminder.cancelled_at_utc = None

        return self._transition(reminder_id, expected_version, "REMINDER_DISMISSED", _ACTIVE, apply)

    def cancel_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        def apply(reminder: Reminder, now: datetime) -> None:
            reminder.status = "CANCELLED"
            reminder.cancelled_at_utc = now
            reminder.dismissed_at_utc = None
            reminder.completed_at_utc = None

        return self._transition(reminder_id, expected_version, "REMINDER_CANCELLED", _ACTIVE, apply)

    def reopen_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        def apply(reminder: Reminder, _: datetime) -> None:
            reminder.status = "PENDING"
            reminder.dismissed_at_utc = None
            reminder.completed_at_utc = None
            reminder.cancelled_at_utc = None

        return self._transition(reminder_id, expected_version, "REMINDER_REOPENED", _TERMINAL, apply)

    def delete_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._active_reminder(uow, reminder_id)
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, expected_version, now, deleted=False)
            reminder.deleted_at_utc = now
            self._audit_event(uow, "REMINDER_DELETED", reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_DELETED", reminder, now)
            self._commit(uow)
            return _view(reminder, now)

    def restore_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._existing(uow, reminder_id)
            if reminder.deleted_at_utc is None:
                raise NotFoundError("Deleted reminder was not found.")
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, expected_version, now, deleted=True)
            reminder.deleted_at_utc = None
            self._canonicalize_active(reminder)
            self._audit_event(uow, "REMINDER_RESTORED", reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_RESTORED", reminder, now)
            self._commit(uow)
            return _view(reminder, now)

    def list_reminders(self, query: ReminderQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return tuple(_summary(reminder, now) for reminder in uow.reminders.list_reminders(data, now))

    def list_upcoming(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return tuple(
                _summary(reminder, now)
                for reminder in uow.reminders.list_upcoming(now, limit=data.limit, offset=data.offset)
            )

    def list_due(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return tuple(
                _summary(reminder, now)
                for reminder in uow.reminders.list_due(now, limit=data.limit, offset=data.offset)
            )

    def list_overdue(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return tuple(
                _summary(reminder, now)
                for reminder in uow.reminders.list_overdue(now, limit=data.limit, offset=data.offset)
            )

    def list_history(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return tuple(
                _summary(reminder, now)
                for reminder in uow.reminders.list_history(limit=data.limit, offset=data.offset)
            )

    def list_reminder_events(
        self, reminder_id: str, query: ReminderPageQuery | None = None
    ) -> tuple[ReminderEventView, ...]:
        data = query or ReminderPageQuery()
        with self._uows() as uow:
            assert uow.reminders is not None
            self._existing(uow, reminder_id)
            return tuple(
                _event_view(event)
                for event in uow.reminders.list_events(reminder_id, limit=data.limit, offset=data.offset)
            )
