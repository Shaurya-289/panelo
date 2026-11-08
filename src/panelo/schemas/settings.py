"""Typed public settings contracts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HOTKEY_PART = re.compile(r"^(Ctrl|Alt|Shift|Win|[A-Z0-9]|F(?:[1-9]|1[0-2])|Space)$")


class Theme(StrEnum):
    SYSTEM = "SYSTEM"
    LIGHT = "LIGHT"
    DARK = "DARK"


class NotificationStyle(StrEnum):
    TOAST = "TOAST"
    WINDOWS = "WINDOWS"
    BOTH = "BOTH"


class FilesViewMode(StrEnum):
    LIST = "LIST"
    GRID = "GRID"


class SettingsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    theme: Theme
    global_hotkey: str = Field(min_length=3, max_length=120)
    start_with_windows: bool
    minimize_to_tray: bool
    notifications_enabled: bool
    default_timer_duration_ms: int = Field(ge=1_000, le=86_400_000)
    pomodoro_work_duration_ms: int = Field(ge=1_000, le=86_400_000)
    short_break_duration_ms: int = Field(ge=1_000, le=86_400_000)
    long_break_duration_ms: int = Field(ge=1_000, le=86_400_000)
    user_name: str = Field(max_length=60)
    tour_done: bool
    notification_style: NotificationStyle
    notification_sound: str = Field(max_length=260)
    backdrop_blur: int = Field(ge=0, le=100)
    backdrop_dim: int = Field(ge=0, le=100)
    overlay_size_pct: int = Field(ge=60, le=90)
    overlay_layout_json: str
    files_view_mode: FilesViewMode
    library_path: str = Field(max_length=1024)
    auto_backup: bool
    keep_backups: int = Field(ge=1, le=60)
    last_auto_backup_at_utc: datetime | None
    updated_at_utc: datetime
    version: int = Field(ge=1)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    theme: Theme | None = None
    global_hotkey: str | None = Field(default=None, min_length=3, max_length=120)
    start_with_windows: bool | None = None
    minimize_to_tray: bool | None = None
    notifications_enabled: bool | None = None
    default_timer_duration_ms: int | None = Field(default=None, ge=1_000, le=86_400_000)
    pomodoro_work_duration_ms: int | None = Field(default=None, ge=1_000, le=86_400_000)
    short_break_duration_ms: int | None = Field(default=None, ge=1_000, le=86_400_000)
    long_break_duration_ms: int | None = Field(default=None, ge=1_000, le=86_400_000)
    user_name: str | None = Field(default=None, max_length=60)
    tour_done: bool | None = None
    notification_style: NotificationStyle | None = None
    notification_sound: str | None = Field(default=None, max_length=260)
    backdrop_blur: int | None = Field(default=None, ge=0, le=100)
    backdrop_dim: int | None = Field(default=None, ge=0, le=100)
    overlay_size_pct: int | None = Field(default=None, ge=60, le=90)
    overlay_layout_json: str | None = None
    files_view_mode: FilesViewMode | None = None
    library_path: str | None = Field(default=None, max_length=1024)
    auto_backup: bool | None = None
    keep_backups: int | None = Field(default=None, ge=1, le=60)

    @field_validator("global_hotkey")
    @classmethod
    def validate_hotkey(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split("+")
        if (
            len(parts) < 2
            or len(parts) != len(set(parts))
            or not all(_HOTKEY_PART.fullmatch(part) for part in parts)
        ):
            raise ValueError("Hotkey must use canonical parts such as Ctrl+Alt+Space.")
        if parts[-1] in {"Ctrl", "Alt", "Shift", "Win"}:
            raise ValueError("Hotkey must end with a non-modifier key.")
        return "+".join(parts)
