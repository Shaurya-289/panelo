"""Alembic environment for SQLite migrations."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from productivity_app.db import models  # noqa: F401 - import mappings into metadata
from productivity_app.db.base import Base
from productivity_app.db.engine import configure_and_verify_sqlite_pragmas

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure_sqlite_connection(dbapi_connection: object, _: object) -> None:
    """Apply the same connection policy used by the application engine."""
    timeout_ms = int(config.get_main_option("productivity.busy_timeout_ms", "5000"))
    configure_and_verify_sqlite_pragmas(  # type: ignore[arg-type]
        dbapi_connection, busy_timeout_ms=timeout_ms
    )


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(connectable, "connect", _configure_sqlite_connection)
    with connectable.connect() as connection:
        # SQLite rebuilds a table by dropping the old one, and with enforcement on that
        # drop fires every child table's ON DELETE action (cascading deletes, nulling links).
        # The pragma is a no-op inside a transaction, so it is switched off first; integrity
        # is checked explicitly before the commit instead.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        with connection.begin():
            # pysqlite does not open a transaction for DDL, which would leave a migration that
            # fails half-way partly applied.  Start one explicitly so the schema changes, the
            # data copies and the version stamp commit or roll back together.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                transactional_ddl=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"Migration left {len(violations)} foreign key violation(s); rolled back."
                )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
