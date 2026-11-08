from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from panelo.bootstrap import _alembic_config, create_application
from panelo.core.config import AppConfig, DatabaseConfig
from panelo.core.paths import AppPaths
from panelo.db.engine import sqlite_url

BASE = "20260924_0001"
HEAD = "20261002_0002"
PHASE7_TABLES = {
    "focus_sequences",
    "focus_sessions",
    "search_documents",
    "unified_search_fts",
}


def _config(path):
    return _alembic_config(sqlite_url(path), 5_000)


def _objects(path, object_type: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type=?", (object_type,)
            )
        }


def _revision(path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_phase7_schema_contains_focus_and_external_content_fts(application) -> None:
    with application.engine.connect() as connection:
        inspector = inspect(connection)
        assert {"focus_sequences", "focus_sessions", "search_documents"} <= set(
            inspector.get_table_names()
        )
        sequence_columns = {column["name"] for column in inspector.get_columns("focus_sequences")}
        assert {
            "id",
            "state",
            "task_id",
            "current_phase_index",
            "work_duration_ms",
            "short_break_duration_ms",
            "long_break_duration_ms",
            "completed_at_utc",
            "cancelled_at_utc",
            "created_at_utc",
            "updated_at_utc",
            "version",
        } == sequence_columns
        sql = connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='unified_search_fts'")
        )
        assert sql is not None
        assert "fts5" in sql.lower()
        assert "content='search_documents'" in sql.lower()


def test_downgrade_removes_every_phase7_object_and_reupgrade_is_clean(tmp_path) -> None:
    path = tmp_path / "cycle.sqlite3"
    config = _config(path)
    command.upgrade(config, HEAD)

    command.downgrade(config, BASE)

    assert _revision(path) == BASE
    assert not (PHASE7_TABLES & _objects(path, "table"))
    assert not {name for name in _objects(path, "table") if name.startswith("unified_search_fts_")}
    assert not {name for name in _objects(path, "trigger") if name.startswith("trg_fts_")}
    assert not {
        "ux_focus_sequences_one_active",
        "ix_focus_sessions_sequence_phase",
    } & _objects(path, "index")

    command.upgrade(config, HEAD)

    assert _revision(path) == HEAD
    assert _objects(path, "table") >= PHASE7_TABLES
    assert len({name for name in _objects(path, "trigger") if name.startswith("trg_fts_")}) == 24
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO tasks(id,title,description,status,priority,created_at_utc,updated_at_utc,version) "
            "VALUES('00000000-0000-0000-0000-000000000001','Reupgrade sentinel','',"
            "'TODO','NORMAL',1,1,1)"
        )
        assert connection.execute(
            "SELECT count(*) FROM unified_search_fts WHERE unified_search_fts MATCH ?",
            ('"reupgrade"',),
        ).fetchone() == (1,)


def test_fts_capability_failure_precedes_persistent_phase7_ddl(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "no-fts.sqlite3"
    config = _config(path)
    command.upgrade(config, BASE)
    original = Connection.exec_driver_sql

    def fail_probe(self, statement, *args, **kwargs):
        if "temp.__phase7_fts_probe" in statement and statement.lstrip().upper().startswith(
            "CREATE"
        ):
            raise sqlite3.OperationalError("injected FTS5 capability failure")
        return original(self, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "exec_driver_sql", fail_probe)
    with pytest.raises(RuntimeError) as captured:
        command.upgrade(config, HEAD)

    assert isinstance(captured.value.__cause__, sqlite3.OperationalError)
    assert _revision(path) == BASE
    assert not (PHASE7_TABLES & _objects(path, "table"))
    assert not {name for name in _objects(path, "trigger") if name.startswith("trg_fts_")}


@pytest.mark.parametrize(
    "failure_marker",
    [
        "CREATE VIRTUAL TABLE unified_search_fts",
        "INSERT INTO search_documents(entity_type",
        "VALUES('rebuild')",
        "VALUES('integrity-check', 1)",
    ],
)
def test_failed_phase7_migration_is_unstamped_and_rejected(
    tmp_path, monkeypatch, failure_marker
) -> None:
    path = tmp_path / f"failed-{abs(hash(failure_marker))}.sqlite3"
    config = _config(path)
    command.upgrade(config, BASE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO tasks(id,title,description,status,priority,created_at_utc,updated_at_utc,version) "
            "VALUES('00000000-0000-0000-0000-000000000002','Base survives','',"
            "'TODO','NORMAL',1,1,1)"
        )
    original = Connection.exec_driver_sql

    def fail_migration(self, statement, *args, **kwargs):
        if failure_marker in statement:
            raise sqlite3.OperationalError(f"injected migration failure at {failure_marker}")
        return original(self, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "exec_driver_sql", fail_migration)
    with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
        command.upgrade(config, HEAD)

    assert _revision(path) == BASE
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT title FROM tasks WHERE id='00000000-0000-0000-0000-000000000002'"
        ).fetchone() == ("Base survives",)

    paths = AppPaths(data_dir=tmp_path, log_dir=tmp_path / "logs")
    app_config = AppConfig(
        paths=paths,
        database=DatabaseConfig(path=path),
        log_level="CRITICAL",
    )
    with pytest.raises((RuntimeError, sqlite3.Error, SQLAlchemyError)):
        create_application(app_config)
