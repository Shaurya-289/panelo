"""Public commands, queries, and result models for persisted reminders."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReminderStatusValue = Literal[
    "PENDING", "TRIGGERED", "OVERDUE", "DISMISSED", "COMPLETED", "CANCELLED"
]
ReminderTimingValue = Literal["UPCOMING", "DUE", "OVERDUE"]
ReminderSort = Literal[
    "due_soonest",
    "due_latest",
    "created_newest",
    "created_oldest",
    "updated_newest",
    "updated_oldest",
    "title",
    "status",
]
ReminderLinkType = Literal["ANY", "NONE", "TASK", "NOTE"]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime values must be timezone-aware.")
    return value.astimezone(UTC)


class _ReminderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateReminder(_ReminderModel):
    title: str = Field(min_length=1, max_length=300)
    message: str = Field(default="", max_length=100_000)
    due_at_utc: datetime
    scheduled_timezone: str = Field(min_length=1, max_length=64)
    task_id: str | None = None
    note_id: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _trim_title(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Reminder title must not be blank.")
        return value

    @field_validator("due_at_utc")
    @classmethod
    def _aware_due(cls, value: datetime) -> datetime:
        result = _utc(value)
        assert result is not None
        return result

    @field_validator("scheduled_timezone", mode="before")
    @classmethod
    def _known_timezone(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Timezone must be a valid IANA zone name.") from exc
        return value

    @model_validator(mode="after")
    def _one_link(self) -> CreateReminder:
        if self.task_id is not None and self.note_id is not None:
            raise ValueError("A reminder may link to either a task or a note, not both.")
        return self


class UpdateReminder(_ReminderModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    message: str | None = Field(default=None, max_length=100_000)
    due_at_utc: datetime | None = None
    scheduled_timezone: str | None = Field(default=None, max_length=64)
    task_id: str | None = None
    note_id: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _trim_title(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Reminder title must not be blank.")
        return value

    @field_validator("due_at_utc")
    @classmethod
    def _aware_due(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @field_validator("scheduled_timezone", mode="before")
    @classmethod
    def _known_timezone(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Timezone must be a valid IANA zone name.") from exc
        return value


class SnoozeReminder(_ReminderModel):
    expected_version: int = Field(ge=1)
    due_at_utc: datetime
    scheduled_timezone: str | None = Field(default=None, max_length=64)

    @field_validator("due_at_utc")
    @classmethod
    def _aware_due(cls, value: datetime) -> datetime:
        result = _utc(value)
        assert result is not None
        return result

    @field_validator("scheduled_timezone", mode="before")
    @classmethod
    def _known_timezone(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Timezone must be a valid IANA zone name.") from exc
        return value


class ReminderPageQuery(_ReminderModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ReminderQuery(ReminderPageQuery):
    statuses: tuple[ReminderStatusValue, ...] = ()
    timing_states: tuple[ReminderTimingValue, ...] = ()
    link_type: ReminderLinkType = "ANY"
    linked_id: str | None = None
    due_from_utc: datetime | None = None
    due_before_utc: datetime | None = None
    include_deleted: bool = False
    sort: ReminderSort = "updated_newest"

    @field_validator("statuses", "timing_states")
    @classmethod
    def _deduplicate(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator("due_from_utc", "due_before_utc")
    @classmethod
    def _aware_due(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _valid_query(self) -> ReminderQuery:
        if (
            self.due_from_utc is not None
            and self.due_before_utc is not None
            and self.due_from_utc >= self.due_before_utc
        ):
            raise ValueError("Reminder due range must have from before before.")
        if self.link_type in {"ANY", "NONE"} and self.linked_id is not None:
            raise ValueError("linked_id requires TASK or NOTE link_type.")
        if self.timing_states and self.statuses and not set(self.statuses).intersection(
            {"PENDING", "TRIGGERED", "OVERDUE"}
        ):
            raise ValueError("Timing filters require an active reminder status.")
        return self


class ReminderSummary(_ReminderModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    status: ReminderStatusValue
    timing_state: ReminderTimingValue | None
    due_at_utc: datetime
    scheduled_timezone: str
    task_id: str | None
    note_id: str | None
    last_triggered_at_utc: datetime | None
    dismissed_at_utc: datetime | None
    completed_at_utc: datetime | None
    cancelled_at_utc: datetime | None
    snooze_count: int
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None


class ReminderView(ReminderSummary):
    message: str


class ReminderEventView(_ReminderModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    reminder_id: str
    event_type: str
    occurred_at_utc: datetime
    previous_status: ReminderStatusValue | None
    new_status: ReminderStatusValue | None
    old_due_at_utc: datetime | None
    new_due_at_utc: datetime | None
    metadata_json: str | None
