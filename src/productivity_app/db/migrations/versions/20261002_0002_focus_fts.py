"""Add focus metadata and the Phase 7 FTS5 index.

Revision ID: 20261002_0002
Revises: 20260924_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0002"
down_revision: str | None = "20260924_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SOURCES = {
    "tasks": ("TASK", "title", "description"),
    "notes": ("NOTE", "title", "content"),
    "reminders": ("REMINDER", "title", "message"),
    "timers": ("TIMER", "name", "''"),
    "folders": ("FOLDER", "name", "''"),
    "tags": ("TAG", "name", "''"),
    "timer_presets": ("TIMER_PRESET", "name", "''"),
}


def _probe_fts5() -> None:
    connection = op.get_bind()
    try:
        connection.exec_driver_sql("CREATE VIRTUAL TABLE temp.__phase7_fts_probe USING fts5(value)")
        connection.exec_driver_sql("DROP TABLE temp.__phase7_fts_probe")
    except Exception as exc:
        raise RuntimeError("SQLite FTS5 support is required for Phase 7.") from exc


def _source_triggers(table: str, entity_type: str, title: str, body: str) -> None:
    connection = op.get_bind()
    body_ref = body if body == "''" else f"NEW.{body}"
    connection.exec_driver_sql(
        f"CREATE TRIGGER trg_fts_{table}_ai AFTER INSERT ON {table} BEGIN "
        "INSERT INTO search_documents(entity_type, entity_id, title, body) "
        f"VALUES ('{entity_type}', NEW.id, NEW.{title}, {body_ref}); END"
    )
    fields = title if body == "''" else f"{title}, {body}"
    connection.exec_driver_sql(
        f"CREATE TRIGGER trg_fts_{table}_au AFTER UPDATE OF {fields} ON {table} BEGIN "
        "UPDATE search_documents SET "
        f"title = NEW.{title}, body = {body_ref} "
        f"WHERE entity_type = '{entity_type}' AND entity_id = NEW.id; END"
    )
    connection.exec_driver_sql(
        f"CREATE TRIGGER trg_fts_{table}_ad AFTER DELETE ON {table} BEGIN "
        f"DELETE FROM search_documents WHERE entity_type = '{entity_type}' AND entity_id = OLD.id; END"
    )


def upgrade() -> None:
    _probe_fts5()
    op.create_table(
        "focus_sequences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("current_phase_index", sa.Integer(), nullable=False),
        sa.Column("work_duration_ms", sa.Integer(), nullable=False),
        sa.Column("short_break_duration_ms", sa.Integer(), nullable=False),
        sa.Column("long_break_duration_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at_utc", sa.BigInteger()),
        sa.Column("cancelled_at_utc", sa.BigInteger()),
        sa.Column("created_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_utc", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.CheckConstraint("state IN ('ACTIVE', 'COMPLETED', 'CANCELLED')", name="ck_focus_sequences_state"),
        sa.CheckConstraint("current_phase_index BETWEEN 0 AND 7", name="ck_focus_sequences_phase_index"),
        sa.CheckConstraint("work_duration_ms BETWEEN 1000 AND 86400000", name="ck_focus_sequences_work_duration"),
        sa.CheckConstraint("short_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_focus_sequences_short_duration"),
        sa.CheckConstraint("long_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_focus_sequences_long_duration"),
        sa.CheckConstraint("version >= 1", name="ck_focus_sequences_version"),
        sa.CheckConstraint("(state = 'ACTIVE' AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL) OR (state = 'COMPLETED' AND completed_at_utc IS NOT NULL AND cancelled_at_utc IS NULL) OR (state = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND completed_at_utc IS NULL)", name="ck_focus_sequences_terminal_timestamps"),
    )
    op.create_index("ux_focus_sequences_one_active", "focus_sequences", [sa.text("1")], unique=True, sqlite_where=sa.text("state = 'ACTIVE'"))
    op.create_table(
        "focus_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sequence_id", sa.String(36)),
        sa.Column("timer_id", sa.String(36), nullable=False, unique=True),
        sa.Column("phase_kind", sa.String(32), nullable=False),
        sa.Column("phase_index", sa.Integer()),
        sa.Column("created_at_utc", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["sequence_id"], ["focus_sequences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timer_id"], ["timers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("sequence_id", "phase_index", name="uq_focus_sessions_sequence_phase"),
        sa.CheckConstraint("phase_kind IN ('FOCUS', 'SHORT_BREAK', 'LONG_BREAK')", name="ck_focus_sessions_kind"),
        sa.CheckConstraint("(sequence_id IS NULL AND phase_index IS NULL) OR (sequence_id IS NOT NULL AND phase_index BETWEEN 0 AND 7)", name="ck_focus_sessions_sequence_phase"),
    )
    op.create_index("ix_focus_sessions_sequence_phase", "focus_sessions", ["sequence_id", "phase_index"])
    op.create_table(
        "search_documents",
        sa.Column("rowid", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", name="uq_search_documents_entity"),
        sa.CheckConstraint("entity_type IN ('TASK', 'NOTE', 'REMINDER', 'TIMER', 'FOLDER', 'TAG', 'TIMER_PRESET')", name="ck_search_documents_entity_type"),
    )
    connection = op.get_bind()
    connection.exec_driver_sql("CREATE VIRTUAL TABLE unified_search_fts USING fts5(title, body, content='search_documents', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')")
    connection.exec_driver_sql("CREATE TRIGGER trg_fts_documents_ai AFTER INSERT ON search_documents BEGIN INSERT INTO unified_search_fts(rowid, title, body) VALUES (NEW.rowid, NEW.title, NEW.body); END")
    connection.exec_driver_sql("CREATE TRIGGER trg_fts_documents_ad AFTER DELETE ON search_documents BEGIN INSERT INTO unified_search_fts(unified_search_fts, rowid, title, body) VALUES ('delete', OLD.rowid, OLD.title, OLD.body); END")
    connection.exec_driver_sql("CREATE TRIGGER trg_fts_documents_au AFTER UPDATE ON search_documents BEGIN INSERT INTO unified_search_fts(unified_search_fts, rowid, title, body) VALUES ('delete', OLD.rowid, OLD.title, OLD.body); INSERT INTO unified_search_fts(rowid, title, body) VALUES (NEW.rowid, NEW.title, NEW.body); END")
    for table, values in _SOURCES.items():
        _source_triggers(table, *values)
        entity_type, title, body = values
        value_body = body if body == "''" else body
        connection.exec_driver_sql(
            "INSERT INTO search_documents(entity_type, entity_id, title, body) "
            f"SELECT '{entity_type}', id, {title}, {value_body} FROM {table}"
        )
    connection.exec_driver_sql("INSERT INTO unified_search_fts(unified_search_fts) VALUES('rebuild')")
    connection.exec_driver_sql("INSERT INTO unified_search_fts(unified_search_fts, rank) VALUES('integrity-check', 1)")


def downgrade() -> None:
    connection = op.get_bind()
    for table in _SOURCES:
        for suffix in ("ai", "au", "ad"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_fts_{table}_{suffix}")
    for suffix in ("ai", "au", "ad"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_fts_documents_{suffix}")
    connection.exec_driver_sql("DROP TABLE IF EXISTS unified_search_fts")
    op.drop_table("search_documents")
    op.drop_index("ix_focus_sessions_sequence_phase", table_name="focus_sessions")
    op.drop_table("focus_sessions")
    op.drop_index("ux_focus_sequences_one_active", table_name="focus_sequences")
    op.drop_table("focus_sequences")
