"""Reminder workflows, derived timing, lifecycle, and durable audit history."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from panelo.core.clock import Clock
from panelo.core.events import DomainEvent
from panelo.core.exceptions import (
    ConflictError,
    DatabaseCorruptionError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from panelo.core.ids import IdGenerator, Uuid4Generator
from panelo.core.recurrence import (
    RepeatKind,
    RepeatRule,
    RepeatUnit,
    advance,
    align_due,
    days_to_mask,
    describe,
    mask_to_days,
)
from panelo.db.models.initial import Reminder, ReminderEvent
from panelo.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from panelo.schemas.reminders import (
    CreateReminder,
    ReminderEventView,
    ReminderPageQuery,
    ReminderQuery,
    ReminderRepeat,
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
        "ck_reminders_repeat_kind_values",
        "ck_reminders_repeat_unit_values",
        "ck_reminders_repeat_shape",
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


def _rule(reminder: Reminder) -> RepeatRule | None:
    """The repeat rule stored in the reminder's columns (``None`` for a one-time reminder)."""
    if reminder.repeat_kind is None:
        return None
    return RepeatRule(
        kind=cast(RepeatKind, reminder.repeat_kind),
        days=mask_to_days(reminder.repeat_days) if reminder.repeat_days is not None else (),
        day_of_month=reminder.repeat_day_of_month,
        every_n=reminder.repeat_every_n,
        every_unit=cast(RepeatUnit | None, reminder.repeat_every_unit),
    )


def _rule_from_schema(repeat: ReminderRepeat) -> RepeatRule:
    return RepeatRule(
        kind=repeat.kind,
        days=repeat.days,
        day_of_month=repeat.day_of_month,
        every_n=repeat.every_n,
        every_unit=repeat.every_unit,
    )


def _repeat_columns(rule: RepeatRule | None) -> dict[str, object]:
    """The repeat columns that encode ``rule`` (all ``None`` for no rule)."""
    return {
        "repeat_kind": rule.kind if rule else None,
        "repeat_days": days_to_mask(rule.days) if rule and rule.days else None,
        "repeat_day_of_month": rule.day_of_month if rule else None,
        "repeat_every_n": rule.every_n if rule else None,
        "repeat_every_unit": rule.every_unit if rule else None,
    }


def _repeat_view(rule: RepeatRule) -> ReminderRepeat:
    return ReminderRepeat(
        kind=rule.kind,
        days=rule.days,
        day_of_month=rule.day_of_month,
        every_n=rule.every_n,
        every_unit=rule.every_unit,
    )


def _summary(reminder: Reminder, now: datetime, times_done: int = 0) -> ReminderSummary:
    rule = _rule(reminder)
    return ReminderSummary(
        id=reminder.id,
        title=reminder.title,
        message=reminder.message,
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
        repeat=_repeat_view(rule) if rule else None,
        repeat_description=(
            describe(rule, reminder.anchor_at_utc, ZoneInfo(reminder.scheduled_timezone))
            if rule
            else None
        ),
        anchor_at_utc=reminder.anchor_at_utc,
        times_done=times_done,
        created_at_utc=reminder.created_at_utc,
        updated_at_utc=reminder.updated_at_utc,
        version=reminder.version,
    )


def _view(reminder: Reminder, now: datetime, times_done: int = 0) -> ReminderView:
    return ReminderView(**_summary(reminder, now, times_done).model_dump())


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
    def _claim(
        uow: SqlAlchemyUnitOfWork, reminder: Reminder, expected_version: int, now: datetime
    ) -> None:
        assert uow.reminders is not None
        if not uow.reminders.claim_version(reminder.id, expected_version, now):
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

    @staticmethod
    def _aligned_due(rule: RepeatRule, due: datetime, timezone: str) -> datetime:
        """The first valid occurrence of ``rule`` at or after ``due``."""
        aligned = align_due(rule, due, ZoneInfo(timezone))
        if aligned is None:
            raise ValidationError("The repeat rule has no valid occurrence.")
        return aligned

    @staticmethod
    def _times_done(uow: SqlAlchemyUnitOfWork, reminder_ids: Sequence[str]) -> dict[str, int]:
        assert uow.reminders is not None
        counts = uow.reminders.completion_counts(reminder_ids)
        return {reminder_id: counts.get(reminder_id, 0) for reminder_id in reminder_ids}

    def _view_of(self, uow: SqlAlchemyUnitOfWork, reminder: Reminder, now: datetime) -> ReminderView:
        return _view(reminder, now, self._times_done(uow, [reminder.id])[reminder.id])

    def _summaries(
        self, uow: SqlAlchemyUnitOfWork, reminders: Sequence[Reminder], now: datetime
    ) -> tuple[ReminderSummary, ...]:
        counts = self._times_done(uow, [reminder.id for reminder in reminders])
        return tuple(_summary(reminder, now, counts[reminder.id]) for reminder in reminders)

    def create_reminder(self, command: CreateReminder) -> ReminderView:
        now = self._clock.now_utc()
        rule = _rule_from_schema(command.repeat) if command.repeat else None
        due = (
            self._aligned_due(rule, command.due_at_utc, command.scheduled_timezone)
            if rule
            else command.due_at_utc
        )
        with self._uows() as uow:
            assert uow.reminders is not None
            uow.acquire_write_lock()
            self._active_task(uow, command.task_id)
            self._active_note(uow, command.note_id)
            reminder = Reminder(
                id=self._ids.new(),
                title=command.title,
                message=command.message,
                due_at_utc=due,
                scheduled_timezone=command.scheduled_timezone,
                status="PENDING",
                task_id=command.task_id,
                note_id=command.note_id,
                last_triggered_at_utc=None,
                dismissed_at_utc=None,
                completed_at_utc=None,
                cancelled_at_utc=None,
                snooze_count=0,
                anchor_at_utc=due if rule else None,
                **_repeat_columns(rule),
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

    def get_reminder(self, reminder_id: str) -> ReminderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            return self._view_of(uow, self._existing(uow, reminder_id), now)

    def update_reminder(self, reminder_id: str, patch: UpdateReminder) -> ReminderView:
        values = patch.model_dump(exclude={"expected_version", "repeat"}, exclude_unset=True)
        repeat_given = "repeat" in patch.model_fields_set
        if not values and not repeat_given:
            raise ValidationError("Reminder update must include at least one field.")
        if any(
            values.get(key) is None
            for key in ("title", "message", "due_at_utc", "scheduled_timezone")
            if key in values
        ):
            raise ValidationError("Reminder title, message, due time, and timezone cannot be null.")
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._existing(uow, reminder_id)
            if reminder.status not in _ACTIVE:
                raise InvalidStateTransitionError("Terminal reminders must be reopened before editing.")
            final_task = values.get("task_id", reminder.task_id)
            final_note = values.get("note_id", reminder.note_id)
            if final_task is not None and final_note is not None:
                raise ValidationError("A reminder may link to either a task or a note, not both.")
            if "task_id" in values and final_task is not None and final_task != reminder.task_id:
                self._active_task(uow, final_task)
            if "note_id" in values and final_note is not None and final_note != reminder.note_id:
                self._active_note(uow, final_note)
            changes = self._changes(reminder, values, patch.repeat, repeat_given)
            changed = any(getattr(reminder, key) != value for key, value in changes.items())
            if not changed and reminder.status == "PENDING":
                raise ValidationError("Reminder update does not change any fields.")
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, patch.expected_version, now)
            for key, value in changes.items():
                setattr(reminder, key, value)
            self._canonicalize_active(reminder)
            self._audit_event(uow, "REMINDER_UPDATED", reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_UPDATED", reminder, now)
            self._commit(uow)
            return self._view_of(uow, reminder, now)

    def _changes(
        self,
        reminder: Reminder,
        values: dict[str, object],
        repeat: ReminderRepeat | None,
        repeat_given: bool,
    ) -> dict[str, object]:
        """The column values an update leaves the reminder with.

        Setting a rule moves the due time to the next valid occurrence at or after it, and
        the result is the new anchor.  Changing only the due time of a repeating reminder
        makes the new time the anchor.
        """
        changes = dict(values)
        due = cast(datetime, values.get("due_at_utc", reminder.due_at_utc))
        if repeat_given:
            rule = _rule_from_schema(repeat) if repeat else None
            changes.update(_repeat_columns(rule))
            if rule is not None:
                timezone = cast(str, values.get("scheduled_timezone", reminder.scheduled_timezone))
                due = self._aligned_due(rule, due, timezone)
                changes["due_at_utc"] = due
            changes["anchor_at_utc"] = due if rule is not None else None
        elif reminder.repeat_kind is not None and "due_at_utc" in values:
            changes["anchor_at_utc"] = due
        return changes

    def snooze_reminder(self, reminder_id: str, command: SnoozeReminder) -> ReminderView:
        """Move the due time; a repeat rule's anchor stays where it was."""
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._existing(uow, reminder_id)
            if reminder.status not in _ACTIVE or reminder.due_at_utc > now:
                raise InvalidStateTransitionError("Only due or overdue reminders may be snoozed.")
            if command.due_at_utc <= now:
                raise ValidationError("Snoozed reminders must be scheduled in the future.")
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, command.expected_version, now)
            reminder.due_at_utc = command.due_at_utc
            if command.scheduled_timezone is not None:
                reminder.scheduled_timezone = command.scheduled_timezone
            reminder.status = "PENDING"
            reminder.snooze_count += 1
            self._audit_event(uow, "REMINDER_SNOOZED", reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_SNOOZED", reminder, now)
            self._commit(uow)
            return self._view_of(uow, reminder, now)

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
            reminder = self._existing(uow, reminder_id)
            if reminder.status not in required:
                raise InvalidStateTransitionError(
                    "Reminder cannot make that transition from its current state."
                )
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, expected_version, now)
            apply(reminder, now)
            self._audit_event(uow, event_type, reminder, now, previous_status, old_due)
            self._domain_event(uow, event_type, reminder, now)
            self._commit(uow)
            return self._view_of(uow, reminder, now)

    def complete_reminder(self, reminder_id: str, expected_version: int) -> ReminderView:
        """Finish a one-time reminder; advance a repeating one to its next occurrence.

        A repeating reminder stays ``PENDING``: its due time and anchor become the next
        occurrence (strictly in the future and after the current due time, skipping any that
        were missed) and its snoozes are forgotten.  A ``COMPLETED_OCCURRENCE`` audit event
        records the completion (it is what ``times_done`` counts).
        """
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            reminder = self._existing(uow, reminder_id)
            if reminder.status not in _ACTIVE:
                raise InvalidStateTransitionError(
                    "Reminder cannot make that transition from its current state."
                )
            previous_status, old_due = reminder.status, reminder.due_at_utc
            self._claim(uow, reminder, expected_version, now)
            rule = _rule(reminder)
            if rule is None:
                reminder.status = "COMPLETED"
                reminder.completed_at_utc = now
                reminder.dismissed_at_utc = None
                reminder.cancelled_at_utc = None
                audit_type = "REMINDER_COMPLETED"
            else:
                following = advance(
                    rule,
                    reminder.due_at_utc,
                    reminder.anchor_at_utc,
                    now,
                    ZoneInfo(reminder.scheduled_timezone),
                )
                if following is None:
                    raise DatabaseCorruptionError("A repeating reminder has no next occurrence.")
                reminder.status = "PENDING"
                reminder.due_at_utc = following
                reminder.anchor_at_utc = following
                reminder.snooze_count = 0
                audit_type = "COMPLETED_OCCURRENCE"
            self._audit_event(uow, audit_type, reminder, now, previous_status, old_due)
            self._domain_event(uow, "REMINDER_COMPLETED", reminder, now)
            self._commit(uow)
            return self._view_of(uow, reminder, now)

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
        """Delete permanently (reminders have no Trash); returns the reminder as it was."""
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            uow.acquire_write_lock()
            reminder = self._existing(uow, reminder_id)
            self._claim(uow, reminder, expected_version, now)
            view = self._view_of(uow, reminder, now)
            uow.reminders.delete(reminder.id)
            self._domain_event(uow, "REMINDER_DELETED", reminder, now)
            self._commit(uow)
            return view

    def list_reminders(self, query: ReminderQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return self._summaries(uow, uow.reminders.list_reminders(data, now), now)

    def list_upcoming(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return self._summaries(
                uow, uow.reminders.list_upcoming(now, limit=data.limit, offset=data.offset), now
            )

    def list_due(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return self._summaries(
                uow, uow.reminders.list_due(now, limit=data.limit, offset=data.offset), now
            )

    def list_overdue(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return self._summaries(
                uow, uow.reminders.list_overdue(now, limit=data.limit, offset=data.offset), now
            )

    def list_history(self, query: ReminderPageQuery | None = None) -> tuple[ReminderSummary, ...]:
        data, now = query or ReminderPageQuery(), self._clock.now_utc()
        with self._uows() as uow:
            assert uow.reminders is not None
            return self._summaries(
                uow, uow.reminders.list_history(limit=data.limit, offset=data.offset), now
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
                for event in uow.reminders.list_events(
                    reminder_id, limit=data.limit, offset=data.offset
                )
            )
