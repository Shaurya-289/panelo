"""Public read-only unified-search contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SearchEntityType = Literal["TASK", "NOTE", "REMINDER", "TIMER", "FOLDER", "TAG", "TIMER_PRESET"]
SearchMatchedField = Literal["title", "name", "description", "content", "message"]
FullTextQueryMode = Literal["TERMS", "PREFIX", "PHRASE"]


class UnifiedSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200)
    entity_types: tuple[SearchEntityType, ...] = ()
    include_deleted: bool = False
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("query", mode="before")
    @classmethod
    def _trim_query(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Search query must not be blank.")
        return value

    @field_validator("entity_types")
    @classmethod
    def _dedupe(cls, value: tuple[SearchEntityType, ...]) -> tuple[SearchEntityType, ...]:
        return tuple(dict.fromkeys(value))


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: SearchEntityType
    entity_id: str
    title: str
    matched_field: SearchMatchedField
    persisted_state: str | None
    derived_state: str | None
    updated_at_utc: datetime
    is_deleted: bool


class FullTextSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=200)
    entity_types: tuple[SearchEntityType, ...] = ()
    include_deleted: bool = False
    mode: FullTextQueryMode = "PREFIX"
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("text", mode="before")
    @classmethod
    def _trim_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Full-text search query must not be blank.")
        return value

    @field_validator("entity_types")
    @classmethod
    def _dedupe(cls, value: tuple[SearchEntityType, ...]) -> tuple[SearchEntityType, ...]:
        return tuple(dict.fromkeys(value))
