"""Public contracts for manual local SQLite backups."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BackupKind = Literal["MANUAL", "PRE_RESTORE"]


class BackupPageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class DeleteBackup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    filename: str = Field(min_length=1, max_length=255)


class ValidateBackup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_path: Path


class RestoreBackup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_path: Path


class BackupView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    filename: str
    kind: BackupKind
    created_at_utc: datetime
    size_bytes: int


class BackupValidationView(BackupView):
    sha256: str
    schema_revision: str
    integrity_status: Literal["OK"] = "OK"


class BackupRestoreView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_filename: str
    source_sha256: str
    safety_backup: BackupView
    completed_at_utc: datetime
