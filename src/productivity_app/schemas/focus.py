"""Public Focus/Pomodoro orchestration contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from productivity_app.schemas.timers import TimerView

FocusPhaseKind = Literal["FOCUS", "SHORT_BREAK", "LONG_BREAK"]
FocusSequenceState = Literal["ACTIVE", "COMPLETED", "CANCELLED"]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime values must be timezone-aware.")
    return value.astimezone(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateFocusSession(_Model):
    phase_kind: FocusPhaseKind
    task_id: str | None = None

    @model_validator(mode="after")
    def _breaks_unlinked(self) -> CreateFocusSession:
        if self.phase_kind != "FOCUS" and self.task_id is not None:
            raise ValueError("Break sessions cannot be linked to a task.")
        return self


class CreateFocusSequence(_Model):
    task_id: str | None = None


class AdvanceFocusSequence(_Model):
    expected_version: int = Field(ge=1)


class CancelFocusSequence(_Model):
    expected_version: int = Field(ge=1)


class FocusPageQuery(_Model):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class FocusSessionQuery(FocusPageQuery):
    phase_kinds: tuple[FocusPhaseKind, ...] = ()
    sequence_id: str | None = None

    @field_validator("phase_kinds")
    @classmethod
    def _dedupe(cls, value: tuple[FocusPhaseKind, ...]) -> tuple[FocusPhaseKind, ...]:
        return tuple(dict.fromkeys(value))


class FocusSequenceQuery(FocusPageQuery):
    states: tuple[FocusSequenceState, ...] = ()

    @field_validator("states")
    @classmethod
    def _dedupe(cls, value: tuple[FocusSequenceState, ...]) -> tuple[FocusSequenceState, ...]:
        return tuple(dict.fromkeys(value))


class FocusStatsQuery(_Model):
    started_from_utc: datetime | None = None
    started_before_utc: datetime | None = None
    task_id: str | None = None

    @field_validator("started_from_utc", "started_before_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _range(self) -> FocusStatsQuery:
        if self.started_from_utc is not None and self.started_before_utc is not None and self.started_from_utc >= self.started_before_utc:
            raise ValueError("Focus stats range must have from before before.")
        return self


class FocusSessionSummary(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    sequence_id: str | None
    phase_kind: FocusPhaseKind
    phase_index: int | None
    created_at_utc: datetime
    timer: TimerView


class FocusSessionView(FocusSessionSummary):
    pass


class FocusSequenceSummary(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    state: FocusSequenceState
    task_id: str | None
    current_phase_index: int
    work_duration_ms: int
    short_break_duration_ms: int
    long_break_duration_ms: int
    completed_at_utc: datetime | None
    cancelled_at_utc: datetime | None
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int


class FocusSequenceView(FocusSequenceSummary):
    current_session: FocusSessionView


class FocusStatsView(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    completed_focus_sessions: int = Field(ge=0)
    focused_duration_ms: int = Field(ge=0)
