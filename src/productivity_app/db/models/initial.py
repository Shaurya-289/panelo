"""Persistence mappings. Domain behavior belongs in the services."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from productivity_app.db.base import Base, MutableEntity
from productivity_app.db.types import UTCDateTime

DEFAULT_SETTINGS_VALUES: dict[str, object] = {
    "theme": "SYSTEM",
    "global_hotkey": "Ctrl+Alt+Space",
    "start_with_windows": False,
    "minimize_to_tray": True,
    "notifications_enabled": True,
    "default_timer_duration_ms": 1_500_000,
    "pomodoro_work_duration_ms": 1_500_000,
    "short_break_duration_ms": 300_000,
    "long_break_duration_ms": 900_000,
    "user_name": "",
    "tour_done": False,
    "notification_style": "TOAST",
    "notification_sound": "",
    "backdrop_blur": 100,
    "backdrop_dim": 45,
    "overlay_size_pct": 85,
    "overlay_layout_json": "",
    "files_view_mode": "LIST",
    "library_path": "",
    "auto_backup": True,
    "keep_backups": 7,
}


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class ReminderStatus(StrEnum):
    PENDING = "PENDING"
    TRIGGERED = "TRIGGERED"
    OVERDUE = "OVERDUE"
    DISMISSED = "DISMISSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TimerState(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class Note(MutableEntity, Base):
    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint("content_format = 'PLAIN_TEXT'", name="ck_notes_content_format"),
        CheckConstraint("is_pinned IN (0, 1)", name="ck_notes_is_pinned_bool"),
        CheckConstraint("is_archived IN (0, 1)", name="ck_notes_is_archived_bool"),
        CheckConstraint(
            "color IN ('none', 'plum', 'rose', 'amber', 'moss', 'teal', 'slate', 'sand')",
            name="ck_notes_color_values",
        ),
        Index("ix_notes_list", "deleted_at_utc", "is_archived", "is_pinned", "updated_at_utc"),
        Index("ix_notes_title", "title"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_format: Mapped[str] = mapped_column(String(32), nullable=False, default="PLAIN_TEXT")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    color: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )


class Task(MutableEntity, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 300", name="ck_tasks_title_length"),
        CheckConstraint(
            "start_at_utc IS NULL OR due_at_utc IS NULL OR start_at_utc <= due_at_utc",
            name="ck_tasks_start_before_due",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')", name="ck_tasks_priority_values"
        ),
        CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_tasks_status_values",
        ),
        CheckConstraint(
            "(status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND cancelled_at_utc IS NULL) "
            "OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND completed_at_utc IS NULL) "
            "OR (status IN ('TODO', 'IN_PROGRESS') AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)",
            name="ck_tasks_terminal_timestamps",
        ),
        CheckConstraint("due_has_time IN (0, 1)", name="ck_tasks_due_has_time_bool"),
        Index("ix_tasks_status_due", "deleted_at_utc", "status", "due_at_utc"),
        Index("ix_tasks_priority_status", "priority", "status", "deleted_at_utc"),
        Index("ix_tasks_completed", "completed_at_utc", "deleted_at_utc"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskStatus.TODO.value)
    priority: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TaskPriority.NORMAL.value
    )
    start_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    due_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    due_has_time: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )


class Reminder(MutableEntity, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 300", name="ck_reminders_title_length"),
        CheckConstraint(
            "NOT (task_id IS NOT NULL AND note_id IS NOT NULL)", name="ck_reminders_one_link"
        ),
        CheckConstraint("snooze_count >= 0", name="ck_reminders_snooze_count"),
        CheckConstraint(
            "status IN ('PENDING', 'TRIGGERED', 'OVERDUE', 'DISMISSED', 'COMPLETED', 'CANCELLED')",
            name="ck_reminders_status_values",
        ),
        CheckConstraint(
            "(status = 'DISMISSED' AND dismissed_at_utc IS NOT NULL AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL) "
            "OR (status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND dismissed_at_utc IS NULL AND cancelled_at_utc IS NULL) "
            "OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND dismissed_at_utc IS NULL AND completed_at_utc IS NULL) "
            "OR (status IN ('PENDING', 'TRIGGERED', 'OVERDUE') AND dismissed_at_utc IS NULL AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)",
            name="ck_reminders_terminal_timestamps",
        ),
        CheckConstraint(
            "repeat_kind IS NULL OR repeat_kind IN "
            "('DAILY', 'WEEKDAYS', 'WEEKLY', 'MONTHLY', 'YEARLY', 'EVERY')",
            name="ck_reminders_repeat_kind_values",
        ),
        CheckConstraint(
            "repeat_every_unit IS NULL OR repeat_every_unit IN ('HOURS', 'DAYS', 'WEEKS')",
            name="ck_reminders_repeat_unit_values",
        ),
        CheckConstraint(
            "(repeat_kind IS NULL AND repeat_days IS NULL AND repeat_day_of_month IS NULL "
            "AND repeat_every_n IS NULL AND repeat_every_unit IS NULL AND anchor_at_utc IS NULL) "
            "OR (repeat_kind IS NOT NULL AND anchor_at_utc IS NOT NULL AND ("
            "(repeat_kind IN ('DAILY', 'WEEKDAYS', 'YEARLY') AND repeat_days IS NULL "
            "AND repeat_day_of_month IS NULL AND repeat_every_n IS NULL "
            "AND repeat_every_unit IS NULL) "
            "OR (repeat_kind = 'WEEKLY' AND repeat_days IS NOT NULL "
            "AND repeat_days BETWEEN 1 AND 127 AND repeat_day_of_month IS NULL "
            "AND repeat_every_n IS NULL AND repeat_every_unit IS NULL) "
            "OR (repeat_kind = 'MONTHLY' AND repeat_day_of_month IS NOT NULL "
            "AND repeat_day_of_month BETWEEN 1 AND 31 AND repeat_days IS NULL "
            "AND repeat_every_n IS NULL AND repeat_every_unit IS NULL) "
            "OR (repeat_kind = 'EVERY' AND repeat_every_n IS NOT NULL AND repeat_every_n >= 1 "
            "AND repeat_every_unit IS NOT NULL AND repeat_days IS NULL "
            "AND repeat_day_of_month IS NULL)))",
            name="ck_reminders_repeat_shape",
        ),
        Index("ix_reminders_scheduler", "status", "due_at_utc"),
        Index("ix_reminders_task", "task_id"),
        Index("ix_reminders_note", "note_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    scheduled_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReminderStatus.PENDING.value
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    note_id: Mapped[str | None] = mapped_column(ForeignKey("notes.id", ondelete="SET NULL"))
    last_triggered_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dismissed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repeat_kind: Mapped[str | None] = mapped_column(String(16))
    repeat_days: Mapped[int | None] = mapped_column(Integer)
    repeat_day_of_month: Mapped[int | None] = mapped_column(Integer)
    repeat_every_n: Mapped[int | None] = mapped_column(Integer)
    repeat_every_unit: Mapped[str | None] = mapped_column(String(8))
    anchor_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ReminderEvent(Base):
    __tablename__ = "reminder_events"
    __table_args__ = (Index("ix_reminder_events_history", "reminder_id", "occurred_at_utc"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reminder_id: Mapped[str] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str | None] = mapped_column(String(32))
    old_due_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    new_due_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metadata_json: Mapped[str | None] = mapped_column(Text)


class TimerPreset(MutableEntity, Base):
    __tablename__ = "timer_presets"
    __table_args__ = (
        CheckConstraint("duration_ms > 0", name="ck_timer_presets_positive_duration"),
        CheckConstraint("sort_order >= 0", name="ck_timer_presets_sort_order"),
        CheckConstraint("is_builtin IN (0, 1)", name="ck_timer_presets_is_builtin_bool"),
        Index(
            "ux_timer_presets_active_name",
            "normalized_name",
            unique=True,
            sqlite_where=text("deleted_at_utc IS NULL"),
        ),
        Index(
            "ux_timer_presets_active_position",
            "sort_order",
            unique=True,
            sqlite_where=text("deleted_at_utc IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Timer(MutableEntity, Base):
    __tablename__ = "timers"
    __table_args__ = (
        CheckConstraint("planned_duration_ms > 0", name="ck_timers_positive_duration"),
        CheckConstraint(
            "elapsed_before_run_ms >= 0 AND elapsed_before_run_ms <= planned_duration_ms",
            name="ck_timers_elapsed_range",
        ),
        CheckConstraint(
            "state IN ('READY', 'RUNNING', 'PAUSED', 'FINISHED', 'CANCELLED')",
            name="ck_timers_state_values",
        ),
        CheckConstraint(
            "(state = 'RUNNING' AND run_started_at_utc IS NOT NULL AND deadline_at_utc IS NOT NULL AND ended_at_utc IS NULL) "
            "OR (state = 'PAUSED' AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND ended_at_utc IS NULL) "
            "OR (state = 'READY' AND elapsed_before_run_ms = 0 AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND first_started_at_utc IS NULL AND ended_at_utc IS NULL) "
            "OR (state IN ('FINISHED', 'CANCELLED') AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND ended_at_utc IS NOT NULL)",
            name="ck_timers_state_timestamps",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    preset_id: Mapped[str | None] = mapped_column(
        ForeignKey("timer_presets.id", ondelete="SET NULL")
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=TimerState.READY.value)
    planned_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_before_run_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_started_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deadline_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_started_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TimerRun(Base):
    __tablename__ = "timer_runs"
    __table_args__ = (
        CheckConstraint("planned_duration_ms > 0", name="ck_timer_runs_positive_duration"),
        CheckConstraint("actual_duration_ms >= 0", name="ck_timer_runs_actual_duration"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('FINISHED', 'FINISHED_EARLY', 'CANCELLED', 'RESET')",
            name="ck_timer_runs_outcome_values",
        ),
        CheckConstraint(
            "(ended_at_utc IS NULL AND outcome IS NULL) OR (ended_at_utc IS NOT NULL AND outcome IS NOT NULL)",
            name="ck_timer_runs_end_outcome",
        ),
        Index("ix_timer_runs_timer_started", "timer_id", "started_at_utc"),
        Index("ix_timer_runs_task_started", "task_id", "started_at_utc"),
        Index(
            "ux_timer_runs_one_open",
            "timer_id",
            unique=True,
            sqlite_where=text("ended_at_utc IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timer_id: Mapped[str] = mapped_column(
        ForeignKey("timers.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    planned_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str | None] = mapped_column(String(32))


class AppSettings(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_app_settings_singleton"),
        CheckConstraint("theme IN ('SYSTEM', 'LIGHT', 'DARK')", name="ck_settings_theme_values"),
        CheckConstraint("version >= 1", name="ck_settings_version_positive"),
        CheckConstraint("start_with_windows IN (0, 1)", name="ck_settings_start_with_windows_bool"),
        CheckConstraint("minimize_to_tray IN (0, 1)", name="ck_settings_minimize_to_tray_bool"),
        CheckConstraint(
            "notifications_enabled IN (0, 1)", name="ck_settings_notifications_enabled_bool"
        ),
        CheckConstraint(
            "default_timer_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_default_timer"
        ),
        CheckConstraint(
            "pomodoro_work_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_work_timer"
        ),
        CheckConstraint(
            "short_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_short_break"
        ),
        CheckConstraint(
            "long_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_long_break"
        ),
        CheckConstraint("length(user_name) <= 60", name="ck_settings_user_name_length"),
        CheckConstraint("tour_done IN (0, 1)", name="ck_settings_tour_done_bool"),
        CheckConstraint(
            "notification_style IN ('TOAST', 'WINDOWS', 'BOTH')",
            name="ck_settings_notification_style_values",
        ),
        CheckConstraint(
            "length(notification_sound) <= 260", name="ck_settings_notification_sound_length"
        ),
        CheckConstraint("backdrop_blur BETWEEN 0 AND 100", name="ck_settings_backdrop_blur"),
        CheckConstraint("backdrop_dim BETWEEN 0 AND 100", name="ck_settings_backdrop_dim"),
        CheckConstraint("overlay_size_pct BETWEEN 60 AND 90", name="ck_settings_overlay_size"),
        CheckConstraint(
            "files_view_mode IN ('LIST', 'GRID')", name="ck_settings_files_view_mode_values"
        ),
        CheckConstraint("length(library_path) <= 1024", name="ck_settings_library_path_length"),
        CheckConstraint("auto_backup IN (0, 1)", name="ck_settings_auto_backup_bool"),
        CheckConstraint("keep_backups BETWEEN 1 AND 60", name="ck_settings_keep_backups"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="SYSTEM")
    global_hotkey: Mapped[str] = mapped_column(
        String(120), nullable=False, default="Ctrl+Alt+Space"
    )
    start_with_windows: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minimize_to_tray: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_timer_duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_500_000
    )
    pomodoro_work_duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_500_000
    )
    short_break_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=300_000)
    long_break_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=900_000)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_name: Mapped[str] = mapped_column(
        String(60), nullable=False, default="", server_default=text("''")
    )
    tour_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    notification_style: Mapped[str] = mapped_column(
        String(16), nullable=False, default="TOAST", server_default="TOAST"
    )
    notification_sound: Mapped[str] = mapped_column(
        String(260), nullable=False, default="", server_default=text("''")
    )
    backdrop_blur: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )
    backdrop_dim: Mapped[int] = mapped_column(
        Integer, nullable=False, default=45, server_default=text("45")
    )
    overlay_size_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=85, server_default=text("85")
    )
    overlay_layout_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    files_view_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, default="LIST", server_default="LIST"
    )
    library_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="", server_default=text("''")
    )
    auto_backup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    keep_backups: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7, server_default=text("7")
    )
    last_auto_backup_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_deliveries_dedupe_key"),
        CheckConstraint(
            "status IN ('PENDING', 'IN_FLIGHT', 'DELIVERED', 'FAILED')",
            name="ck_notification_deliveries_status_values",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_notification_deliveries_attempt_count"),
        Index("ix_notification_deliveries_status_available", "status", "available_at_utc"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_until_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    provider_message_id: Mapped[str | None] = mapped_column(String(256))
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    delivered_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ExternalNotification(Base):
    __tablename__ = "external_notifications"
    __table_args__ = (
        UniqueConstraint(
            "source", "source_device_id", "external_id", name="uq_external_notifications_source"
        ),
        CheckConstraint("is_read IN (0, 1)", name="ck_external_notifications_is_read_bool"),
        CheckConstraint(
            "is_dismissed IN (0, 1)", name="ck_external_notifications_is_dismissed_bool"
        ),
        Index("ix_external_notifications_received", "received_at_utc", "is_dismissed"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    app_name: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
