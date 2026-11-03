"""Static regression contract for the immutable initial migration.

This deliberately does not import ORM metadata: the migration itself is the subject.
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic import command
from sqlalchemy import inspect, text

from productivity_app.bootstrap import _alembic_config
from productivity_app.db.engine import create_sqlite_engine, sqlite_url

_COLUMNS: dict[str, tuple[tuple[str, str, bool, int], ...]] = {
    "folders": (("id", "VARCHAR(36)", False, 1), ("name", "VARCHAR(120)", False, 0), ("normalized_name", "VARCHAR(120)", False, 0), ("parent_id", "VARCHAR(36)", True, 0), ("sort_order", "INTEGER", False, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "tags": (("id", "VARCHAR(36)", False, 1), ("name", "VARCHAR(120)", False, 0), ("normalized_name", "VARCHAR(120)", False, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "notes": (("id", "VARCHAR(36)", False, 1), ("title", "VARCHAR(300)", False, 0), ("content", "TEXT", False, 0), ("content_format", "VARCHAR(32)", False, 0), ("folder_id", "VARCHAR(36)", True, 0), ("is_pinned", "BOOLEAN", False, 0), ("is_archived", "BOOLEAN", False, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "tasks": (("id", "VARCHAR(36)", False, 1), ("title", "VARCHAR(300)", False, 0), ("description", "TEXT", False, 0), ("status", "VARCHAR(32)", False, 0), ("priority", "VARCHAR(32)", False, 0), ("folder_id", "VARCHAR(36)", True, 0), ("start_at_utc", "BIGINT", True, 0), ("due_at_utc", "BIGINT", True, 0), ("completed_at_utc", "BIGINT", True, 0), ("cancelled_at_utc", "BIGINT", True, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "note_tags": (("note_id", "VARCHAR(36)", False, 1), ("tag_id", "VARCHAR(36)", False, 2)),
    "task_tags": (("task_id", "VARCHAR(36)", False, 1), ("tag_id", "VARCHAR(36)", False, 2)),
    "reminders": (("id", "VARCHAR(36)", False, 1), ("title", "VARCHAR(300)", False, 0), ("message", "TEXT", False, 0), ("due_at_utc", "BIGINT", False, 0), ("scheduled_timezone", "VARCHAR(64)", False, 0), ("status", "VARCHAR(32)", False, 0), ("task_id", "VARCHAR(36)", True, 0), ("note_id", "VARCHAR(36)", True, 0), ("last_triggered_at_utc", "BIGINT", True, 0), ("dismissed_at_utc", "BIGINT", True, 0), ("completed_at_utc", "BIGINT", True, 0), ("cancelled_at_utc", "BIGINT", True, 0), ("snooze_count", "INTEGER", False, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "reminder_events": (("id", "VARCHAR(36)", False, 1), ("reminder_id", "VARCHAR(36)", False, 0), ("event_type", "VARCHAR(64)", False, 0), ("occurred_at_utc", "BIGINT", False, 0), ("previous_status", "VARCHAR(32)", True, 0), ("new_status", "VARCHAR(32)", True, 0), ("old_due_at_utc", "BIGINT", True, 0), ("new_due_at_utc", "BIGINT", True, 0), ("metadata_json", "TEXT", True, 0)),
    "timer_presets": (("id", "VARCHAR(36)", False, 1), ("name", "VARCHAR(120)", False, 0), ("normalized_name", "VARCHAR(120)", False, 0), ("duration_ms", "INTEGER", False, 0), ("sort_order", "INTEGER", False, 0), ("is_builtin", "BOOLEAN", False, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "timers": (("id", "VARCHAR(36)", False, 1), ("name", "VARCHAR(120)", False, 0), ("preset_id", "VARCHAR(36)", True, 0), ("task_id", "VARCHAR(36)", True, 0), ("state", "VARCHAR(32)", False, 0), ("planned_duration_ms", "INTEGER", False, 0), ("elapsed_before_run_ms", "INTEGER", False, 0), ("run_started_at_utc", "BIGINT", True, 0), ("deadline_at_utc", "BIGINT", True, 0), ("first_started_at_utc", "BIGINT", True, 0), ("ended_at_utc", "BIGINT", True, 0), ("deleted_at_utc", "BIGINT", True, 0), ("created_at_utc", "BIGINT", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "timer_runs": (("id", "VARCHAR(36)", False, 1), ("timer_id", "VARCHAR(36)", False, 0), ("task_id", "VARCHAR(36)", True, 0), ("planned_duration_ms", "INTEGER", False, 0), ("actual_duration_ms", "INTEGER", False, 0), ("started_at_utc", "BIGINT", False, 0), ("ended_at_utc", "BIGINT", True, 0), ("outcome", "VARCHAR(32)", True, 0)),
    "app_settings": (("id", "INTEGER", False, 1), ("theme", "VARCHAR(16)", False, 0), ("global_hotkey", "VARCHAR(120)", False, 0), ("start_with_windows", "BOOLEAN", False, 0), ("minimize_to_tray", "BOOLEAN", False, 0), ("notifications_enabled", "BOOLEAN", False, 0), ("default_timer_duration_ms", "INTEGER", False, 0), ("pomodoro_work_duration_ms", "INTEGER", False, 0), ("short_break_duration_ms", "INTEGER", False, 0), ("long_break_duration_ms", "INTEGER", False, 0), ("updated_at_utc", "BIGINT", False, 0), ("version", "INTEGER", False, 0)),
    "notification_deliveries": (("id", "VARCHAR(36)", False, 1), ("kind", "VARCHAR(64)", False, 0), ("aggregate_type", "VARCHAR(64)", False, 0), ("aggregate_id", "VARCHAR(36)", False, 0), ("dedupe_key", "VARCHAR(256)", False, 0), ("title", "VARCHAR(300)", False, 0), ("body", "TEXT", False, 0), ("status", "VARCHAR(32)", False, 0), ("attempt_count", "INTEGER", False, 0), ("available_at_utc", "BIGINT", False, 0), ("lease_until_utc", "BIGINT", True, 0), ("last_error_code", "VARCHAR(64)", True, 0), ("last_error_message", "VARCHAR(500)", True, 0), ("provider_message_id", "VARCHAR(256)", True, 0), ("created_at_utc", "BIGINT", False, 0), ("delivered_at_utc", "BIGINT", True, 0)),
    "external_notifications": (("id", "VARCHAR(36)", False, 1), ("source", "VARCHAR(64)", False, 0), ("source_device_id", "VARCHAR(128)", False, 0), ("external_id", "VARCHAR(256)", False, 0), ("app_name", "VARCHAR(256)", False, 0), ("title", "VARCHAR(300)", False, 0), ("body", "TEXT", False, 0), ("received_at_utc", "BIGINT", False, 0), ("is_read", "BOOLEAN", False, 0), ("is_dismissed", "BOOLEAN", False, 0), ("created_at_utc", "BIGINT", False, 0)),
}

_FOREIGN_KEYS = {
    "folders": {(("parent_id",), "folders", ("id",), "SET NULL")}, "notes": {(("folder_id",), "folders", ("id",), "SET NULL")}, "tasks": {(("folder_id",), "folders", ("id",), "SET NULL")},
    "note_tags": {(("note_id",), "notes", ("id",), "CASCADE"), (("tag_id",), "tags", ("id",), "CASCADE")}, "task_tags": {(("task_id",), "tasks", ("id",), "CASCADE"), (("tag_id",), "tags", ("id",), "CASCADE")},
    "reminders": {(("task_id",), "tasks", ("id",), "SET NULL"), (("note_id",), "notes", ("id",), "SET NULL")}, "reminder_events": {(("reminder_id",), "reminders", ("id",), "CASCADE")},
    "timers": {(("preset_id",), "timer_presets", ("id",), "SET NULL"), (("task_id",), "tasks", ("id",), "SET NULL")}, "timer_runs": {(("timer_id",), "timers", ("id",), "CASCADE"), (("task_id",), "tasks", ("id",), "SET NULL")},
}

_INDEXES = {
    "folders": {"ix_folders_parent_active_position": (("parent_id", "deleted_at_utc", "sort_order"), False)}, "notes": {"ix_notes_list": (("deleted_at_utc", "is_archived", "is_pinned", "updated_at_utc"), False), "ix_notes_folder_list": (("folder_id", "deleted_at_utc", "updated_at_utc"), False), "ix_notes_title": (("title",), False)}, "tasks": {"ix_tasks_status_due": (("deleted_at_utc", "status", "due_at_utc"), False), "ix_tasks_folder_status": (("folder_id", "deleted_at_utc", "status"), False), "ix_tasks_priority_status": (("priority", "status", "deleted_at_utc"), False), "ix_tasks_completed": (("completed_at_utc", "deleted_at_utc"), False)}, "reminders": {"ix_reminders_scheduler": (("status", "due_at_utc", "deleted_at_utc"), False), "ix_reminders_task": (("task_id",), False), "ix_reminders_note": (("note_id",), False)}, "reminder_events": {"ix_reminder_events_history": (("reminder_id", "occurred_at_utc"), False)}, "timer_runs": {"ix_timer_runs_timer_started": (("timer_id", "started_at_utc"), False), "ix_timer_runs_task_started": (("task_id", "started_at_utc"), False)}, "notification_deliveries": {"ix_notification_deliveries_status_available": (("status", "available_at_utc"), False)}, "external_notifications": {"ix_external_notifications_received": (("received_at_utc", "is_dismissed"), False)},
}

_PARTIAL_INDEXES = {
    "ux_folders_active_root_name": "normalized_name WHERE parent_id IS NULL AND deleted_at_utc IS NULL", "ux_folders_active_child_name": "parent_id, normalized_name WHERE parent_id IS NOT NULL AND deleted_at_utc IS NULL", "ux_folders_active_root_position": "sort_order WHERE parent_id IS NULL AND deleted_at_utc IS NULL", "ux_folders_active_child_position": "parent_id, sort_order WHERE parent_id IS NOT NULL AND deleted_at_utc IS NULL", "ux_tags_active_normalized_name": "normalized_name WHERE deleted_at_utc IS NULL", "ux_timer_presets_active_name": "normalized_name WHERE deleted_at_utc IS NULL", "ux_timer_presets_active_position": "sort_order WHERE deleted_at_utc IS NULL", "ux_timers_one_active": "1) WHERE state IN ('RUNNING', 'PAUSED') AND deleted_at_utc IS NULL", "ux_timer_runs_one_open": "timer_id WHERE ended_at_utc IS NULL",
}

_CHECKS = {
    "folders": {"ck_folders_sort_order_nonnegative": "sort_order >= 0", "ck_folders_no_self_parent": "parent_id IS NULL OR parent_id != id"},
    "notes": {"ck_notes_content_format": "content_format = 'PLAIN_TEXT'", "ck_notes_is_pinned_bool": "is_pinned IN (0, 1)", "ck_notes_is_archived_bool": "is_archived IN (0, 1)"},
    "tasks": {"ck_tasks_title_length": "length(title) BETWEEN 1 AND 300", "ck_tasks_start_before_due": "start_at_utc IS NULL OR due_at_utc IS NULL OR start_at_utc <= due_at_utc", "ck_tasks_priority_values": "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')", "ck_tasks_status_values": "status IN ('TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')", "ck_tasks_terminal_timestamps": "(status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND cancelled_at_utc IS NULL) OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND completed_at_utc IS NULL) OR (status IN ('TODO', 'IN_PROGRESS') AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)"},
    "reminders": {"ck_reminders_title_length": "length(title) BETWEEN 1 AND 300", "ck_reminders_one_link": "NOT (task_id IS NOT NULL AND note_id IS NOT NULL)", "ck_reminders_snooze_count": "snooze_count >= 0", "ck_reminders_status_values": "status IN ('PENDING', 'TRIGGERED', 'OVERDUE', 'DISMISSED', 'COMPLETED', 'CANCELLED')", "ck_reminders_terminal_timestamps": "(status = 'DISMISSED' AND dismissed_at_utc IS NOT NULL AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL) OR (status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND dismissed_at_utc IS NULL AND cancelled_at_utc IS NULL) OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND dismissed_at_utc IS NULL AND completed_at_utc IS NULL) OR (status IN ('PENDING', 'TRIGGERED', 'OVERDUE') AND dismissed_at_utc IS NULL AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)"},
    "timer_presets": {"ck_timer_presets_positive_duration": "duration_ms > 0", "ck_timer_presets_sort_order": "sort_order >= 0", "ck_timer_presets_is_builtin_bool": "is_builtin IN (0, 1)"},
    "timers": {"ck_timers_positive_duration": "planned_duration_ms > 0", "ck_timers_elapsed_range": "elapsed_before_run_ms >= 0 AND elapsed_before_run_ms <= planned_duration_ms", "ck_timers_state_values": "state IN ('READY', 'RUNNING', 'PAUSED', 'FINISHED', 'CANCELLED')", "ck_timers_state_timestamps": "(state = 'RUNNING' AND run_started_at_utc IS NOT NULL AND deadline_at_utc IS NOT NULL AND ended_at_utc IS NULL) OR (state = 'PAUSED' AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND ended_at_utc IS NULL) OR (state = 'READY' AND elapsed_before_run_ms = 0 AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND first_started_at_utc IS NULL AND ended_at_utc IS NULL) OR (state IN ('FINISHED', 'CANCELLED') AND run_started_at_utc IS NULL AND deadline_at_utc IS NULL AND ended_at_utc IS NOT NULL)"},
    "timer_runs": {"ck_timer_runs_positive_duration": "planned_duration_ms > 0", "ck_timer_runs_actual_duration": "actual_duration_ms >= 0", "ck_timer_runs_outcome_values": "outcome IS NULL OR outcome IN ('FINISHED', 'FINISHED_EARLY', 'CANCELLED', 'RESET')", "ck_timer_runs_end_outcome": "(ended_at_utc IS NULL AND outcome IS NULL) OR (ended_at_utc IS NOT NULL AND outcome IS NOT NULL)"},
    "app_settings": {"ck_app_settings_singleton": "id = 1", "ck_settings_theme_values": "theme IN ('SYSTEM', 'LIGHT', 'DARK')", "ck_settings_version_positive": "version >= 1", "ck_settings_start_with_windows_bool": "start_with_windows IN (0, 1)", "ck_settings_minimize_to_tray_bool": "minimize_to_tray IN (0, 1)", "ck_settings_notifications_enabled_bool": "notifications_enabled IN (0, 1)", "ck_settings_default_timer": "default_timer_duration_ms BETWEEN 1000 AND 86400000", "ck_settings_work_timer": "pomodoro_work_duration_ms BETWEEN 1000 AND 86400000", "ck_settings_short_break": "short_break_duration_ms BETWEEN 1000 AND 86400000", "ck_settings_long_break": "long_break_duration_ms BETWEEN 1000 AND 86400000"},
    "notification_deliveries": {"ck_notification_deliveries_status_values": "status IN ('PENDING', 'IN_FLIGHT', 'DELIVERED', 'FAILED')", "ck_notification_deliveries_attempt_count": "attempt_count >= 0"},
    "external_notifications": {"ck_external_notifications_is_read_bool": "is_read IN (0, 1)", "ck_external_notifications_is_dismissed_bool": "is_dismissed IN (0, 1)"},
}


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[(),]", " ", value)).strip().upper()


def test_initial_migration_matches_static_phase_zero_schema_contract(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-contract.db"
    # This test freezes revision 20260924_0001 itself.  Later revisions are
    # deliberately excluded from the base-schema contract.
    command.upgrade(_alembic_config(sqlite_url(database_path), 5_000), "20260924_0001")
    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - {"alembic_version"} == set(_COLUMNS)
        for table, columns in _COLUMNS.items():
            actual = inspector.get_columns(table)
            assert [(column["name"], str(column["type"]).upper(), column["nullable"], column["primary_key"]) for column in actual] == list(columns)
            assert all(column["default"] is None for column in actual)
            expected_fks = _FOREIGN_KEYS.get(table, set())
            actual_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]), fk["options"].get("ondelete")) for fk in inspector.get_foreign_keys(table)}
            assert actual_fks == expected_fks
            expected_indexes = _INDEXES.get(table, {})
            actual_indexes = {index["name"]: (tuple(index["column_names"]), bool(index["unique"])) for index in inspector.get_indexes(table) if index["name"] not in _PARTIAL_INDEXES}
            assert actual_indexes == expected_indexes
            actual_checks = {check["name"]: check["sqltext"] for check in inspector.get_check_constraints(table)}
            assert {name: _compact(sql) for name, sql in actual_checks.items()} == {
                name: _compact(sql) for name, sql in _CHECKS.get(table, {}).items()
            }
        with engine.connect() as connection:
            partial = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")).all())
            for name, fragment in _PARTIAL_INDEXES.items():
                assert name in partial
                assert _compact(fragment) in _compact(partial[name])
        assert inspector.get_unique_constraints("notification_deliveries") == [{"name": "uq_notification_deliveries_dedupe_key", "column_names": ["dedupe_key"]}]
        assert inspector.get_unique_constraints("external_notifications") == [{"name": "uq_external_notifications_source", "column_names": ["source", "source_device_id", "external_id"]}]
    finally:
        engine.dispose()
