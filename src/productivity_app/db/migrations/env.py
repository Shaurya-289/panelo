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
    # SQLite's DDL can auto-commit, but Alembic's version-table update still needs
    # an explicit owning transaction so it is durable when the connection closes.
    with connectable.begin() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
