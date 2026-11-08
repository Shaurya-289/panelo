"""Schema changes for the new UI design.

* several timers may be active at once (drops ``ux_timers_one_active``)
* ``notes.color``, ``tasks.due_has_time``, repeating-reminder columns, new settings
* tags and folders are removed completely (tables, columns, indexes, FTS rows and triggers)
* reminders are deleted permanently: ``reminders.deleted_at_utc`` is dropped, and reminders that
  were only soft-deleted are purged (with their audit events)

SQLite cannot drop a column that carries a foreign key, so ``notes``, ``tasks``,
``reminders``, ``app_settings`` and ``search_documents`` are rebuilt in batch mode.  A rebuild
drops the old table, which would fire the ``ON DELETE`` actions of every child table (and
detaches every trigger), so ``env.py`` runs migrations with ``PRAGMA foreign_keys=OFF`` and
checks ``PRAGMA foreign_key_check`` before committing.  All FTS triggers are dropped up front
(an ``ALTER TABLE ... RENAME`` fails while a trigger body names a dropped table) and recreated
at the end together with the FTS index.

The downgrade restores the previous schema.  Data without a place in it is lost: note colors, the
date-only task flag, repeat rules, the new settings and the link of every note and task to a
folder (there are none).  If several timers are active it keeps the most recently started one and
cancels the others, since the old schema allows only one.

Revision ID: 20261103_0003
Revises: 20261002_0002
Create Date: 2026-11-03
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20261103_0003"
down_revision: str | None = "20261002_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the FTS synchronisation contract at the two schema versions this
# migration connects (the live contract is ``panelo.db.fts_contract``).
_SOURCES_OLD = {
    "tasks": ("TASK", "title", "description"),
    "notes": ("NOTE", "title", "content"),
    "reminders": ("REMINDER", "title", "message"),
    "timers": ("TIMER", "name", "''"),
    "folders": ("FOLDER", "name", "''"),
    "tags": ("TAG", "name", "''"),
    "timer_presets": ("TIMER_PRESET", "name", "''"),
}
_SOURCES_NEW = {
    table: spec for table, spec in _SOURCES_OLD.items() if table not in {"folders", "tags"}
}
_ENTITY_TYPES_OLD = "'TASK', 'NOTE', 'REMINDER', 'TIMER', 'FOLDER', 'TAG', 'TIMER_PRESET'"
_ENTITY_TYPES_NEW = "'TASK', 'NOTE', 'REMINDER', 'TIMER', 'TIMER_PRESET'"
_NOTE_COLORS = "'none', 'plum', 'rose', 'amber', 'moss', 'teal', 'slate', 'sand'"

_REPEAT_SHAPE = (
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
    "AND repeat_day_of_month IS NULL)))"
)

# The new app_settings columns in column order: (name, type, SQL literal of the server default).
_SETTINGS_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object], str], ...] = (
    ("user_name", sa.String(60), "''"),
    ("tour_done", sa.Boolean(), "0"),
    ("notification_style", sa.String(16), "'TOAST'"),
    ("notification_sound", sa.String(260), "''"),
    ("backdrop_blur", sa.Integer(), "100"),
    ("backdrop_dim", sa.Integer(), "45"),
    ("overlay_size_pct", sa.Integer(), "85"),
    ("overlay_layout_json", sa.Text(), "''"),
    ("files_view_mode", sa.String(8), "'LIST'"),
    ("library_path", sa.String(1024), "''"),
    ("auto_backup", sa.Boolean(), "1"),
    ("keep_backups", sa.Integer(), "7"),
)
_SETTINGS_CHECKS = {
    "ck_settings_user_name_length": "length(user_name) <= 60",
    "ck_settings_tour_done_bool": "tour_done IN (0, 1)",
    "ck_settings_notification_style_values": "notification_style IN ('TOAST', 'WINDOWS', 'BOTH')",
    "ck_settings_notification_sound_length": "length(notification_sound) <= 260",
    "ck_settings_backdrop_blur": "backdrop_blur BETWEEN 0 AND 100",
    "ck_settings_backdrop_dim": "backdrop_dim BETWEEN 0 AND 100",
    "ck_settings_overlay_size": "overlay_size_pct BETWEEN 60 AND 90",
    "ck_settings_files_view_mode_values": "files_view_mode IN ('LIST', 'GRID')",
    "ck_settings_library_path_length": "length(library_path) <= 1024",
    "ck_settings_auto_backup_bool": "auto_backup IN (0, 1)",
    "ck_settings_keep_backups": "keep_backups BETWEEN 1 AND 60",
}


def _mutable() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def _source_trigger_sql(table: str, entity_type: str, title: str, body: str) -> list[str]:
    body_ref = body if body == "''" else f"NEW.{body}"
    fields = title if body == "''" else f"{title}, {body}"
    return [
        f"CREATE TRIGGER trg_fts_{table}_ai AFTER INSERT ON {table} BEGIN "
        "INSERT INTO search_documents(entity_type, entity_id, title, body) "
        f"VALUES ('{entity_type}', NEW.id, NEW.{title}, {body_ref}); END",
        f"CREATE TRIGGER trg_fts_{table}_au AFTER UPDATE OF {fields} ON {table} BEGIN "
        "UPDATE search_documents SET "
        f"title = NEW.{title}, body = {body_ref} "
        f"WHERE entity_type = '{entity_type}' AND entity_id = NEW.id; END",
        f"CREATE TRIGGER trg_fts_{table}_ad AFTER DELETE ON {table} BEGIN "
        f"DELETE FROM search_documents WHERE entity_type = '{entity_type}' "
        "AND entity_id = OLD.id; END",
    ]


_DOCUMENT_TRIGGERS = (
    "CREATE TRIGGER trg_fts_documents_ai AFTER INSERT ON search_documents BEGIN "
    "INSERT INTO unified_search_fts(rowid, title, body) "
    "VALUES (NEW.rowid, NEW.title, NEW.body); END",
    "CREATE TRIGGER trg_fts_documents_ad AFTER DELETE ON search_documents BEGIN "
    "INSERT INTO unified_search_fts(unified_search_fts, rowid, title, body) "
    "VALUES ('delete', OLD.rowid, OLD.title, OLD.body); END",
    "CREATE TRIGGER trg_fts_documents_au AFTER UPDATE ON search_documents BEGIN "
    "INSERT INTO unified_search_fts(unified_search_fts, rowid, title, body) "
    "VALUES ('delete', OLD.rowid, OLD.title, OLD.body); "
    "INSERT INTO unified_search_fts(rowid, title, body) "
    "VALUES (NEW.rowid, NEW.title, NEW.body); END",
)


def _drop_fts_triggers(connection: Connection) -> None:
    names = [
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_fts_%'"
        )
    ]
    for name in names:
        connection.exec_driver_sql(f"DROP TRIGGER {name}")


def _create_fts_triggers(
    connection: Connection, sources: dict[str, tuple[str, str, str]]
) -> None:
    for statement in _DOCUMENT_TRIGGERS:
        connection.exec_driver_sql(statement)
    for table, spec in sources.items():
        for statement in _source_trigger_sql(table, *spec):
            connection.exec_driver_sql(statement)


def _rebuild_fts(connection: Connection, sources: dict[str, tuple[str, str, str]]) -> None:
    """Re-project the source tables into ``search_documents`` and rebuild the FTS index.

    The synchronisation triggers must be absent; they are recreated by the caller.
    """
    connection.exec_driver_sql("DELETE FROM search_documents")
    for table, (entity_type, title, body) in sources.items():
        connection.exec_driver_sql(
            "INSERT INTO search_documents(entity_type, entity_id, title, body) "
            f"SELECT '{entity_type}', id, {title}, {body} FROM {table}"
        )
    connection.exec_driver_sql("INSERT INTO unified_search_fts(unified_search_fts) VALUES('rebuild')")


def _replace_entity_type_check(entity_types: str) -> None:
    with op.batch_alter_table("search_documents", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_search_documents_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_search_documents_entity_type", f"entity_type IN ({entity_types})"
        )


def upgrade() -> None:
    connection = op.get_bind()
    _drop_fts_triggers(connection)

    # Several timers may now be active at once.
    op.drop_index("ux_timers_one_active", table_name="timers")

    with op.batch_alter_table("notes", recreate="always") as batch_op:
        batch_op.drop_index("ix_notes_folder_list")
        batch_op.drop_column("folder_id")
        batch_op.add_column(
            sa.Column("color", sa.String(16), nullable=False, server_default="none")
        )
        batch_op.create_check_constraint("ck_notes_color_values", f"color IN ({_NOTE_COLORS})")

    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_index("ix_tasks_folder_status")
        batch_op.drop_column("folder_id")
        batch_op.add_column(
            sa.Column("due_has_time", sa.Boolean(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.create_check_constraint("ck_tasks_due_has_time_bool", "due_has_time IN (0, 1)")

    # Reminders are deleted permanently now; purge any that were only soft-deleted.
    connection.exec_driver_sql(
        "DELETE FROM reminder_events WHERE reminder_id IN "
        "(SELECT id FROM reminders WHERE deleted_at_utc IS NOT NULL)"
    )
    connection.exec_driver_sql("DELETE FROM reminders WHERE deleted_at_utc IS NOT NULL")
    with op.batch_alter_table("reminders", recreate="always") as batch_op:
        batch_op.drop_index("ix_reminders_scheduler")
        batch_op.drop_column("deleted_at_utc")
        batch_op.create_index("ix_reminders_scheduler", ["status", "due_at_utc"])
        batch_op.add_column(sa.Column("repeat_kind", sa.String(16)))
        batch_op.add_column(sa.Column("repeat_days", sa.Integer()))
        batch_op.add_column(sa.Column("repeat_day_of_month", sa.Integer()))
        batch_op.add_column(sa.Column("repeat_every_n", sa.Integer()))
        batch_op.add_column(sa.Column("repeat_every_unit", sa.String(8)))
        batch_op.add_column(sa.Column("anchor_at_utc", sa.BigInteger()))
        batch_op.create_check_constraint(
            "ck_reminders_repeat_kind_values",
            "repeat_kind IS NULL OR repeat_kind IN "
            "('DAILY', 'WEEKDAYS', 'WEEKLY', 'MONTHLY', 'YEARLY', 'EVERY')",
        )
        batch_op.create_check_constraint(
            "ck_reminders_repeat_unit_values",
            "repeat_every_unit IS NULL OR repeat_every_unit IN ('HOURS', 'DAYS', 'WEEKS')",
        )
        batch_op.create_check_constraint("ck_reminders_repeat_shape", _REPEAT_SHAPE)

    with op.batch_alter_table("app_settings", recreate="always") as batch_op:
        for name, column_type, default in _SETTINGS_COLUMNS:
            batch_op.add_column(
                sa.Column(name, column_type, nullable=False, server_default=sa.text(default))
            )
        batch_op.add_column(sa.Column("last_auto_backup_at_utc", sa.BigInteger()))
        for name, condition in _SETTINGS_CHECKS.items():
            batch_op.create_check_constraint(name, condition)

    # Tags and folders are gone for good.
    connection.exec_driver_sql("DELETE FROM search_documents WHERE entity_type IN ('FOLDER', 'TAG')")
    op.drop_table("note_tags")
    op.drop_table("task_tags")
    op.drop_table("tags")
    op.drop_table("folders")

    _replace_entity_type_check(_ENTITY_TYPES_NEW)
    _rebuild_fts(connection, _SOURCES_NEW)
    _create_fts_triggers(connection, _SOURCES_NEW)
    connection.exec_driver_sql(
        "INSERT INTO unified_search_fts(unified_search_fts, rank) VALUES('integrity-check', 1)"
    )


def _cancel_surplus_active_timers(connection: Connection) -> None:
    """The old schema allows one RUNNING or PAUSED timer; keep the most recently started."""
    now = int(time.time() * 1_000_000)
    rows = connection.exec_driver_sql(
        "SELECT id, state, planned_duration_ms, elapsed_before_run_ms, run_started_at_utc "
        "FROM timers WHERE state IN ('RUNNING', 'PAUSED') AND deleted_at_utc IS NULL "
        "ORDER BY first_started_at_utc DESC, id DESC"
    ).fetchall()
    for timer_id, state, planned, elapsed_before, run_started in rows[1:]:
        elapsed = elapsed_before
        if state == "RUNNING":
            elapsed = min(planned, elapsed_before + max(0, (now - run_started) // 1_000))
        connection.exec_driver_sql(
            "UPDATE timers SET state = 'CANCELLED', elapsed_before_run_ms = ?, "
            "run_started_at_utc = NULL, deadline_at_utc = NULL, ended_at_utc = ?, "
            "updated_at_utc = ?, version = version + 1 WHERE id = ?",
            (elapsed, now, now, timer_id),
        )
        connection.exec_driver_sql(
            "UPDATE timer_runs SET actual_duration_ms = ?, ended_at_utc = ?, outcome = 'CANCELLED' "
            "WHERE timer_id = ? AND ended_at_utc IS NULL",
            (elapsed, now, timer_id),
        )


def downgrade() -> None:
    """Restore the previous schema.

    Data that has no place in the old schema is lost: note colors, the date-only task flag,
    repeat rules (a repeating reminder becomes a one-time reminder at its current due time) and
    the new settings.  Surplus active timers are cancelled (the old schema allows only one).
    """
    connection = op.get_bind()
    _drop_fts_triggers(connection)
    _cancel_surplus_active_timers(connection)
    op.create_index(
        "ux_timers_one_active",
        "timers",
        [sa.literal_column("1")],
        unique=True,
        sqlite_where=sa.text("state IN ('RUNNING', 'PAUSED') AND deleted_at_utc IS NULL"),
    )

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

    with op.batch_alter_table("notes", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_notes_color_values", type_="check")
        batch_op.drop_column("color")
        batch_op.add_column(
            sa.Column(
                "folder_id",
                sa.String(36),
                sa.ForeignKey("folders.id", ondelete="SET NULL", name="fk_notes_folder_id_folders"),
            ),
            insert_after="content_format",
        )
        batch_op.create_index("ix_notes_folder_list", ["folder_id", "deleted_at_utc", "updated_at_utc"])

    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_tasks_due_has_time_bool", type_="check")
        batch_op.drop_column("due_has_time")
        batch_op.add_column(
            sa.Column(
                "folder_id",
                sa.String(36),
                sa.ForeignKey("folders.id", ondelete="SET NULL", name="fk_tasks_folder_id_folders"),
            ),
            insert_after="priority",
        )
        batch_op.create_index("ix_tasks_folder_status", ["folder_id", "deleted_at_utc", "status"])

    with op.batch_alter_table("reminders", recreate="always") as batch_op:
        batch_op.drop_index("ix_reminders_scheduler")
        batch_op.add_column(sa.Column("deleted_at_utc", sa.BigInteger()), insert_after="snooze_count")
        batch_op.create_index("ix_reminders_scheduler", ["status", "due_at_utc", "deleted_at_utc"])
        batch_op.drop_constraint("ck_reminders_repeat_shape", type_="check")
        batch_op.drop_constraint("ck_reminders_repeat_unit_values", type_="check")
        batch_op.drop_constraint("ck_reminders_repeat_kind_values", type_="check")
        for name in (
            "anchor_at_utc",
            "repeat_every_unit",
            "repeat_every_n",
            "repeat_day_of_month",
            "repeat_days",
            "repeat_kind",
        ):
            batch_op.drop_column(name)

    with op.batch_alter_table("app_settings", recreate="always") as batch_op:
        for name in _SETTINGS_CHECKS:
            batch_op.drop_constraint(name, type_="check")
        batch_op.drop_column("last_auto_backup_at_utc")
        for name, _, _ in reversed(_SETTINGS_COLUMNS):
            batch_op.drop_column(name)

    _replace_entity_type_check(_ENTITY_TYPES_OLD)
    _rebuild_fts(connection, _SOURCES_OLD)
    _create_fts_triggers(connection, _SOURCES_OLD)
    connection.exec_driver_sql(
        "INSERT INTO unified_search_fts(unified_search_fts, rank) VALUES('integrity-check', 1)"
    )
