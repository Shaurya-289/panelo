"""Public contracts for folder operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    position: int | None = Field(default=None, ge=0)


class RenameFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)


class MoveFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: str | None = None
    position: int = Field(ge=0)
    expected_version: int = Field(ge=1)


class FolderView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    parent_id: str | None
    sort_order: int
    created_at_utc: datetime
    updated_at_utc: datetime
    version: int
    deleted_at_utc: datetime | None


class FolderTreeNode(FolderView):
    children: tuple[FolderTreeNode, ...] = ()
