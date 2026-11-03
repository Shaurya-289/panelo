"""Behavioral regression tests for Phase 0 foundation guarantees."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from productivity_app import bootstrap
from productivity_app.bootstrap import (
    _alembic_config,
    _validate_database_integrity,
    create_application,
)
from productivity_app.core import logging_config
from productivity_app.core.events import DomainEvent, InProcessEventBus
from productivity_app.core.exceptions import DatabaseCorruptionError, DatabaseError, ValidationError
from productivity_app.core.logging_config import _OWNED_HANDLER, configure_logging, shutdown_logging
from productivity_app.db.engine import configure_and_verify_sqlite_pragmas
from productivity_app.db.types import UTCDateTime, datetime_to_epoch_microseconds
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork
from productivity_app.schemas.settings import SettingsPatch


def test_startup_is_independent_of_current_working_directory(
    app_config, clock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unrelated_directory = tmp_path / "unrelated"
    unrelated_directory.mkdir()
    monkeypatch.chdir(unrelated_directory)
    app = create_application(app_config, clock)
    try:
        assert app.settings.get_settings().version == 1
    finally:
        app.close()


def test_migrated_schema_exposes_phase_zero_contract(application) -> None:
    inspector = inspect(application.engine)
    expected_tables = {
        "notes", "tasks", "reminders", "reminder_events", "timer_presets", "timers", "timer_runs", "app_settings",
        "notification_deliveries", "external_notifications",
    }
    assert expected_tables <= set(inspector.get_table_names())
    assert not {"folders", "tags", "note_tags", "task_tags"} & set(inspector.get_table_names())
    task_columns = {column["name"]: column for column in inspector.get_columns("tasks")}
    assert task_columns["id"]["nullable"] is False
    assert task_columns["due_at_utc"]["nullable"] is True
    assert inspector.get_foreign_keys("tasks") == []
    task_checks = {constraint["name"] for constraint in inspector.get_check_constraints("tasks")}
    assert {"ck_tasks_priority_values", "ck_tasks_status_values", "ck_tasks_terminal_timestamps"} <= task_checks
    timer_indexes = {index["name"]: index for index in inspector.get_indexes("timer_runs")}
    assert timer_indexes["ux_timer_runs_one_open"]["unique"] == 1
    preset_indexes = {index["name"]: index for index in inspector.get_indexes("timer_presets")}
    assert preset_indexes["ux_timer_presets_active_position"]["unique"] == 1
    assert preset_indexes["ux_timer_presets_active_name"]["unique"] == 1


def test_initial_migration_can_downgrade_and_upgrade(application) -> None:
    config = _alembic_config(str(application.engine.url), 5_000)
    command.downgrade(config, "base")
    assert "app_settings" not in inspect(application.engine).get_table_names()
    command.upgrade(config, "head")
    assert "app_settings" in inspect(application.engine).get_table_names()


def test_database_invariants_reject_invalid_rows(application) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    session = application.session_factory()
    try:
        session.execute(text("INSERT INTO timer_presets VALUES ('first', 'First', 'first', 1000, 0, 0, NULL, :n, :n, 1)"), {"n": datetime_to_epoch_microseconds(now)})
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(text("INSERT INTO timer_presets VALUES ('second', 'Other', 'other', 1000, 0, 0, NULL, :n, :n, 1)"), {"n": datetime_to_epoch_microseconds(now)})
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(text("INSERT INTO tasks (id, title, description, status, priority, created_at_utc, updated_at_utc, version) VALUES ('bad-task', 'Task', '', 'TODO', 'BAD', :n, :n, 1)"), {"n": datetime_to_epoch_microseconds(now)})
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(text("INSERT INTO reminder_events (id, reminder_id, event_type, occurred_at_utc) VALUES ('event', 'missing-reminder', 'X', 1)"))
            session.commit()
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize(
    "value",
    [
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(1900, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        datetime(2026, 9, 24, 18, 30, 5, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        datetime(2500, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
def test_utc_codec_round_trips_exact_microseconds(value: datetime) -> None:
    codec = UTCDateTime()
    stored = codec.process_bind_param(value, None)  # type: ignore[arg-type]
    assert codec.process_result_value(stored, None) == value.astimezone(UTC)  # type: ignore[arg-type]


def test_utc_codec_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        datetime_to_epoch_microseconds(datetime(2026, 1, 1))


def test_logging_is_idempotent_utc_and_falls_back_to_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logger = configure_logging(tmp_path, "INFO")
    owned_handlers = [handler for handler in logger.handlers if getattr(handler, _OWNED_HANDLER, False)]
    assert len(owned_handlers) == 1
    configure_logging(tmp_path, "DEBUG")
    assert len([handler for handler in logger.handlers if getattr(handler, _OWNED_HANDLER, False)]) == 1
    record = logging.LogRecord(logger.name, logging.INFO, __file__, 1, "message", (), None)
    record.created = 0
    assert owned_handlers[0].formatter is not None
    assert owned_handlers[0].formatter.format(record).startswith("1970-01-01T00:00:00Z")
    shutdown_logging()

    def raise_os_error(*_: object, **__: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "mkdir", raise_os_error)
    fallback = configure_logging(tmp_path / "unavailable", "INFO")
    assert isinstance(
        next(handler for handler in fallback.handlers if getattr(handler, _OWNED_HANDLER, False)),
        logging.StreamHandler,
    )
    shutdown_logging()


def _owned_stderr_handler() -> logging.Handler:
    logger = logging.getLogger("productivity_app")
    handlers = [handler for handler in logger.handlers if getattr(handler, _OWNED_HANDLER, False)]
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)
    assert not isinstance(handlers[0], logging.FileHandler)
    return handlers[0]


def test_bootstrap_continues_when_log_directory_is_a_regular_file(
    app_config, clock, tmp_path: Path
) -> None:
    app_config.paths.log_dir.write_text("not a directory", encoding="utf-8")
    app = create_application(app_config, clock)
    try:
        assert app.settings.get_settings().version == 1
        _owned_stderr_handler()
    finally:
        app.close()


def test_bootstrap_continues_when_log_file_handler_cannot_open(
    app_config, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_file_handler(*_: object, **__: object) -> logging.Handler:
        raise OSError("unavailable")

    monkeypatch.setattr(logging_config, "RotatingFileHandler", fail_file_handler)
    app = create_application(app_config, clock)
    try:
        assert app.settings.get_settings().version == 1
        _owned_stderr_handler()
    finally:
        app.close()


def test_uow_failure_and_subscriber_paths(application) -> None:
    bus = InProcessEventBus()
    published: list[str] = []
    bus.subscribe("event", lambda event: published.append(event.event_id))
    bus.subscribe("event", lambda event: (_ for _ in ()).throw(RuntimeError("subscriber failed")))
    uow = SqlAlchemyUnitOfWork(application.session_factory, bus)
    event = DomainEvent("event", "1", datetime(2026, 9, 24, tzinfo=UTC), "event-1")
    with uow:
        assert uow.settings is not None
        uow.settings.update_if_version(1, {"theme": "DARK"}, datetime(2026, 9, 24, tzinfo=UTC))
        uow.collect_event(event)
        uow.commit()
    assert published == ["event-1"]
    with pytest.raises(RuntimeError), uow:
        pass


def test_uow_does_not_publish_events_after_rollback(application) -> None:
    bus = InProcessEventBus()
    published: list[str] = []
    bus.subscribe("event", lambda event: published.append(event.event_id))
    uow = SqlAlchemyUnitOfWork(application.session_factory, bus)
    with pytest.raises(RuntimeError), uow:
        uow.collect_event(DomainEvent("event", "1", datetime(2026, 9, 24, tzinfo=UTC), "event-2"))
        raise RuntimeError("abort")
    assert published == []


def test_uow_commit_failure_clears_events_and_preserves_primary_error() -> None:
    class FailingSession:
        def __init__(self) -> None:
            self.rollback_called = False

        def commit(self) -> None:
            raise ValueError("commit failed")

        def rollback(self) -> None:
            self.rollback_called = True
            raise RuntimeError("rollback also failed")

        def close(self) -> None:
            return None

    session = FailingSession()
    bus = InProcessEventBus()
    published: list[str] = []
    bus.subscribe("event", lambda event: published.append(event.event_id))
    uow = SqlAlchemyUnitOfWork(lambda: session, bus)  # type: ignore[arg-type]
    event = DomainEvent("event", "1", datetime(2026, 9, 24, tzinfo=UTC), "event-3")
    with uow:
        uow.collect_event(event)
        with pytest.raises(ValueError, match="commit failed"):
            uow.commit()
    assert session.rollback_called
    assert published == []


@pytest.mark.parametrize(
    ("primary_exception", "rollback_error", "close_error", "commit_error", "expected_error"),
    [
        (True, True, True, False, ValueError),
        (True, False, True, False, ValueError),
        (False, False, True, False, RuntimeError),
        (False, True, True, True, ValueError),
    ],
)
def test_uow_cleanup_preserves_primary_errors_and_finalizes_state(
    primary_exception: bool,
    rollback_error: bool,
    close_error: bool,
    commit_error: bool,
    expected_error: type[Exception],
) -> None:
    class ControlledSession:
        def __init__(self) -> None:
            self.rollback_calls = 0
            self.close_calls = 0

        def commit(self) -> None:
            if commit_error:
                raise ValueError("commit failure")

        def rollback(self) -> None:
            self.rollback_calls += 1
            if rollback_error:
                raise RuntimeError("rollback failure")

        def close(self) -> None:
            self.close_calls += 1
            if close_error:
                raise RuntimeError("close failure")

    session = ControlledSession()
    uow = SqlAlchemyUnitOfWork(lambda: session, InProcessEventBus())  # type: ignore[arg-type]
    event = DomainEvent("event", "aggregate", datetime(2026, 9, 24, tzinfo=UTC), "cleanup-event")
    with pytest.raises(expected_error), uow:
        uow.collect_event(event)
        if primary_exception:
            raise ValueError("primary failure")
        if commit_error:
            uow.commit()
    assert uow._events == []
    assert uow.session is None
    assert uow.settings is None
    assert session.close_calls == 1
    if primary_exception or commit_error:
        assert session.rollback_calls >= 1


def test_settings_reset_empty_patch_and_invalid_persisted_data(application) -> None:
    original = application.settings.get_settings()
    changed = application.settings.update_settings(SettingsPatch(expected_version=original.version, theme="DARK"))
    reset = application.settings.reset_settings(changed.version)
    assert reset.theme.value == "SYSTEM"
    with pytest.raises(ValidationError):
        application.settings.update_settings(SettingsPatch(expected_version=reset.version))
    with application.engine.begin() as connection:
        connection.execute(text("PRAGMA ignore_check_constraints=ON"))
        connection.execute(text("UPDATE app_settings SET theme = 'INVALID' WHERE id = 1"))
        connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
    with pytest.raises(DatabaseCorruptionError):
        application.settings.get_settings()


def test_quick_check_rejects_non_ok_rows() -> None:
    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, _: object) -> list[tuple[str]]:
            return [("corrupt",)]

    class FakeEngine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

    with pytest.raises(DatabaseCorruptionError):
        _validate_database_integrity(FakeEngine())  # type: ignore[arg-type]


def test_pragma_policy_rejects_unaccepted_effective_values() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.statement = ""

        def execute(self, statement: str) -> None:
            self.statement = statement

        def fetchone(self) -> tuple[object] | None:
            values = {
                "PRAGMA journal_mode=WAL": ("delete",),
                "PRAGMA foreign_keys": (1,),
                "PRAGMA synchronous": (1,),
                "PRAGMA busy_timeout": (5_000,),
            }
            return values.get(self.statement)

        def close(self) -> None:
            return None

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

    with pytest.raises(DatabaseError):
        configure_and_verify_sqlite_pragmas(FakeConnection(), busy_timeout_ms=5_000)  # type: ignore[arg-type]


def test_startup_failure_disposes_the_partially_initialized_engine(
    app_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = bootstrap.create_sqlite_engine(app_config.database.path)
    disposed: list[bool] = []
    original_dispose = engine.dispose

    def dispose() -> None:
        disposed.append(True)
        original_dispose()

    def failed_check(_: object) -> None:
        raise DatabaseCorruptionError("simulated corruption")

    monkeypatch.setattr(bootstrap, "create_sqlite_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(bootstrap, "upgrade_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_validate_database_integrity", failed_check)
    monkeypatch.setattr(engine, "dispose", dispose)
    with pytest.raises(DatabaseCorruptionError):
        bootstrap.create_application(app_config)
    assert disposed == [True]
