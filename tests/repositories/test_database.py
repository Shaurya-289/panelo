from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from productivity_app.db.engine import create_sqlite_engine
from productivity_app.db.models.initial import AppSettings


def test_sqlite_pragmas_are_enabled(application) -> None:
    with application.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert str(connection.scalar(text("PRAGMA journal_mode"))).lower() == "wal"
        assert connection.scalar(text("PRAGMA synchronous")) == 1
        assert connection.scalar(text("PRAGMA busy_timeout")) == 5_000


def test_failed_transaction_rolls_back(application) -> None:
    session = application.session_factory()
    try:
        session.add(
            AppSettings(id=2, updated_at_utc=application.settings.get_settings().updated_at_utc)
        )
        try:
            session.commit()
        except Exception:
            session.rollback()
        assert session.get(AppSettings, 2) is None
        assert session.get(AppSettings, 1) is not None
    finally:
        session.close()


def test_initial_schema_contains_phase_zero_tables(application) -> None:
    expected = {
        "folders",
        "tags",
        "notes",
        "tasks",
        "reminders",
        "reminder_events",
        "timer_presets",
        "timers",
        "timer_runs",
        "app_settings",
        "notification_deliveries",
        "external_notifications",
        "note_tags",
        "task_tags",
    }
    with application.engine.connect() as connection:
        names = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).scalars()
        )
    assert expected <= names


def test_failed_uow_commit_is_atomic_across_a_new_physical_connection(application, app_config) -> None:
    with pytest.raises(IntegrityError), application.unit_of_work_factory() as uow:
        assert uow.session is not None
        uow.session.execute(
            text(
                "INSERT INTO tags (id, name, normalized_name, deleted_at_utc, "
                "created_at_utc, updated_at_utc, version) "
                "VALUES ('tag-one', 'One', 'one', NULL, 1, 1, 1)"
            )
        )
        uow.session.execute(
            text(
                "INSERT INTO tags (id, name, normalized_name, deleted_at_utc, "
                "created_at_utc, updated_at_utc, version) "
                "VALUES ('tag-two', 'Two', 'one', NULL, 1, 1, 1)"
            )
        )
        uow.commit()

    application.engine.dispose()
    fresh_engine = create_sqlite_engine(app_config.database.path)
    try:
        with fresh_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM tags")) == 0
    finally:
        fresh_engine.dispose()
