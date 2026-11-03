"""Task workflows, lifecycle rules, and calendar-aware views."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

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
from productivity_app.db.models.initial import Task
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.schemas.tasks import (
    CreateTask,
    TaskCalendarQuery,
    TaskPageQuery,
    TaskPriorityValue,
    TaskQuery,
    TaskSearchQuery,
    TaskStatusValue,
    TaskSummary,
    TaskView,
    UpdateTask,
)

_ACTIVE = {"TODO", "IN_PROGRESS"}
_TASK_CHECK_CONSTRAINTS = frozenset(
    {
        "ck_tasks_title_length",
        "ck_tasks_start_before_due",
        "ck_tasks_priority_values",
        "ck_tasks_status_values",
        "ck_tasks_terminal_timestamps",
        "ck_tasks_due_has_time_bool",
    }
)


def _translate_task_integrity_error(exc: IntegrityError) -> None:
    """Translate only integrity failures reachable from task workflows.

    Phase 1's generic translator remains unchanged for its frozen callers.
    Unknown SQLite integrity errors must stay visible to callers and logs.
    """
    message = str(exc.orig).lower() if exc.orig is not None else ""
    if "foreign key constraint failed" in message:
        raise NotFoundError("A related item is no longer available.") from exc
    if "unique constraint failed: tasks.id" in message:
        raise ConflictError("The requested task change conflicts with current data.") from exc
    if any(f"check constraint failed: {name}" in message for name in _TASK_CHECK_CONSTRAINTS):
        raise ValidationError("The requested task change violates a domain invariant.") from exc
    raise exc


def _view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        title=task.title,
        description=task.description,
        status=cast(TaskStatusValue, task.status),
        priority=cast(TaskPriorityValue, task.priority),
        start_at_utc=task.start_at_utc,
        due_at_utc=task.due_at_utc,
        due_has_time=task.due_has_time,
        completed_at_utc=task.completed_at_utc,
        cancelled_at_utc=task.cancelled_at_utc,
        created_at_utc=task.created_at_utc,
        updated_at_utc=task.updated_at_utc,
        version=task.version,
        deleted_at_utc=task.deleted_at_utc,
    )


def _summary(task: Task) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        title=task.title,
        status=cast(TaskStatusValue, task.status),
        priority=cast(TaskPriorityValue, task.priority),
        start_at_utc=task.start_at_utc,
        due_at_utc=task.due_at_utc,
        due_has_time=task.due_has_time,
        completed_at_utc=task.completed_at_utc,
        cancelled_at_utc=task.cancelled_at_utc,
        created_at_utc=task.created_at_utc,
        updated_at_utc=task.updated_at_utc,
        version=task.version,
        deleted_at_utc=task.deleted_at_utc,
    )


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class TaskService:
    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None
    ) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    def _event(self, uow: SqlAlchemyUnitOfWork, kind: str, task: Task, now: datetime) -> None:
        uow.collect_event(DomainEvent(kind, task.id, now, f"task:{kind.lower()}:{task.id}:{task.version}"))

    @staticmethod
    def _active_task(uow: SqlAlchemyUnitOfWork, task_id: str) -> Task:
        assert uow.tasks is not None
        task = uow.tasks.get(task_id)
        if task is None or task.deleted_at_utc is not None:
            raise NotFoundError("Task was not found.")
        return task

    @staticmethod
    def _claim_active(
        uow: SqlAlchemyUnitOfWork, task: Task, expected_version: int, now: datetime
    ) -> None:
        assert uow.tasks is not None
        if not uow.tasks.claim_version(task.id, expected_version, now, deleted=False):
            raise StaleVersionError("Task changed; refresh and retry.")

    @staticmethod
    def _flush_translated(uow: SqlAlchemyUnitOfWork) -> None:
        assert uow.session is not None
        try:
            uow.session.flush()
        except IntegrityError as exc:
            _translate_task_integrity_error(exc)

    @staticmethod
    def _commit(uow: SqlAlchemyUnitOfWork) -> None:
        try:
            uow.commit()
        except IntegrityError as exc:
            _translate_task_integrity_error(exc)

    def create_task(self, command: CreateTask) -> TaskView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tasks is not None
            uow.acquire_write_lock()
            task = Task(
                id=self._ids.new(),
                title=command.title,
                description=command.description,
                status="TODO",
                priority=command.priority,
                start_at_utc=command.start_at_utc,
                due_at_utc=command.due_at_utc,
                due_has_time=command.due_has_time,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            uow.tasks.add(task)
            self._flush_translated(uow)
            self._event(uow, "TASK_CREATED", task, now)
            self._commit(uow)
            return _view(task)

    def get_task(self, task_id: str, *, include_deleted: bool = False) -> TaskView:
        with self._uows() as uow:
            assert uow.tasks is not None
            task = uow.tasks.get(task_id)
            if task is None or (task.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Task was not found.")
            return _view(task)

    def update_task(self, task_id: str, patch: UpdateTask) -> TaskView:
        values = patch.model_dump(exclude={"expected_version"}, exclude_unset=True)
        if not values:
            raise ValidationError("Task update must include at least one field.")
        if any(
            values.get(key) is None
            for key in ("title", "description", "priority", "due_has_time")
            if key in values
        ):
            raise ValidationError(
                "Task title, description, priority, and date-only flag cannot be null."
            )
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            task = self._active_task(uow, task_id)
            start = values.get("start_at_utc", task.start_at_utc)
            due = values.get("due_at_utc", task.due_at_utc)
            if start is not None and due is not None and start > due:
                raise ValidationError("Task start must not be after its due time.")
            self._claim_active(uow, task, patch.expected_version, now)
            for key, value in values.items():
                setattr(task, key, value)
            self._event(uow, "TASK_UPDATED", task, now)
            self._commit(uow)
            assert uow.tasks is not None
            return _view(task)

    def _transition(
        self,
        task_id: str,
        expected_version: int,
        event: str,
        required_states: set[str],
        apply: Callable[[Task, datetime], None],
    ) -> TaskView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            task = self._active_task(uow, task_id)
            if task.status not in required_states:
                raise InvalidStateTransitionError("Task cannot make that transition from its current state.")
            self._claim_active(uow, task, expected_version, now)
            apply(task, now)
            self._event(uow, event, task, now)
            self._commit(uow)
            assert uow.tasks is not None
            return _view(task)

    def start_task(self, task_id: str, expected_version: int) -> TaskView:
        return self._transition(
            task_id, expected_version, "TASK_STARTED", {"TODO"}, lambda task, _: setattr(task, "status", "IN_PROGRESS")
        )

    def return_task_to_todo(self, task_id: str, expected_version: int) -> TaskView:
        return self._transition(
            task_id, expected_version, "TASK_RETURNED_TO_TODO", {"IN_PROGRESS"}, lambda task, _: setattr(task, "status", "TODO")
        )

    def complete_task(self, task_id: str, expected_version: int) -> TaskView:
        def apply(task: Task, now: datetime) -> None:
            task.status, task.completed_at_utc, task.cancelled_at_utc = "COMPLETED", now, None

        return self._transition(task_id, expected_version, "TASK_COMPLETED", _ACTIVE, apply)

    def cancel_task(self, task_id: str, expected_version: int) -> TaskView:
        def apply(task: Task, now: datetime) -> None:
            task.status, task.completed_at_utc, task.cancelled_at_utc = "CANCELLED", None, now

        return self._transition(task_id, expected_version, "TASK_CANCELLED", _ACTIVE, apply)

    def reopen_task(self, task_id: str, expected_version: int) -> TaskView:
        def apply(task: Task, _: datetime) -> None:
            task.status, task.completed_at_utc, task.cancelled_at_utc = "TODO", None, None

        return self._transition(
            task_id, expected_version, "TASK_REOPENED", {"COMPLETED", "CANCELLED"}, apply
        )

    def delete_task(self, task_id: str, expected_version: int) -> TaskView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            task = self._active_task(uow, task_id)
            self._claim_active(uow, task, expected_version, now)
            task.deleted_at_utc = now
            self._event(uow, "TASK_DELETED", task, now)
            self._commit(uow)
            assert uow.tasks is not None
            return _view(task)

    def restore_task(self, task_id: str, expected_version: int) -> TaskView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tasks is not None
            uow.acquire_write_lock()
            task = uow.tasks.get(task_id)
            if task is None or task.deleted_at_utc is None:
                raise NotFoundError("Deleted task was not found.")
            if not uow.tasks.claim_version(task.id, expected_version, now, deleted=True):
                raise StaleVersionError("Task changed; refresh and retry.")
            task.deleted_at_utc = None
            self._event(uow, "TASK_RESTORED", task, now)
            self._commit(uow)
            return _view(task)

    def list_tasks(self, query: TaskQuery | None = None) -> tuple[TaskSummary, ...]:
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_tasks(query or TaskQuery()))

    def search_tasks(self, query: TaskSearchQuery) -> tuple[TaskSummary, ...]:
        term = query.query.strip()
        if not term:
            raise ValidationError("Search query must not be empty.")
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(
                _summary(task)
                for task in uow.tasks.search_like(term, _escaped_like(term), include_terminal=query.include_terminal, limit=query.limit)
            )

    def _calendar_window(self, timezone: str, days: int) -> tuple[datetime, datetime]:
        zone = ZoneInfo(timezone)
        local_date: date = self._clock.now_utc().astimezone(zone).date()
        start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
        end = datetime.combine(local_date + timedelta(days=days), time.min, tzinfo=zone).astimezone(UTC)
        return start, end

    def list_today(self, query: TaskCalendarQuery) -> tuple[TaskSummary, ...]:
        start, end = self._calendar_window(query.timezone, 1)
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_today(start, end, limit=query.limit, offset=query.offset))

    def list_next_seven_days(self, query: TaskCalendarQuery) -> tuple[TaskSummary, ...]:
        start, end = self._calendar_window(query.timezone, 7)
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_next_seven_days(start, end, limit=query.limit, offset=query.offset))

    def list_upcoming(self, query: TaskCalendarQuery) -> tuple[TaskSummary, ...]:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_upcoming(now, limit=query.limit, offset=query.offset))

    def list_overdue(self, query: TaskPageQuery) -> tuple[TaskSummary, ...]:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_overdue(now, limit=query.limit, offset=query.offset))

    def list_completed(self, query: TaskPageQuery) -> tuple[TaskSummary, ...]:
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_completed(limit=query.limit, offset=query.offset))

    def list_important(self, query: TaskPageQuery) -> tuple[TaskSummary, ...]:
        with self._uows() as uow:
            assert uow.tasks is not None
            return tuple(_summary(task) for task in uow.tasks.list_important(limit=query.limit, offset=query.offset))
