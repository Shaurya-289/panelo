"""Create the immutable Phase 0 database schema.

Revision ID: 20260924_0001
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mutable() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
        sa.CheckConstraint("sort_order >= 0", name="ck_folders_sort_order_nonnegative"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id != id", name="ck_folders_no_self_parent"
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ux_folders_active_root_name",
        "folders",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL AND deleted_at_utc IS NULL"),
    )
    op.create_index(
        "ux_folders_active_child_name",
        "folders",
        ["parent_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL AND deleted_at_utc IS NULL"),
    )
    op.create_index(
        "ux_folders_active_root_position",
        "folders",
        ["sort_order"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL AND deleted_at_utc IS NULL"),
    )
    op.create_index(
        "ux_folders_active_child_position",
        "folders",
        ["parent_id", "sort_order"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL AND deleted_at_utc IS NULL"),
    )
    op.create_index(
        "ix_folders_parent_active_position",
        "folders",
        ["parent_id", "deleted_at_utc", "sort_order"],
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
    )
    op.create_index(
        "ux_tags_active_normalized_name",
        "tags",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("deleted_at_utc IS NULL"),
    )

    op.create_table(
        "notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_format", sa.String(32), nullable=False),
        sa.Column("folder_id", sa.String(36)),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
        sa.CheckConstraint("content_format = 'PLAIN_TEXT'", name="ck_notes_content_format"),
        sa.CheckConstraint("is_pinned IN (0, 1)", name="ck_notes_is_pinned_bool"),
        sa.CheckConstraint("is_archived IN (0, 1)", name="ck_notes_is_archived_bool"),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_notes_list", "notes", ["deleted_at_utc", "is_archived", "is_pinned", "updated_at_utc"]
    )
    op.create_index(
        "ix_notes_folder_list", "notes", ["folder_id", "deleted_at_utc", "updated_at_utc"]
    )
    op.create_index("ix_notes_title", "notes", ["title"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(32), nullable=False),
        sa.Column("folder_id", sa.String(36)),
        sa.Column("start_at_utc", sa.BigInteger()),
        sa.Column("due_at_utc", sa.BigInteger()),
        sa.Column("completed_at_utc", sa.BigInteger()),
        sa.Column("cancelled_at_utc", sa.BigInteger()),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 300", name="ck_tasks_title_length"),
        sa.CheckConstraint(
            "start_at_utc IS NULL OR due_at_utc IS NULL OR start_at_utc <= due_at_utc",
            name="ck_tasks_start_before_due",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')", name="ck_tasks_priority_values"
        ),
        sa.CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_tasks_status_values",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND cancelled_at_utc IS NULL) OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND completed_at_utc IS NULL) OR (status IN ('TODO', 'IN_PROGRESS') AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)",
            name="ck_tasks_terminal_timestamps",
        ),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tasks_status_due", "tasks", ["deleted_at_utc", "status", "due_at_utc"])
    op.create_index("ix_tasks_folder_status", "tasks", ["folder_id", "deleted_at_utc", "status"])
    op.create_index("ix_tasks_priority_status", "tasks", ["priority", "status", "deleted_at_utc"])
    op.create_index("ix_tasks_completed", "tasks", ["completed_at_utc", "deleted_at_utc"])

    op.create_table(
        "note_tags",
        sa.Column("note_id", sa.String(36), nullable=False),
        sa.Column("tag_id", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint("note_id", "tag_id"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("tag_id", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint("task_id", "tag_id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "reminders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("due_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("note_id", sa.String(36)),
        sa.Column("last_triggered_at_utc", sa.BigInteger()),
        sa.Column("dismissed_at_utc", sa.BigInteger()),
        sa.Column("completed_at_utc", sa.BigInteger()),
        sa.Column("cancelled_at_utc", sa.BigInteger()),
        sa.Column("snooze_count", sa.Integer(), nullable=False),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 300", name="ck_reminders_title_length"),
        sa.CheckConstraint(
            "NOT (task_id IS NOT NULL AND note_id IS NOT NULL)", name="ck_reminders_one_link"
        ),
        sa.CheckConstraint("snooze_count >= 0", name="ck_reminders_snooze_count"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'TRIGGERED', 'OVERDUE', 'DISMISSED', 'COMPLETED', 'CANCELLED')",
            name="ck_reminders_status_values",
        ),
        sa.CheckConstraint(
            "(status = 'DISMISSED' AND dismissed_at_utc IS NOT NULL AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL) OR (status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND dismissed_at_utc IS NULL AND cancelled_at_utc IS NULL) OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND dismissed_at_utc IS NULL AND completed_at_utc IS NULL) OR (status IN ('PENDING', 'TRIGGERED', 'OVERDUE') AND dismissed_at_utc IS NULL AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)",
            name="ck_reminders_terminal_timestamps",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_reminders_scheduler", "reminders", ["status", "due_at_utc", "deleted_at_utc"]
    )
    op.create_index("ix_reminders_task", "reminders", ["task_id"])
    op.create_index("ix_reminders_note", "reminders", ["note_id"])

    op.create_table(
        "reminder_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reminder_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("occurred_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("previous_status", sa.String(32)),
        sa.Column("new_status", sa.String(32)),
        sa.Column("old_due_at_utc", sa.BigInteger()),
        sa.Column("new_due_at_utc", sa.BigInteger()),
        sa.Column("metadata_json", sa.Text()),
        sa.ForeignKeyConstraint(["reminder_id"], ["reminders.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_reminder_events_history", "reminder_events", ["reminder_id", "occurred_at_utc"]
    )

    op.create_table(
        "timer_presets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
        sa.CheckConstraint("duration_ms > 0", name="ck_timer_presets_positive_duration"),
        sa.CheckConstraint("sort_order >= 0", name="ck_timer_presets_sort_order"),
        sa.CheckConstraint("is_builtin IN (0, 1)", name="ck_timer_presets_is_builtin_bool"),
    )
    op.create_index(
        "ux_timer_presets_active_name",
        "timer_presets",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("deleted_at_utc IS NULL"),
    )
    op.create_index(
        "ux_timer_presets_active_position",
        "timer_presets",
        ["sort_order"],
        unique=True,
        sqlite_where=sa.text("deleted_at_utc IS NULL"),
    )

    op.create_table(
        "timers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("preset_id", sa.String(36)),
        sa.Column("task_id", sa.String(36)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("planned_duration_ms", sa.Integer(), nullable=False),
        sa.Column("elapsed_before_run_ms", sa.Integer(), nullable=False),
        sa.Column("run_started_at_utc", sa.BigInteger()),
        sa.Column("deadline_at_utc", sa.BigInteger()),
        sa.Column("first_started_at_utc", sa.BigInteger()),
        sa.Column("ended_at_utc", sa.BigInteger()),
        sa.Column("deleted_at_utc", sa.BigInteger()),
        *_mutable(),
        sa.CheckConstraint("planned_duration_ms > 0", name="ck_timers_positive_duration"),
        sa.CheckConstraint(
            "elapsed_before_run_ms >= 0 AND elapsed_before_run_ms <= planned_duration_ms",
            name="ck_timers_elapsed_range",
        ),
        sa.CheckConstraint(
            "state IN ('READY', 'RUNNING', 'PAUSED', 'FINISHED', 'CANCELLED')",
            name="ck_timers_state_values",
        ),
        sa.CheckConstraint(
            "(state = 'RUNNING' AND run_started_at_utc IS NOT NULL AND deadline_at_utc IS NOT NULL AND ended_at_utc IS NULL) OR (state = 'PAUSED' AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND ended_at_utc IS NULL) OR (state = 'READY' AND elapsed_before_run_ms = 0 AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND first_started_at_utc IS NULL AND ended_at_utc IS NULL) OR (state IN ('FINISHED', 'CANCELLED') AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND ended_at_utc IS NOT NULL)",
            name="ck_timers_state_timestamps",
        ),
        sa.ForeignKeyConstraint(["preset_id"], ["timer_presets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ux_timers_one_active",
        "timers",
        [sa.literal_column("1")],
        unique=True,
        sqlite_where=sa.text("state IN ('RUNNING', 'PAUSED') AND deleted_at_utc IS NULL"),
    )

    op.create_table(
        "timer_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timer_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("planned_duration_ms", sa.Integer(), nullable=False),
        sa.Column("actual_duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("ended_at_utc", sa.BigInteger()),
        sa.Column("outcome", sa.String(32)),
        sa.CheckConstraint("planned_duration_ms > 0", name="ck_timer_runs_positive_duration"),
        sa.CheckConstraint("actual_duration_ms >= 0", name="ck_timer_runs_actual_duration"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('FINISHED', 'FINISHED_EARLY', 'CANCELLED', 'RESET')",
            name="ck_timer_runs_outcome_values",
        ),
        sa.CheckConstraint(
            "(ended_at_utc IS NULL AND outcome IS NULL) OR (ended_at_utc IS NOT NULL AND outcome IS NOT NULL)",
            name="ck_timer_runs_end_outcome",
        ),
        sa.ForeignKeyConstraint(["timer_id"], ["timers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_timer_runs_timer_started", "timer_runs", ["timer_id", "started_at_utc"])
    op.create_index("ix_timer_runs_task_started", "timer_runs", ["task_id", "started_at_utc"])
    op.create_index(
        "ux_timer_runs_one_open",
        "timer_runs",
        ["timer_id"],
        unique=True,
        sqlite_where=sa.text("ended_at_utc IS NULL"),
    )

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("theme", sa.String(16), nullable=False),
        sa.Column("global_hotkey", sa.String(120), nullable=False),
        sa.Column("start_with_windows", sa.Boolean(), nullable=False),
        sa.Column("minimize_to_tray", sa.Boolean(), nullable=False),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("default_timer_duration_ms", sa.Integer(), nullable=False),
        sa.Column("pomodoro_work_duration_ms", sa.Integer(), nullable=False),
        sa.Column("short_break_duration_ms", sa.Integer(), nullable=False),
        sa.Column("long_break_duration_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_app_settings_singleton"),
        sa.CheckConstraint("theme IN ('SYSTEM', 'LIGHT', 'DARK')", name="ck_settings_theme_values"),
        sa.CheckConstraint("version >= 1", name="ck_settings_version_positive"),
        sa.CheckConstraint(
            "start_with_windows IN (0, 1)", name="ck_settings_start_with_windows_bool"
        ),
        sa.CheckConstraint("minimize_to_tray IN (0, 1)", name="ck_settings_minimize_to_tray_bool"),
        sa.CheckConstraint(
            "notifications_enabled IN (0, 1)", name="ck_settings_notifications_enabled_bool"
        ),
        sa.CheckConstraint(
            "default_timer_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_default_timer"
        ),
        sa.CheckConstraint(
            "pomodoro_work_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_work_timer"
        ),
        sa.CheckConstraint(
            "short_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_short_break"
        ),
        sa.CheckConstraint(
            "long_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_settings_long_break"
        ),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("dedupe_key", sa.String(256), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("lease_until_utc", sa.BigInteger()),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_error_message", sa.String(500)),
        sa.Column("provider_message_id", sa.String(256)),
        sa.Column("created_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("delivered_at_utc", sa.BigInteger()),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_deliveries_dedupe_key"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_FLIGHT', 'DELIVERED', 'FAILED')",
            name="ck_notification_deliveries_status_values",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_notification_deliveries_attempt_count"),
    )
    op.create_index(
        "ix_notification_deliveries_status_available",
        "notification_deliveries",
        ["status", "available_at_utc"],
    )

    op.create_table(
        "external_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_device_id", sa.String(128), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("app_name", sa.String(256), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("received_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("is_dismissed", sa.Boolean(), nullable=False),
        sa.Column("created_at_utc", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "source", "source_device_id", "external_id", name="uq_external_notifications_source"
        ),
        sa.CheckConstraint("is_read IN (0, 1)", name="ck_external_notifications_is_read_bool"),
        sa.CheckConstraint(
            "is_dismissed IN (0, 1)", name="ck_external_notifications_is_dismissed_bool"
        ),
    )
    op.create_index(
        "ix_external_notifications_received",
        "external_notifications",
        ["received_at_utc", "is_dismissed"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_notifications_received", table_name="external_notifications")
    op.drop_table("external_notifications")
    op.drop_index(
        "ix_notification_deliveries_status_available", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    op.drop_table("app_settings")
    op.drop_index("ux_timer_runs_one_open", table_name="timer_runs")
    op.drop_index("ix_timer_runs_task_started", table_name="timer_runs")
    op.drop_index("ix_timer_runs_timer_started", table_name="timer_runs")
    op.drop_table("timer_runs")
    op.drop_index("ux_timers_one_active", table_name="timers")
    op.drop_table("timers")
    op.drop_index("ux_timer_presets_active_position", table_name="timer_presets")
    op.drop_index("ux_timer_presets_active_name", table_name="timer_presets")
    op.drop_table("timer_presets")
    op.drop_index("ix_reminder_events_history", table_name="reminder_events")
    op.drop_table("reminder_events")
    op.drop_index("ix_reminders_note", table_name="reminders")
    op.drop_index("ix_reminders_task", table_name="reminders")
    op.drop_index("ix_reminders_scheduler", table_name="reminders")
    op.drop_table("reminders")
    op.drop_table("task_tags")
    op.drop_table("note_tags")
    op.drop_index("ix_tasks_completed", table_name="tasks")
    op.drop_index("ix_tasks_priority_status", table_name="tasks")
    op.drop_index("ix_tasks_folder_status", table_name="tasks")
    op.drop_index("ix_tasks_status_due", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_notes_title", table_name="notes")
    op.drop_index("ix_notes_folder_list", table_name="notes")
    op.drop_index("ix_notes_list", table_name="notes")
    op.drop_table("notes")
    op.drop_index("ux_tags_active_normalized_name", table_name="tags")
    op.drop_table("tags")
    op.drop_index("ix_folders_parent_active_position", table_name="folders")
    op.drop_index("ux_folders_active_child_position", table_name="folders")
    op.drop_index("ux_folders_active_root_position", table_name="folders")
    op.drop_index("ux_folders_active_child_name", table_name="folders")
    op.drop_index("ux_folders_active_root_name", table_name="folders")
    op.drop_table("folders")
