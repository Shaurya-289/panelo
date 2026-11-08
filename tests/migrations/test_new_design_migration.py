"""Migration 20261103_0003 (the new design): data survival, defaults, atomicity, downgrade.

Every database here lives under ``tmp_path``; nothing touches a real user database.
"""

from __future__ import annotations

import re
import sqlite3
import warnings
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, create_engine, inspect, text

from panelo.bootstrap import _alembic_config, create_application
from panelo.core.config import AppConfig, DatabaseConfig
from panelo.core.paths import AppPaths
from panelo.db import models  # noqa: F401 - imports the mappings into the metadata
from panelo.db.base import Base
from panelo.db.engine import create_sqlite_engine, sqlite_url
from panelo.db.fts_contract import EXPECTED_NORMALIZED_FTS_TRIGGER_SQL, normalize_fts_sql

PREVIOUS = "20261002_0002"
HEAD = "20261103_0003"

_SEED = """
INSERT INTO folders VALUES ('f1','Folder','folder',NULL,0,NULL,1,1,1);
INSERT INTO tags VALUES ('tag1','Tag','tag',NULL,1,1,1);
INSERT INTO notes VALUES ('n1','Note one','body one','PLAIN_TEXT','f1',1,0,NULL,10,20,3);
INSERT INTO notes VALUES ('n2','Note two','body two','PLAIN_TEXT',NULL,0,1,NULL,11,21,1);
INSERT INTO notes VALUES ('n3','Trashed note','gone','PLAIN_TEXT',NULL,0,0,500,12,22,4);
INSERT INTO note_tags VALUES ('n1','tag1');
INSERT INTO tasks VALUES ('t1','Task one','desc','TODO','HIGH','f1',NULL,5000000,NULL,NULL,NULL,12,22,2);
INSERT INTO tasks VALUES ('t2','Task two','','COMPLETED','NORMAL',NULL,NULL,NULL,7000000,NULL,NULL,13,23,5);
INSERT INTO task_tags VALUES ('t1','tag1');
INSERT INTO reminders VALUES ('r1','Linked to task','m',1000000,'UTC','PENDING','t1',NULL,NULL,NULL,NULL,NULL,0,NULL,1,1,1);
INSERT INTO reminders VALUES ('r2','Linked to note','m',2000000,'Asia/Kolkata','PENDING',NULL,'n1',NULL,NULL,NULL,NULL,2,NULL,1,1,3);
INSERT INTO reminders VALUES ('r3','Soft deleted','m',3000000,'UTC','PENDING',NULL,NULL,NULL,NULL,NULL,NULL,0,99,1,1,1);
INSERT INTO reminder_events VALUES ('e1','r1','REMINDER_CREATED',1,NULL,'PENDING',NULL,1000000,NULL);
INSERT INTO reminder_events VALUES ('e2','r2','REMINDER_CREATED',1,NULL,'PENDING',NULL,2000000,NULL);
INSERT INTO reminder_events VALUES ('e3','r3','REMINDER_CREATED',1,NULL,'PENDING',NULL,3000000,NULL);
INSERT INTO timer_presets VALUES ('p1','Preset','preset',60000,0,0,NULL,1,1,1);
INSERT INTO timers VALUES ('tm1','Paused timer','p1','t1','PAUSED',60000,1000,NULL,NULL,5,NULL,NULL,1,1,2);
INSERT INTO timer_runs VALUES ('run1','tm1','t1',60000,1000,5,NULL,NULL);
INSERT INTO app_settings VALUES (1,'DARK','Ctrl+Alt+Space',1,0,1,1500000,1200000,300000,900000,5,4);
"""


def _config(path: Path):
    return _alembic_config(sqlite_url(path), 5_000)


def _seeded_previous_database(path: Path) -> None:
    command.upgrade(_config(path), PREVIOUS)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(_SEED)
        # a focus sequence pointing at a task, and a finished focus timer
        connection.executescript(
            "INSERT INTO focus_sequences VALUES ('fs1','ACTIVE','t1',0,1500000,300000,900000,NULL,NULL,1,1,1);"
            "INSERT INTO timers VALUES ('tm2','Focus','p1','t1','READY',1500000,0,NULL,NULL,NULL,NULL,NULL,1,1,1);"
            "INSERT INTO focus_sessions VALUES ('fz1','fs1','tm2','FOCUS',0,1);"
        )


def _revision(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _names(path: Path, kind: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
        }


def _rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(sql).fetchall()


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[(),]", " ", value)).strip().upper()


def _schema(path: Path) -> dict[str, object]:
    """A structural snapshot (column order included, constraint and FK names excluded)."""
    engine = create_sqlite_engine(path)
    try:
        inspector = inspect(engine)
        tables: dict[str, object] = {}
        for table in sorted(inspector.get_table_names()):
            if table == "alembic_version" or table.startswith("unified_search_fts"):
                continue
            tables[table] = {
                "columns": [
                    (
                        column["name"],
                        str(column["type"]).upper(),
                        column["nullable"],
                        column["primary_key"],
                        str(column["default"]),
                    )
                    for column in inspector.get_columns(table)
                ],
                "foreign_keys": sorted(
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                        fk["options"].get("ondelete"),
                    )
                    for fk in inspector.get_foreign_keys(table)
                ),
                "checks": {
                    check["name"]: _compact(check["sqltext"])
                    for check in inspector.get_check_constraints(table)
                },
            }
        with engine.connect() as connection:
            indexes = {
                row[0]: (row[1], _compact(row[2]))
                for row in connection.execute(
                    text(
                        "SELECT name, tbl_name, sql FROM sqlite_master "
                        "WHERE type='index' AND sql IS NOT NULL"
                    )
                )
            }
            triggers = {
                row[0]: normalize_fts_sql(row[1])
                for row in connection.execute(
                    text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
                )
            }
        return {"tables": tables, "indexes": indexes, "triggers": triggers}
    finally:
        engine.dispose()


# -- Upgrade ---------------------------------------------------------------------------------
@pytest.fixture
def upgraded(tmp_path: Path) -> Path:
    path = tmp_path / "upgraded.db"
    _seeded_previous_database(path)
    command.upgrade(_config(path), "head")
    return path


def test_upgrade_preserves_every_row_and_gives_new_columns_their_defaults(upgraded: Path) -> None:
    assert _revision(upgraded) == HEAD
    assert _rows(
        upgraded,
        "SELECT id, title, content, is_pinned, is_archived, deleted_at_utc, created_at_utc, "
        "updated_at_utc, version, color FROM notes ORDER BY id",
    ) == [
        ("n1", "Note one", "body one", 1, 0, None, 10, 20, 3, "none"),
        ("n2", "Note two", "body two", 0, 1, None, 11, 21, 1, "none"),
        ("n3", "Trashed note", "gone", 0, 0, 500, 12, 22, 4, "none"),
    ]
    assert _rows(
        upgraded,
        "SELECT id, title, status, priority, due_at_utc, completed_at_utc, version, due_has_time "
        "FROM tasks ORDER BY id",
    ) == [
        ("t1", "Task one", "TODO", "HIGH", 5000000, None, 2, 1),
        ("t2", "Task two", "COMPLETED", "NORMAL", None, 7000000, 5, 1),
    ]
    reminders = _rows(
        upgraded,
        "SELECT id, task_id, note_id, snooze_count, version, repeat_kind, repeat_days, "
        "repeat_day_of_month, repeat_every_n, repeat_every_unit, anchor_at_utc FROM reminders "
        "ORDER BY id",
    )
    # Links survive the table rebuilds, and a soft-deleted reminder is gone with its history.
    assert reminders == [
        ("r1", "t1", None, 0, 1, None, None, None, None, None, None),
        ("r2", None, "n1", 2, 3, None, None, None, None, None, None),
    ]
    assert _rows(upgraded, "SELECT id, reminder_id FROM reminder_events ORDER BY id") == [
        ("e1", "r1"),
        ("e2", "r2"),
    ]
    assert _rows(upgraded, "SELECT id, state, task_id, preset_id FROM timers ORDER BY id") == [
        ("tm1", "PAUSED", "t1", "p1"),
        ("tm2", "READY", "t1", "p1"),
    ]
    assert _rows(upgraded, "SELECT id, task_id FROM timer_runs") == [("run1", "t1")]
    assert _rows(upgraded, "SELECT id, task_id FROM focus_sequences") == [("fs1", "t1")]
    assert _rows(upgraded, "SELECT id, sequence_id, timer_id FROM focus_sessions") == [
        ("fz1", "fs1", "tm2")
    ]
    assert _rows(
        upgraded,
        "SELECT id, theme, global_hotkey, start_with_windows, minimize_to_tray, "
        "notifications_enabled, default_timer_duration_ms, pomodoro_work_duration_ms, "
        "short_break_duration_ms, long_break_duration_ms, updated_at_utc, version, user_name, "
        "tour_done, notification_style, notification_sound, backdrop_blur, backdrop_dim, "
        "overlay_size_pct, overlay_layout_json, files_view_mode, library_path, auto_backup, "
        "keep_backups, last_auto_backup_at_utc FROM app_settings",
    ) == [
        (1, "DARK", "Ctrl+Alt+Space", 1, 0, 1, 1500000, 1200000, 300000, 900000, 5, 4)
        + ("", 0, "TOAST", "", 100, 45, 85, "", "LIST", "", 1, 7, None)
    ]


def test_upgrade_removes_tags_folders_and_their_search_and_index_traces(upgraded: Path) -> None:
    assert not {"folders", "tags", "note_tags", "task_tags"} & _names(upgraded, "table")
    assert not {
        "ux_timers_one_active",
        "ix_notes_folder_list",
        "ix_tasks_folder_status",
        "ux_folders_active_root_name",
        "ux_tags_active_normalized_name",
    } & _names(upgraded, "index")
    for table in ("notes", "tasks"):
        assert "folder_id" not in {row[1] for row in _rows(upgraded, f"PRAGMA table_info({table})")}
    assert _rows(
        upgraded, "SELECT entity_type, count(*) FROM search_documents GROUP BY 1 ORDER BY 1"
    ) == [("NOTE", 3), ("REMINDER", 2), ("TASK", 2), ("TIMER", 2), ("TIMER_PRESET", 1)]
    assert {name: sql for name, sql in _schema(upgraded)["triggers"].items()} == (  # type: ignore[union-attr]
        EXPECTED_NORMALIZED_FTS_TRIGGER_SQL
    )
    assert _rows(
        upgraded,
        "SELECT count(*) FROM unified_search_fts WHERE unified_search_fts MATCH '\"body\"'",
    ) == [(2,)]


def test_upgrade_leaves_a_consistent_database_the_application_can_open(
    upgraded: Path, tmp_path: Path, clock
) -> None:
    assert _rows(upgraded, "PRAGMA foreign_key_check") == []
    assert _rows(upgraded, "PRAGMA integrity_check") == [("ok",)]
    paths = AppPaths(data_dir=tmp_path, log_dir=tmp_path / "logs")
    config = AppConfig(paths=paths, database=DatabaseConfig(path=upgraded), log_level="CRITICAL")
    application = create_application(config, clock)
    try:
        application.search.rebuild_full_text_index()  # also verifies triggers and documents
        assert {note.title for note in application.notes.list_notes()} == {"Note one", "Note two"}
        assert application.settings.get_settings().user_name == ""
        assert [timer.id for timer in application.timers.list_active_timers()] == ["tm1"]
    finally:
        application.close()


def test_upgrading_an_empty_database_and_cycling_is_clean(tmp_path: Path) -> None:
    path = tmp_path / "cycle.db"
    command.upgrade(_config(path), "head")
    first = _schema(path)
    command.downgrade(_config(path), "base")
    assert _names(path, "table") == {"alembic_version"}
    command.upgrade(_config(path), "head")
    assert _schema(path) == first


# -- Atomicity and integrity ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "failure_marker",
    [
        "DROP TRIGGER",
        "DELETE FROM reminders WHERE deleted_at_utc IS NOT NULL",
        "DELETE FROM search_documents WHERE entity_type IN ('FOLDER', 'TAG')",
        "INSERT INTO search_documents(entity_type",
        "VALUES('rebuild')",
        "VALUES('integrity-check', 1)",
    ],
)
def test_failed_upgrade_rolls_everything_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_marker: str
) -> None:
    path = tmp_path / "failed.db"
    _seeded_previous_database(path)
    before = _schema(path)
    rows_before = _rows(path, "SELECT id, title, folder_id FROM notes ORDER BY id")
    original = Connection.exec_driver_sql

    def fail(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        if failure_marker in statement:
            raise sqlite3.OperationalError(f"injected failure at {failure_marker}")
        return original(self, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "exec_driver_sql", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        command.upgrade(_config(path), "head")
    monkeypatch.undo()

    assert _revision(path) == PREVIOUS
    assert _schema(path) == before
    assert _rows(path, "SELECT id, title, folder_id FROM notes ORDER BY id") == rows_before
    assert len(_names(path, "trigger")) == 24
    command.upgrade(_config(path), "head")  # and it still upgrades afterwards
    assert _revision(path) == HEAD


def test_upgrade_with_a_dangling_reference_is_refused_and_rolled_back(tmp_path: Path) -> None:
    path = tmp_path / "dangling.db"
    command.upgrade(_config(path), PREVIOUS)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO reminders VALUES ('r','Orphan','',1,'UTC','PENDING','missing-task',NULL,"
            "NULL,NULL,NULL,NULL,0,NULL,1,1,1)"
        )
    before = _schema(path)
    with pytest.raises(RuntimeError, match="foreign key violation"):
        command.upgrade(_config(path), "head")
    assert _revision(path) == PREVIOUS
    assert _schema(path) == before


# -- Downgrade -----------------------------------------------------------------------------------
def test_downgrade_restores_the_previous_schema_exactly_and_keeps_old_data(
    tmp_path: Path, upgraded: Path
) -> None:
    reference = tmp_path / "reference.db"
    command.upgrade(_config(reference), PREVIOUS)

    command.downgrade(_config(upgraded), PREVIOUS)

    assert _revision(upgraded) == PREVIOUS
    assert _schema(upgraded) == _schema(reference)
    assert _rows(upgraded, "SELECT id, title, content, version FROM notes ORDER BY id") == [
        ("n1", "Note one", "body one", 3),
        ("n2", "Note two", "body two", 1),
        ("n3", "Trashed note", "gone", 4),
    ]
    assert _rows(upgraded, "SELECT id, folder_id FROM tasks ORDER BY id") == [("t1", None), ("t2", None)]
    assert _rows(upgraded, "SELECT id, task_id, note_id FROM reminders ORDER BY id") == [
        ("r1", "t1", None),
        ("r2", None, "n1"),
    ]
    assert _rows(upgraded, "SELECT count(*) FROM folders") == [(0,)]
    assert _rows(upgraded, "PRAGMA foreign_key_check") == []
    # the old FTS contract is back, with documents for everything that still exists
    assert len(_names(upgraded, "trigger")) == 24
    assert _rows(
        upgraded, "SELECT entity_type, count(*) FROM search_documents GROUP BY 1 ORDER BY 1"
    ) == [("NOTE", 3), ("REMINDER", 2), ("TASK", 2), ("TIMER", 2), ("TIMER_PRESET", 1)]
    command.upgrade(_config(upgraded), "head")
    assert _revision(upgraded) == HEAD


def test_downgrade_keeps_the_most_recent_active_timer_and_cancels_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "many-timers.db"
    command.upgrade(_config(path), "head")
    with sqlite3.connect(path) as connection:
        for number, state, started in ((1, "RUNNING", 100), (2, "PAUSED", 200), (3, "RUNNING", 300)):
            run_started = "NULL" if state == "PAUSED" else str(started)
            deadline = "NULL" if state == "PAUSED" else str(started + 10**15)
            connection.execute(
                "INSERT INTO timers (id, name, state, planned_duration_ms, elapsed_before_run_ms, "
                "run_started_at_utc, deadline_at_utc, first_started_at_utc, created_at_utc, "
                f"updated_at_utc, version) VALUES ('t{number}', 'T{number}', '{state}', 60000, 0, "
                f"{run_started}, {deadline}, {started}, 1, 1, 1)"
            )
            connection.execute(
                "INSERT INTO timer_runs (id, timer_id, planned_duration_ms, actual_duration_ms, "
                f"started_at_utc) VALUES ('run{number}', 't{number}', 60000, 0, {started})"
            )

    command.downgrade(_config(path), PREVIOUS)

    assert _rows(path, "SELECT id, state FROM timers ORDER BY id") == [
        ("t1", "CANCELLED"),
        ("t2", "CANCELLED"),
        ("t3", "RUNNING"),
    ]
    assert _rows(path, "SELECT timer_id, outcome FROM timer_runs ORDER BY timer_id") == [
        ("t1", "CANCELLED"),
        ("t2", "CANCELLED"),
        ("t3", None),
    ]
    assert _rows(path, "PRAGMA integrity_check") == [("ok",)]


# -- Schema contract -----------------------------------------------------------------------------
_TASK_CHECK_UNCHANGED = {
    "ck_tasks_title_length": "length(title) BETWEEN 1 AND 300",
    "ck_tasks_start_before_due": "start_at_utc IS NULL OR due_at_utc IS NULL OR start_at_utc <= due_at_utc",
    "ck_tasks_priority_values": "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
    "ck_tasks_status_values": "status IN ('TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
    "ck_tasks_terminal_timestamps": "(status = 'COMPLETED' AND completed_at_utc IS NOT NULL AND cancelled_at_utc IS NULL) OR (status = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND completed_at_utc IS NULL) OR (status IN ('TODO', 'IN_PROGRESS') AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL)",
}


@pytest.fixture(scope="module")
def head_schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    path = tmp_path_factory.mktemp("contract") / "head.db"
    command.upgrade(_config(path), "head")
    return _schema(path)


def _table(schema: dict[str, object], name: str) -> dict[str, object]:
    return schema["tables"][name]  # type: ignore[index,no-any-return]


def test_head_schema_has_exactly_the_expected_tables(head_schema: dict[str, object]) -> None:
    assert set(head_schema["tables"]) == {  # type: ignore[arg-type]
        "app_settings", "external_notifications", "focus_sequences", "focus_sessions", "notes",
        "notification_deliveries", "reminder_events", "reminders", "search_documents", "tasks",
        "timer_presets", "timer_runs", "timers",
    }


def test_head_schema_notes_and_tasks(head_schema: dict[str, object]) -> None:
    notes = _table(head_schema, "notes")
    assert notes["columns"] == [
        ("id", "VARCHAR(36)", False, 1, "None"),
        ("title", "VARCHAR(300)", False, 0, "None"),
        ("content", "TEXT", False, 0, "None"),
        ("content_format", "VARCHAR(32)", False, 0, "None"),
        ("is_pinned", "BOOLEAN", False, 0, "None"),
        ("is_archived", "BOOLEAN", False, 0, "None"),
        ("deleted_at_utc", "BIGINT", True, 0, "None"),
        ("created_at_utc", "BIGINT", False, 0, "None"),
        ("updated_at_utc", "BIGINT", False, 0, "None"),
        ("version", "INTEGER", False, 0, "None"),
        ("color", "VARCHAR(16)", False, 0, "'none'"),
    ]
    assert notes["foreign_keys"] == []
    assert notes["checks"]["ck_notes_color_values"] == _compact(  # type: ignore[index]
        "color IN ('none', 'plum', 'rose', 'amber', 'moss', 'teal', 'slate', 'sand')"
    )
    assert set(notes["checks"]) == {  # type: ignore[arg-type]
        "ck_notes_content_format", "ck_notes_is_pinned_bool", "ck_notes_is_archived_bool",
        "ck_notes_color_values",
    }

    tasks = _table(head_schema, "tasks")
    assert [column[0] for column in tasks["columns"]] == [  # type: ignore[union-attr]
        "id", "title", "description", "status", "priority", "start_at_utc", "due_at_utc",
        "completed_at_utc", "cancelled_at_utc", "deleted_at_utc", "created_at_utc",
        "updated_at_utc", "version", "due_has_time",
    ]
    assert tasks["columns"][-1] == ("due_has_time", "BOOLEAN", False, 0, "1")  # type: ignore[index]
    assert tasks["foreign_keys"] == []
    assert tasks["checks"] == {  # type: ignore[comparison-overlap]
        **{name: _compact(sql) for name, sql in _TASK_CHECK_UNCHANGED.items()},
        "ck_tasks_due_has_time_bool": _compact("due_has_time IN (0, 1)"),
    }


def test_head_schema_indexes_triggers_and_search_documents(head_schema: dict[str, object]) -> None:
    indexes = head_schema["indexes"]
    assert {"ix_notes_list", "ix_notes_title", "ix_tasks_status_due", "ix_tasks_priority_status",
            "ix_tasks_completed", "ix_reminders_scheduler", "ix_reminders_task",
            "ix_reminders_note"} <= set(indexes)  # type: ignore[arg-type]
    assert indexes["ix_reminders_scheduler"] == (  # type: ignore[index]
        "reminders", _compact("CREATE INDEX ix_reminders_scheduler ON reminders (status, due_at_utc)")
    )
    assert not [name for name in indexes if "folder" in name or "tag" in name or name == "ux_timers_one_active"]  # type: ignore[attr-defined]
    assert head_schema["triggers"] == EXPECTED_NORMALIZED_FTS_TRIGGER_SQL
    documents = _table(head_schema, "search_documents")
    assert documents["checks"] == {  # type: ignore[comparison-overlap]
        "ck_search_documents_entity_type": _compact(
            "entity_type IN ('TASK', 'NOTE', 'REMINDER', 'TIMER', 'TIMER_PRESET')"
        )
    }
    assert [column[0] for column in documents["columns"]] == [  # type: ignore[union-attr]
        "rowid", "entity_type", "entity_id", "title", "body",
    ]


def test_head_schema_reminders_carry_the_repeat_columns_and_checks(head_schema: dict[str, object]) -> None:
    reminders = _table(head_schema, "reminders")
    columns = {column[0]: column for column in reminders["columns"]}  # type: ignore[union-attr]
    assert "deleted_at_utc" not in columns
    assert columns["repeat_kind"][1:3] == ("VARCHAR(16)", True)
    assert columns["repeat_days"][1:3] == ("INTEGER", True)
    assert columns["repeat_day_of_month"][1:3] == ("INTEGER", True)
    assert columns["repeat_every_n"][1:3] == ("INTEGER", True)
    assert columns["repeat_every_unit"][1:3] == ("VARCHAR(8)", True)
    assert columns["anchor_at_utc"][1:3] == ("BIGINT", True)
    assert {"ck_reminders_repeat_kind_values", "ck_reminders_repeat_unit_values",
            "ck_reminders_repeat_shape"} <= set(reminders["checks"])  # type: ignore[arg-type]
    assert reminders["foreign_keys"] == [  # type: ignore[comparison-overlap]
        (("note_id",), "notes", ("id",), "SET NULL"),
        (("task_id",), "tasks", ("id",), "SET NULL"),
    ]


def test_head_schema_settings(head_schema: dict[str, object]) -> None:
    settings = _table(head_schema, "app_settings")
    columns = {column[0]: column for column in settings["columns"]}  # type: ignore[union-attr]
    assert {name: (column[1], column[2], column[4]) for name, column in columns.items() if name in {
        "user_name", "tour_done", "notification_style", "notification_sound", "backdrop_blur",
        "backdrop_dim", "overlay_size_pct", "overlay_layout_json", "files_view_mode",
        "library_path", "auto_backup", "keep_backups", "last_auto_backup_at_utc"}} == {
        "user_name": ("VARCHAR(60)", False, "''"),
        "tour_done": ("BOOLEAN", False, "0"),
        "notification_style": ("VARCHAR(16)", False, "'TOAST'"),
        "notification_sound": ("VARCHAR(260)", False, "''"),
        "backdrop_blur": ("INTEGER", False, "100"),
        "backdrop_dim": ("INTEGER", False, "45"),
        "overlay_size_pct": ("INTEGER", False, "85"),
        "overlay_layout_json": ("TEXT", False, "''"),
        "files_view_mode": ("VARCHAR(8)", False, "'LIST'"),
        "library_path": ("VARCHAR(1024)", False, "''"),
        "auto_backup": ("BOOLEAN", False, "1"),
        "keep_backups": ("INTEGER", False, "7"),
        "last_auto_backup_at_utc": ("BIGINT", True, "None"),
    }
    assert {
        "ck_settings_user_name_length", "ck_settings_tour_done_bool",
        "ck_settings_notification_style_values", "ck_settings_notification_sound_length",
        "ck_settings_backdrop_blur", "ck_settings_backdrop_dim", "ck_settings_overlay_size",
        "ck_settings_files_view_mode_values", "ck_settings_library_path_length",
        "ck_settings_auto_backup_bool", "ck_settings_keep_backups", "ck_app_settings_singleton",
        "ck_settings_theme_values", "ck_settings_version_positive",
    } <= set(settings["checks"])  # type: ignore[arg-type]


def test_migrated_schema_matches_the_orm_models(tmp_path: Path) -> None:
    path = tmp_path / "orm.db"
    command.upgrade(_config(path), "head")
    engine = create_engine(sqlite_url(path))

    def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
        derived = name in {"alembic_version", "search_documents"} or (name or "").startswith(
            "unified_search_fts"
        )
        return not derived and not (name or "").startswith("sqlite_autoindex")

    try:
        with engine.connect() as connection, warnings.catch_warnings():
            warnings.simplefilter("ignore")  # expression-based indexes cannot be reflected
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_object": include_object,
                },
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()
