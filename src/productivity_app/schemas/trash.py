"""Public contracts for the Trash (soft-deleted notes and tasks)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

TrashKind = Literal["NOTE", "TASK"]


class TrashItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TrashKind
    id: str
    # The stored title; a note may have none, so ``preview`` is the first line of its text.
    title: str
    preview: str
    deleted_at_utc: datetime
    # What ``restore_note`` / ``restore_task`` expect as ``expected_version``.
    version: int
