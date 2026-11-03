"""Transaction boundary and post-commit event collection."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from productivity_app.core.events import DomainEvent, InProcessEventBus
from productivity_app.core.exceptions import DatabaseBusyError
from productivity_app.core.lifecycle import ApplicationLifecycleGate, _Lease
from productivity_app.repositories.focus import FocusRepository
from productivity_app.repositories.folders import FolderRepository
from productivity_app.repositories.notes import NoteRepository
from productivity_app.repositories.reminders import ReminderRepository
from productivity_app.repositories.search import SearchRepository
from productivity_app.repositories.settings import SettingsRepository
from productivity_app.repositories.tags import TagRepository
from productivity_app.repositories.tasks import TaskRepository
from productivity_app.repositories.timers import TimerRepository

_LOGGER = logging.getLogger(__name__)


class _LifecycleSession:
    """Require a captured repository/session to remain inside its UoW lease."""

    def __init__(self, session: Session, lease: _Lease | None) -> None:
        self._session = session
        self._lease = lease

    def __getattr__(self, name: str) -> Any:
        if self._lease is not None:
            self._lease.require_active()
        value = getattr(self._session, name)
        if not callable(value):
            return value

        def guarded(*args: object, **kwargs: object) -> Any:
            if self._lease is not None:
                self._lease.require_active()
            return value(*args, **kwargs)

        return guarded


class SqlAlchemyUnitOfWork:
    def __init__(
        self, session_factory: sessionmaker[Session], event_bus: InProcessEventBus,
        lifecycle_gate: ApplicationLifecycleGate | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._lifecycle_gate = lifecycle_gate
        self._lifecycle_lease: _Lease | None = None
        self._raw_session: Session | None = None
        self.session: _LifecycleSession | None = None
        self.settings: SettingsRepository | None = None
        self.folders: FolderRepository | None = None
        self.tags: TagRepository | None = None
        self.notes: NoteRepository | None = None
        self.tasks: TaskRepository | None = None
        self.reminders: ReminderRepository | None = None
        self.timers: TimerRepository | None = None
        self.search: SearchRepository | None = None
        self.focus: FocusRepository | None = None
        self._events: list[DomainEvent] = []
        self._used = False
        self._write_lock_acquired = False

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        if self._used:
            raise RuntimeError("Unit of work instances are single-use.")
        self._used = True
        if self._lifecycle_gate is not None:
            self._lifecycle_lease = self._lifecycle_gate.operation()
        try:
            self._raw_session = self._session_factory()
            self.session = _LifecycleSession(self._raw_session, self._lifecycle_lease)
            self.settings = SettingsRepository(self.session)  # type: ignore[arg-type]
            self.folders = FolderRepository(self.session)  # type: ignore[arg-type]
            self.tags = TagRepository(self.session)  # type: ignore[arg-type]
            self.notes = NoteRepository(self.session)  # type: ignore[arg-type]
            self.tasks = TaskRepository(self.session)  # type: ignore[arg-type]
            self.reminders = ReminderRepository(self.session)  # type: ignore[arg-type]
            self.timers = TimerRepository(self.session)  # type: ignore[arg-type]
            self.search = SearchRepository(self.session)  # type: ignore[arg-type]
            self.focus = FocusRepository(self.session)  # type: ignore[arg-type]
        except BaseException:
            if self._raw_session is not None:
                try:
                    self._raw_session.close()
                except Exception:
                    _LOGGER.exception("Session close failed while entering a unit of work")
                self._raw_session = None
            self.session = None
            if self._lifecycle_lease is not None:
                self._lifecycle_lease.close()
                self._lifecycle_lease = None
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        primary_exception_active = exc_type is not None
        try:
            if primary_exception_active and self.session is not None:
                self._rollback_without_masking()
        finally:
            self._events.clear()
            try:
                self._close_session(preserve_primary_exception=primary_exception_active)
            finally:
                self.session = None
                self._raw_session = None
                self.settings = None
                self.folders = None
                self.tags = None
                self.notes = None
                self.tasks = None
                self.reminders = None
                self.timers = None
                self.search = None
                self.focus = None
                if self._lifecycle_lease is not None:
                    self._lifecycle_lease.close()
                    self._lifecycle_lease = None

    def collect_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    def acquire_write_lock(self) -> None:
        """Acquire SQLite's writer reservation before authoritative mutation reads.

        SQLite foreign keys cannot express that a referenced soft-deleted row is
        inactive.  ``BEGIN IMMEDIATE`` serializes writers before services validate
        active parents and related rows, making those validations authoritative for
        the transaction that follows.
        """
        if self.session is None:
            raise RuntimeError("Unit of work has not been entered.")
        if self._write_lock_acquired:
            return
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as exc:
            message = str(exc.orig).lower() if exc.orig is not None else ""
            if "database is locked" in message or "database is busy" in message:
                raise DatabaseBusyError("Database is busy; retry the operation.") from exc
            raise
        self._write_lock_acquired = True

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work has not been entered.")
        try:
            self.session.commit()
        except Exception:
            self._events.clear()
            self._rollback_without_masking()
            raise
        events, self._events = self._events, []
        for event in events:
            for failure in self._event_bus.publish(event):
                _LOGGER.exception("Post-commit event subscriber failed", exc_info=failure)

    def _rollback_without_masking(self) -> None:
        if self.session is None:
            return
        try:
            self.session.rollback()
        except Exception:
            _LOGGER.exception("Rollback failed while preserving the original transaction error")

    def _close_session(self, *, preserve_primary_exception: bool) -> None:
        if self._raw_session is None:
            return
        if not preserve_primary_exception:
            self._raw_session.close()
            return
        try:
            self._raw_session.close()
        except Exception:
            _LOGGER.exception(
                "Session close failed while preserving the original transaction error"
            )


UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


def create_unit_of_work_factory(
    session_factory: sessionmaker[Session], event_bus: InProcessEventBus,
    lifecycle_gate: ApplicationLifecycleGate | None = None,
) -> UnitOfWorkFactory:
    return lambda: SqlAlchemyUnitOfWork(session_factory, event_bus, lifecycle_gate)
