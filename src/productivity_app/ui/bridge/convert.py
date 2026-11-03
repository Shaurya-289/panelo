"""Conversions between backend views and the plain dictionaries the QML pages read.

The pages work with JavaScript-friendly values: milliseconds since the epoch for every date,
lower-case words for enums, ``null`` for "none".  Everything here is pure (no Qt state beyond
asking the system for its time zone), so the mappings are unit-tested without a window.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from productivity_app.schemas.backups import BackupView
from productivity_app.schemas.notes import NoteView
from productivity_app.schemas.reminders import ReminderRepeat, ReminderSummary
from productivity_app.schemas.settings import SettingsView
from productivity_app.schemas.tasks import TaskSummary
from productivity_app.schemas.timers import TimerPresetView, TimerSummary
from productivity_app.schemas.trash import TrashItem

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MS: Final = timedelta(milliseconds=1)

# ── Time ────────────────────────────────────────────────────────────


def to_ms(moment: datetime | None) -> int | None:
    """An aware datetime as epoch milliseconds (``None`` stays ``None``)."""
    if moment is None:
        return None
    return (moment.astimezone(UTC) - _EPOCH) // _MS


def from_ms(milliseconds: float) -> datetime:
    """Epoch milliseconds as an aware UTC datetime."""
    return _EPOCH + timedelta(milliseconds=round(float(milliseconds)))


@lru_cache(maxsize=1)
def local_timezone_name() -> str:
    """The IANA name of the PC's time zone (``UTC`` when it cannot be told)."""
    try:
        from PySide6.QtCore import QTimeZone  # noqa: PLC0415

        name = bytes(QTimeZone.systemTimeZoneId().data()).decode("ascii", "ignore")
        ZoneInfo(name)
        return name
    except (ImportError, ValueError, ZoneInfoNotFoundError, OSError):
        return "UTC"


def local_zone() -> ZoneInfo:
    return ZoneInfo(local_timezone_name())


# ── Notes, tasks, reminders ─────────────────────────────────────────


def note_dict(note: NoteView) -> dict[str, Any]:
    return {
        "id": note.id,
        "title": note.title,
        "body": note.content,
        "tint": note.color,
        "pinned": note.is_pinned,
        "archived": note.is_archived,
        "createdAt": to_ms(note.created_at_utc),
        "updatedAt": to_ms(note.updated_at_utc),
        "deletedAt": to_ms(note.deleted_at_utc),
    }


def task_dict(task: TaskSummary) -> dict[str, Any]:
    due = to_ms(task.due_at_utc)
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "due": due,
        "hasTime": bool(task.due_has_time) and due is not None,
        "priority": task.priority.lower(),
        "done": task.status == "COMPLETED",
        "doneAt": to_ms(task.completed_at_utc),
        "createdAt": to_ms(task.created_at_utc),
        "deletedAt": to_ms(task.deleted_at_utc),
        "status": task.status,
    }


_RULE_KINDS: Final = {
    "daily": "DAILY",
    "weekdays": "WEEKDAYS",
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
    "yearly": "YEARLY",
    "every": "EVERY",
}


def rule_from_qml(rule: Mapping[str, Any] | None) -> ReminderRepeat | None:
    """A QML repeat rule (``{kind: "weekly", days: [1, 3]}``) as the backend schema.

    ``None`` (or anything that is not a usable rule) means "never repeats".
    """
    if not rule:
        return None
    kind = _RULE_KINDS.get(str(rule.get("kind", "")))
    try:
        if kind == "WEEKLY":
            return ReminderRepeat(kind="WEEKLY", days=tuple(int(d) for d in rule.get("days") or ()))
        if kind == "MONTHLY":
            return ReminderRepeat(kind="MONTHLY", day_of_month=int(rule["day"]))
        if kind == "EVERY":
            unit = str(rule.get("unit", "")).upper()
            return ReminderRepeat(
                kind="EVERY",
                every_n=int(rule["n"]),
                every_unit=unit,  # type: ignore[arg-type]
            )
        if kind in {"DAILY", "WEEKDAYS", "YEARLY"}:
            return ReminderRepeat(kind=kind)  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return None
    return None


def rule_to_qml(rule: ReminderRepeat | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    kind = rule.kind.lower()
    if rule.kind == "WEEKLY":
        return {"kind": kind, "days": list(rule.days)}
    if rule.kind == "MONTHLY":
        return {"kind": kind, "day": rule.day_of_month}
    if rule.kind == "EVERY":
        assert rule.every_unit is not None
        return {"kind": kind, "n": rule.every_n, "unit": rule.every_unit.lower()}
    return {"kind": kind}


# A reminder that is over: Done or dismissed (it shows under "Past").
PAST_STATUSES: Final = frozenset({"COMPLETED", "DISMISSED"})
ACTIVE_STATUSES: Final = frozenset({"PENDING", "TRIGGERED", "OVERDUE"})


def reminder_dict(reminder: ReminderSummary) -> dict[str, Any]:
    due = to_ms(reminder.due_at_utc)
    done = reminder.status in PAST_STATUSES
    link: dict[str, Any] | None = None
    if reminder.task_id is not None:
        link = {"kind": "task", "id": reminder.task_id}
    elif reminder.note_id is not None:
        link = {"kind": "note", "id": reminder.note_id}
    return {
        "id": reminder.id,
        "title": reminder.title,
        "message": reminder.message,
        "due": due,
        "done": done,
        "doneAt": to_ms(reminder.completed_at_utc or reminder.dismissed_at_utc) if done else None,
        "snoozeCount": reminder.snooze_count,
        "createdAt": to_ms(reminder.created_at_utc),
        "link": link,
        "repeat": rule_to_qml(reminder.repeat),
        "anchor": to_ms(reminder.anchor_at_utc) if reminder.repeat else due,
        "timesDone": reminder.times_done,
        "status": reminder.status,
    }


# ── Timers, presets, trash, backups ─────────────────────────────────


def timer_dict(timer: TimerSummary) -> dict[str, Any]:
    running = timer.state == "RUNNING"
    finished = timer.effective_state == "FINISHED"
    return {
        "id": timer.id,
        "name": timer.name,
        "total": round(timer.planned_duration_ms / 1000),
        "remaining": 0 if finished else math.ceil(timer.remaining_ms / 1000),
        "running": running and not finished,
        "deadline": to_ms(timer.deadline_at_utc) if running else None,
        "state": timer.state,
    }


def preset_label(duration_ms: int) -> str:
    minutes = round(duration_ms / 60000)
    return f"{minutes // 60}h" if minutes >= 60 and minutes % 60 == 0 else f"{minutes}m"


def preset_dict(preset: TimerPresetView) -> dict[str, Any]:
    return {"id": preset.id, "label": preset.name, "ms": preset.duration_ms}


def preset_timer_name(duration_ms: int) -> str:
    minutes = round(duration_ms / 60000)
    return "1 h timer" if minutes == 60 else f"{minutes} min timer"


def trash_dict(item: TrashItem) -> dict[str, Any]:
    title = item.title.strip() or item.preview
    if not title and item.kind == "NOTE":
        title = "Empty note"
    return {
        "kind": item.kind.lower(),
        "id": item.id,
        "title": title,
        "deletedAt": to_ms(item.deleted_at_utc),
        "version": item.version,
    }


def backup_dict(backup: BackupView) -> dict[str, Any]:
    return {
        "id": backup.filename,
        "at": to_ms(backup.created_at_utc),
        "sizeMb": round(backup.size_bytes / 1048576, 2),
        "kind": backup.kind.lower(),
    }


# ── Settings ────────────────────────────────────────────────────────

# QML key -> (backend field, to QML, to backend)
_SETTING_MAP: Final[dict[str, tuple[str, Any, Any]]] = {
    "startWithWindows": ("start_with_windows", bool, bool),
    "closeHidesToTray": ("minimize_to_tray", bool, bool),
    "hotkey": ("global_hotkey", str, str),
    "focusMs": ("pomodoro_work_duration_ms", int, lambda v: max(1000, int(round(v)))),
    "shortMs": ("short_break_duration_ms", int, lambda v: max(1000, int(round(v)))),
    "longMs": ("long_break_duration_ms", int, lambda v: max(1000, int(round(v)))),
    "reminderAlerts": ("notifications_enabled", bool, bool),
    "notificationStyle": ("notification_style", lambda v: str(v.value).lower(), lambda v: str(v).upper()),
    "notificationSound": ("notification_sound", str, str),
    "backdropBlur": ("backdrop_blur", lambda v: v / 100, lambda v: min(100, max(0, round(v * 100)))),
    "backdropDim": ("backdrop_dim", lambda v: v / 100, lambda v: min(100, max(0, round(v * 100)))),
    "overlaySize": ("overlay_size_pct", lambda v: v / 100, lambda v: min(90, max(60, round(v * 100)))),
    "autoBackup": ("auto_backup", bool, bool),
    "keepBackups": ("keep_backups", int, lambda v: min(60, max(1, int(round(v))))),
    "libraryPath": ("library_path", str, str),
    "userName": ("user_name", str, lambda v: str(v)[:60]),
    "tourDone": ("tour_done", bool, bool),
    "overlayLayoutJson": ("overlay_layout_json", str, str),
    "filesViewMode": ("files_view_mode", lambda v: str(v.value).lower(), lambda v: str(v).upper()),
}

SETTING_KEYS: Final = tuple(_SETTING_MAP)


def settings_dict(settings: SettingsView) -> dict[str, Any]:
    return {
        key: to_qml(getattr(settings, field)) for key, (field, to_qml, _) in _SETTING_MAP.items()
    }


def setting_field(key: str, value: Any) -> tuple[str, Any]:
    """The backend patch field and value for a QML setting (``KeyError`` for an unknown key)."""
    field, _, to_backend = _SETTING_MAP[key]
    return field, to_backend(value)
