"""SQLite engine creation and connection safety settings."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from productivity_app.core.exceptions import DatabaseError


def _pragma_scalar(cursor: object, statement: str) -> object:
    cursor.execute(statement)  # type: ignore[attr-defined]
    row = cursor.fetchone()  # type: ignore[attr-defined]
    return row[0] if row is not None else None


def configure_and_verify_sqlite_pragmas(
    dbapi_connection: Connection, *, busy_timeout_ms: int
) -> None:
    """Apply and verify the SQLite safety policy for one physical connection."""
    if not isinstance(busy_timeout_ms, int) or busy_timeout_ms <= 0:
        raise ValueError("busy_timeout_ms must be a positive integer.")
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        journal_mode = _pragma_scalar(cursor, "PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        foreign_keys = _pragma_scalar(cursor, "PRAGMA foreign_keys")
        synchronous = _pragma_scalar(cursor, "PRAGMA synchronous")
        busy_timeout = _pragma_scalar(cursor, "PRAGMA busy_timeout")
        if foreign_keys != 1 or str(journal_mode).lower() != "wal" or synchronous != 1:
            raise DatabaseError("SQLite connection did not accept required safety PRAGMAs.")
        if busy_timeout != busy_timeout_ms:
            raise DatabaseError("SQLite connection did not accept the configured busy timeout.")
    finally:
        cursor.close()


def sqlite_url(database_path: Path) -> str:
    return str(make_url(f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"))


def create_sqlite_engine(database_path: Path, *, busy_timeout_ms: int = 5_000) -> Engine:
    if not isinstance(busy_timeout_ms, int) or busy_timeout_ms <= 0:
        raise ValueError("busy_timeout_ms must be a positive integer.")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(database_path),
        future=True,
        poolclass=NullPool,
        connect_args={"check_same_thread": True},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection: Connection, _: object) -> None:
        configure_and_verify_sqlite_pragmas(dbapi_connection, busy_timeout_ms=busy_timeout_ms)

    return engine
