"""Composition root and safe runtime replacement for the local application."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import ClassVar

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from productivity_app.core.clock import Clock, SystemClock
from productivity_app.core.config import AppConfig, default_config
from productivity_app.core.events import (
    DomainEventType,
    EventCallback,
    EventSubscription,
    InProcessEventBus,
)
from productivity_app.core.exceptions import (
    BackupRecoveryError,
    BackupRestoreError,
    BackupValidationError,
    DatabaseCorruptionError,
    MigrationError,
)
from productivity_app.core.ids import Uuid4Generator
from productivity_app.core.lifecycle import ApplicationLifecycleGate
from productivity_app.core.logging_config import configure_logging, shutdown_logging
from productivity_app.db.engine import create_sqlite_engine
from productivity_app.db.session import create_session_factory
from productivity_app.db.unit_of_work import UnitOfWorkFactory, create_unit_of_work_factory
from productivity_app.schemas.backups import BackupRestoreView, RestoreBackup
from productivity_app.services.backups import BackupService
from productivity_app.services.focus import FocusService
from productivity_app.services.notes import NoteService
from productivity_app.services.reminders import ReminderService
from productivity_app.services.search import SearchService
from productivity_app.services.settings import SettingsService
from productivity_app.services.tasks import TaskService
from productivity_app.services.timers import TimerService
from productivity_app.services.trash import TrashService

_MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "db" / "migrations"


def _alembic_config(database_url: str, busy_timeout_ms: int) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIRECTORY))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("productivity.busy_timeout_ms", str(busy_timeout_ms))
    return config


def upgrade_database(database_url: str, busy_timeout_ms: int) -> None:
    command.upgrade(_alembic_config(database_url, busy_timeout_ms), "head")


def _validate_database_integrity(engine: Engine) -> None:
    with engine.connect() as connection:
        results = [row[0] for row in connection.execute(text("PRAGMA quick_check"))]
    if results != ["ok"]:
        raise DatabaseCorruptionError(f"SQLite quick_check failed: {results!r}")


def _verify_fts5_available(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("CREATE VIRTUAL TABLE temp.__phase7_fts_probe USING fts5(value)")
            connection.exec_driver_sql("DROP TABLE temp.__phase7_fts_probe")
    except Exception as exc:
        raise MigrationError("SQLite FTS5 support is required.") from exc


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """An indivisible generation of engine, gate, bus, and services."""

    engine: Engine
    session_factory: sessionmaker[Session]
    unit_of_work_factory: UnitOfWorkFactory
    settings: SettingsService
    notes: NoteService
    tasks: TaskService
    trash: TrashService
    reminders: ReminderService
    timers: TimerService
    search: SearchService
    backups: BackupService
    focus: FocusService
    event_bus: InProcessEventBus
    gate: ApplicationLifecycleGate


@dataclass(slots=True)
class Application:
    _RUNTIME_ATTRIBUTES: ClassVar[frozenset[str]] = frozenset({
        "engine", "session_factory", "unit_of_work_factory", "settings", "notes", "tasks", "trash",
        "reminders", "timers", "search", "backups", "focus", "_event_bus", "_gate",
    })
    engine: Engine
    session_factory: sessionmaker[Session]
    unit_of_work_factory: UnitOfWorkFactory
    settings: SettingsService
    notes: NoteService
    tasks: TaskService
    trash: TrashService
    reminders: ReminderService
    timers: TimerService
    search: SearchService
    backups: BackupService
    focus: FocusService
    _event_bus: InProcessEventBus
    _gate: ApplicationLifecycleGate
    _config: AppConfig
    _clock: Clock
    _closed: bool = False
    _runtime_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _runtime: RuntimeComponents | None = field(default=None, init=False, repr=False)

    def __getattribute__(self, name: str) -> object:
        # Public runtime access is a single generation read, rather than a
        # sequence of mutable attributes changed one by one during restore.
        if name in Application._RUNTIME_ATTRIBUTES:
            runtime = object.__getattribute__(self, "_runtime")
            if runtime is not None:
                mapped = "event_bus" if name == "_event_bus" else "gate" if name == "_gate" else name
                return getattr(runtime, mapped)
        return object.__getattribute__(self, name)

    def __post_init__(self) -> None:
        self._runtime = RuntimeComponents(
            engine=self.engine,
            session_factory=self.session_factory,
            unit_of_work_factory=self.unit_of_work_factory,
            settings=self.settings,
            notes=self.notes,
            tasks=self.tasks,
            trash=self.trash,
            reminders=self.reminders,
            timers=self.timers,
            search=self.search,
            backups=self.backups,
            focus=self.focus,
            event_bus=self._event_bus,
            gate=self._gate,
        )

    def _active_runtime(self) -> RuntimeComponents:
        with self._runtime_lock:
            assert self._runtime is not None
            return self._runtime

    @property
    def is_closed(self) -> bool:
        return self._closed

    def subscribe(self, event_type: DomainEventType | str, callback: EventCallback) -> EventSubscription:
        value = str(event_type)
        if value not in {item.value for item in DomainEventType}:
            raise ValueError(f"Unknown domain event type: {value}")
        runtime = self._active_runtime()
        with runtime.gate.operation():
            return runtime.event_bus.observe(value, callback)

    def close(self) -> None:
        if self._closed:
            return
        runtime = self._active_runtime()
        runtime.gate.begin_transition()
        try:
            runtime.engine.dispose()
            runtime.gate.close()
            self._closed = True
        finally:
            if not self._closed:
                self._gate.abort_transition()
        shutdown_logging()

    def restore_backup(self, command: RestoreBackup) -> BackupRestoreView:
        if self._closed:
            raise BackupRestoreError("Application is closed.")
        original_source = command.source_path
        live = self._config.database.path.resolve()
        runtime = self._active_runtime()
        source_snapshot = runtime.backups._validate_path(original_source)
        if source_snapshot.path == live:
            raise BackupRestoreError("Cannot restore from the live database file.")
        source_view = runtime.backups._as_view(source_snapshot)
        runtime.gate.begin_transition()
        stage = live.with_name(f".{live.name}.{Uuid4Generator().new()}.restore.partial")
        disposed = False
        replaced = False
        safety = None
        try:
            runtime.backups.stage_approved_restore_source(
                original_source, source_snapshot, stage
            )
            runtime.backups._fsync_file(stage)
            safety = runtime.backups._create_backup("PRE_RESTORE")
            with runtime.engine.begin() as connection:
                checkpoint = connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).first()
                if checkpoint is not None and checkpoint[0] != 0:
                    raise BackupRestoreError("Database checkpoint was busy.")
            runtime.engine.dispose()
            disposed = True
            runtime.gate.close()
            self._remove_sidecars(live)
            os.replace(stage, live)
            replaced = True
            self._install(_compose_application(self._config, self._clock, run_migrations=False))
            return BackupRestoreView(source_filename=source_snapshot.filename, source_sha256=source_view.sha256, safety_backup=safety.model_copy(), completed_at_utc=self._clock.now_utc())
        except Exception as exc:
            if not disposed:
                runtime.gate.abort_transition()
                if isinstance(exc, BackupValidationError):
                    raise
                raise BackupRestoreError("Backup restore preflight failed.") from exc
            try:
                if replaced:
                    assert safety is not None
                    safety_path = runtime.backups.backup_directory / safety.filename
                    runtime.backups._copy_sqlite(safety_path, stage)
                    runtime.backups._validate_path(stage)
                    runtime.backups._fsync_file(stage)
                    self._remove_sidecars(live)
                    os.replace(stage, live)
                self._install(_compose_application(self._config, self._clock, run_migrations=False))
            except Exception as recovery_exc:
                self._closed = True
                failure = BackupRecoveryError("Restore and safety recovery both failed.")
                failure.restore_failure = exc  # type: ignore[attr-defined]
                failure.recovery_failure = recovery_exc  # type: ignore[attr-defined]
                raise failure from recovery_exc
            raise BackupRestoreError("Restore failed; original database was recovered.") from exc
        finally:
            try:
                if stage.exists():
                    stage.unlink()
            except OSError:
                pass

    def _install(self, other: Application) -> None:
        replacement = other._active_runtime()
        # The FTS index is derived infrastructure.  Rebuild it before the new
        # runtime becomes visible so a restored application never exposes a
        # partially verified search generation.
        replacement.search.rebuild_full_text_index()
        with self._runtime_lock:
            self._runtime = replacement
            self._closed = False

    @staticmethod
    def _remove_sidecars(live: Path) -> None:
        parent = live.parent.resolve()
        for suffix in ("-wal", "-shm"):
            sidecar = live.with_name(f"{live.name}{suffix}")
            try:
                if sidecar.parent.resolve() != parent:
                    raise BackupRestoreError("Unsafe SQLite sidecar path.")
                if not sidecar.exists():
                    continue
                if not BackupService._is_regular_file(sidecar):
                    raise BackupRestoreError("Unsafe SQLite sidecar artifact.")
                sidecar.unlink()
            except BackupRestoreError:
                raise
            except OSError as exc:
                raise BackupRestoreError("Could not remove SQLite sidecar.") from exc


def _compose_application(config: AppConfig, clock: Clock, *, run_migrations: bool) -> Application:
    engine = create_sqlite_engine(config.database.path, busy_timeout_ms=config.database.busy_timeout_ms)
    try:
        _verify_fts5_available(engine)
        if run_migrations:
            upgrade_database(str(engine.url), config.database.busy_timeout_ms)
        _validate_database_integrity(engine)
        event_bus = InProcessEventBus()
        session_factory = create_session_factory(engine)
        gate = ApplicationLifecycleGate()
        uow_factory = create_unit_of_work_factory(session_factory, event_bus, gate)
        settings = SettingsService(uow_factory, clock)
        notes, tasks = NoteService(uow_factory, clock), TaskService(uow_factory, clock)
        trash = TrashService(uow_factory, clock)
        reminders, timers = ReminderService(uow_factory, clock), TimerService(uow_factory, clock)
        search = SearchService(uow_factory, clock)
        focus = FocusService(uow_factory, clock)
        backups = BackupService(
            config.database.path, config.database.busy_timeout_ms, clock, Uuid4Generator(), gate,
            settings,
        )
        settings.get_settings()
        timers.reconcile_expired_timers()
        return Application(
            engine=engine,
            session_factory=session_factory,
            unit_of_work_factory=uow_factory,
            settings=settings,
            notes=notes,
            tasks=tasks,
            trash=trash,
            reminders=reminders,
            timers=timers,
            search=search,
            backups=backups,
            focus=focus,
            _event_bus=event_bus,
            _gate=gate,
            _config=config,
            _clock=clock,
        )
    except BaseException:
        engine.dispose()
        raise


def create_application(config: AppConfig | None = None, clock: Clock | None = None) -> Application:
    app_config = config or default_config()
    application_clock = clock or SystemClock()
    app_config.paths.ensure_data_directory_exists()
    configure_logging(app_config.paths.log_dir, app_config.log_level)
    try:
        return _compose_application(app_config, application_clock, run_migrations=True)
    except BaseException:
        shutdown_logging()
        raise
