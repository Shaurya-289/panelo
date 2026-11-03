"""Public commands, queries, and read models for persisted countdown timers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TimerStateValue = Literal["READY", "RUNNING", "PAUSED", "FINISHED", "CANCELLED"]
TimerRunOutcomeValue = Literal["FINISHED", "FINISHED_EARLY", "CANCELLED", "RESET"]
TimerSort = Literal[
    "updated_newest",
    "updated_oldest",
    "created_newest",
    "created_oldest",
    "name",
    "state",
    "duration_shortest",
    "duration_longest",
    "ended_newest",
]
TimerLinkType = Literal["ANY", "NONE", "TASK"]
PresetLinkType = Literal["ANY", "NONE", "PRESET"]
PresetSort = Literal["position", "name", "duration_shortest", "duration_longest"]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime values must be timezone-aware.")
    return value.astimezone(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTimer(_Model):
    name: str = Field(min_length=1, max_length=120)
    planned_duration_ms: int | None = None
    preset_id: str | None = None
    task_id: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Timer name must not be blank.")
        return value

    @field_validator("planned_duration_ms", mode="before")
    @classmethod
    def _duration_is_integer(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("Timer duration must be an integer number of milliseconds.")
        return value

    @model_validator(mode="after")
    def _source(self) -> CreateTimer:
        if self.planned_duration_ms is not None and self.planned_duration_ms <= 0:
            raise ValueError("Timer duration must be positive.")
        if self.planned_duration_ms is not None and self.preset_id is not None:
            raise ValueError("Specify either a timer preset or an explicit duration, not both.")
        return self


class UpdateTimer(_Model):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    planned_duration_ms: int | None = None
    preset_id: str | None = None
    task_id: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Timer name must not be blank.")
        return value

    @field_validator("planned_duration_ms", mode="before")
    @classmethod
    def _duration_is_integer(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("Timer duration must be an integer number of milliseconds.")
        return value

    @model_validator(mode="after")
    def _duration(self) -> UpdateTimer:
        if (
            "planned_duration_ms" in self.model_fields_set
            and self.planned_duration_ms is not None
            and self.planned_duration_ms <= 0
        ):
            raise ValueError("Timer duration must be positive.")
        if (
            "planned_duration_ms" in self.model_fields_set
            and "preset_id" in self.model_fields_set
            and self.planned_duration_ms is not None
            and self.preset_id is not None
        ):
            raise ValueError("Specify either a timer preset or an explicit duration, not both.")
        return self


class TimerPageQuery(_Model):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class TimerQuery(TimerPageQuery):
    states: tuple[TimerStateValue, ...] = ()
    effective_states: tuple[TimerStateValue, ...] = ()
    task_link: TimerLinkType = "ANY"
    task_id: str | None = None
    preset_link: PresetLinkType = "ANY"
    preset_id: str | None = None
    ended_from_utc: datetime | None = None
    ended_before_utc: datetime | None = None
    include_deleted: bool = False
    sort: TimerSort = "updated_newest"

    @field_validator("states", "effective_states")
    @classmethod
    def _dedupe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator("ended_from_utc", "ended_before_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _valid(self) -> TimerQuery:
        if (
            self.ended_from_utc is not None
            and self.ended_before_utc is not None
            and self.ended_from_utc >= self.ended_before_utc
        ):
            raise ValueError("Timer end range must have from before before.")
        if self.task_link != "TASK" and self.task_id is not None:
            raise ValueError("task_id requires TASK task_link.")
        if self.preset_link != "PRESET" and self.preset_id is not None:
            raise ValueError("preset_id requires PRESET preset_link.")
        return self


class TimerRunQuery(TimerPageQuery):
    outcomes: tuple[TimerRunOutcomeValue, ...] = ()
    task_id: str | None = None
    started_from_utc: datetime | None = None
    started_before_utc: datetime | None = None
    ended_from_utc: datetime | None = None
    ended_before_utc: datetime | None = None

    @field_validator("outcomes")
    @classmethod
    def _dedupe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator("started_from_utc", "started_before_utc", "ended_from_utc", "ended_before_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _ranges(self) -> TimerRunQuery:
        if (
            self.started_from_utc is not None
            and self.started_before_utc is not None
            and self.started_from_utc >= self.started_before_utc
        ):
            raise ValueError("Timer run start range must have from before before.")
        if (
            self.ended_from_utc is not None
            and self.ended_before_utc is not None
            and self.ended_from_utc >= self.ended_before_utc
        ):
            raise ValueError("Timer run end range must have from before before.")
        return self


class CreateTimerPreset(_Model):
    name: str = Field(min_length=1, max_length=120)
    duration_ms: int
    position: int | None = Field(default=None, ge=0)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Preset name must not be blank.")
        return value

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _duration(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Preset duration must be an integer number of milliseconds.")
        if value <= 0:
            raise ValueError("Preset duration must be positive.")
        return value


class UpdateTimerPreset(_Model):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    duration_ms: int | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Preset name must not be blank.")
        return value

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _duration(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError("Preset duration must be a positive integer number of milliseconds.")
        return value


class MoveTimerPreset(_Model):
    expected_version: int = Field(ge=1)
    position: int = Field(ge=0)


class TimerPresetQuery(TimerPageQuery):
    include_deleted: bool = False
    sort: PresetSort = "position"


class TimerSummary(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    preset_id: str | None
    task_id: str | None
    state: TimerStateValue
    effective_state: TimerStateValue
    planned_duration_ms: int
    elapsed_before_run_ms: int
    effective_elapsed_ms: int
    remaining_ms: int
    run_started_at_utc: datetime | None
    deadline_at_utc: datetime | None
    first_started_at_utc: datetime | None
    ended_at_utc: datetime | None
    needs_reconciliation: bool
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None


class TimerView(TimerSummary):
    pass


class TimerRunView(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    timer_id: str
    task_id: str | None
    planned_duration_ms: int
    actual_duration_ms: int
    started_at_utc: datetime
    ended_at_utc: datetime
    outcome: TimerRunOutcomeValue


class TimerPresetView(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    duration_ms: int
    sort_order: int
    is_builtin: bool
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None
