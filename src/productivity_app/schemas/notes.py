"""Public contracts for notes, listing, and search."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NoteColor = Literal["none", "plum", "rose", "amber", "moss", "teal", "slate", "sand"]


class CreateNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=300)
    content: str = Field(default="")
    is_pinned: bool = False
    color: NoteColor = "none"


class UpdateNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    content: str | None = None
    is_pinned: bool | None = None
    is_archived: bool | None = None
    color: NoteColor | None = None


class NoteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_pinned: bool | None = None
    is_archived: bool | None = None
    include_deleted: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    sort: Literal[
        "updated_desc", "updated_asc", "created_desc", "created_asc", "title", "pinned_first"
    ] = "updated_desc"


class NoteSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    include_archived: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class NoteView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    title: str
    content: str
    is_pinned: bool
    is_archived: bool
    color: NoteColor
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None


class NoteSummary(NoteView):
    content: str
