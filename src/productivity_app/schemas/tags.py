"""Public contracts for tags."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateTag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class RenameTag(CreateTag):
    expected_version: int = Field(ge=1)


class TagView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None
