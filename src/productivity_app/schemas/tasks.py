"""Public commands, queries, and result models for tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskStatusValue = Literal["TODO", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
TaskPriorityValue = Literal["LOW", "NORMAL", "HIGH", "URGENT"]
TaskSort = Literal[
    "due_soonest",
    "due_latest",
    "priority",
    "created_newest",
    "created_oldest",
    "updated_newest",
    "updated_oldest",
    "title",
    "status",
]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime values must be timezone-aware.")
    return value.astimezone(UTC)


class _TaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTask(_TaskModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=100_000)
    priority: TaskPriorityValue = "NORMAL"
    folder_id: str | None = None
    tag_ids: tuple[str, ...] = ()
    start_at_utc: datetime | None = None
    due_at_utc: datetime | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _trim_title(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Task title must not be blank.")
        return value

    @field_validator("start_at_utc", "due_at_utc")
    @classmethod
    def _aware_datetimes(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _valid_date_order(self) -> CreateTask:
        if (
            self.start_at_utc is not None
            and self.due_at_utc is not None
            and self.start_at_utc > self.due_at_utc
        ):
            raise ValueError("Task start must not be after its due time.")
        return self


class UpdateTask(_TaskModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=100_000)
    priority: TaskPriorityValue | None = None
    folder_id: str | None = None
    start_at_utc: datetime | None = None
    due_at_utc: datetime | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _trim_optional_title(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Task title must not be blank.")
        return value

    @field_validator("start_at_utc", "due_at_utc")
    @classmethod
    def _aware_datetimes(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class TaskQuery(_TaskModel):
    statuses: tuple[TaskStatusValue, ...] = ()
    priorities: tuple[TaskPriorityValue, ...] = ()
    folder_id: str | None = None
    tag_id: str | None = None
    start_from_utc: datetime | None = None
    start_before_utc: datetime | None = None
    due_from_utc: datetime | None = None
    due_before_utc: datetime | None = None
    completed_from_utc: datetime | None = None
    completed_before_utc: datetime | None = None
    include_deleted: bool = False
    sort: TaskSort = "updated_newest"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @field_validator("statuses", "priorities")
    @classmethod
    def _deduplicate(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator(
        "start_from_utc",
        "start_before_utc",
        "due_from_utc",
        "due_before_utc",
        "completed_from_utc",
        "completed_before_utc",
    )
    @classmethod
    def _aware_datetimes(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _valid_ranges(self) -> TaskQuery:
        for start, end, label in (
            (self.start_from_utc, self.start_before_utc, "start"),
            (self.due_from_utc, self.due_before_utc, "due"),
            (self.completed_from_utc, self.completed_before_utc, "completion"),
        ):
            if start is not None and end is not None and start >= end:
                raise ValueError(f"Task {label} range must have from before before.")
        if (
            (self.completed_from_utc is not None or self.completed_before_utc is not None)
            and self.statuses
            and "COMPLETED" not in self.statuses
        ):
            raise ValueError("Completion ranges require COMPLETED status when statuses are supplied.")
        return self


class TaskSearchQuery(_TaskModel):
    query: str = Field(min_length=1, max_length=200)
    include_terminal: bool = False
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("query", mode="before")
    @classmethod
    def _trim_query(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Search query must not be empty.")
        return value


class TaskPageQuery(_TaskModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class TaskCalendarQuery(TaskPageQuery):
    timezone: str = Field(min_length=1, max_length=128)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Timezone must be a valid IANA zone name.") from exc
        return value


class TaskSummary(_TaskModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    title: str
    status: TaskStatusValue
    priority: TaskPriorityValue
    folder_id: str | None
    tag_ids: tuple[str, ...]
    start_at_utc: datetime | None
    due_at_utc: datetime | None
    completed_at_utc: datetime | None
    cancelled_at_utc: datetime | None
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None


class TaskView(TaskSummary):
    description: str
