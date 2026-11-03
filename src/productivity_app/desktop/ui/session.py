"""Facade lifetime, GUI-safe events and generation-checked results."""

import logging
import traceback
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError as InputValidationError
from PySide6.QtCore import QObject, Qt, Signal, Slot

from productivity_app.core.events import DomainEvent, DomainEventType
from productivity_app.desktop.ui.actions import Action, dispatch
from productivity_app.desktop.ui.operations import Request, ServiceOperationRunner
from productivity_app.desktop.ui.state import UiState


def error_text(error: Exception) -> str:
    if isinstance(error, InputValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in error.errors(include_input=False)
        )
    code = getattr(error, "code", "")
    if code == "stale_version":
        return "Changed elsewhere. Your draft is kept. Reload or explicitly reapply after review."
    if code == "database_busy":
        return "Database is busy. Wait, then retry explicitly."
    if code == "active_timer_conflict":
        return "Another timer is running or paused. Open Timers to manage it."
    if code or isinstance(error, (ValueError, RuntimeError)):
        return str(error)
    # Exception reprs (particularly DBAPI parameter dumps) can contain full
    # draft content. Record the traceback frames without those payloads.
    logging.getLogger(__name__).error(
        "UI operation failed (%s)\n%s",
        type(error).__name__,
        "".join(traceback.format_list(traceback.extract_tb(error.__traceback__))),
    )
    return "Operation failed. Details are in the application log."


class DesktopUiSession(QObject):
    changed = Signal(object)
    _event = Signal(object)
    restored = Signal()

    def __init__(self, application: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.application = application
        self.state = UiState()
        self.runner = ServiceOperationRunner(self)
        self.callbacks: dict[int, Callable[[Any, Exception | None], None]] = {}
        self.subscriptions: list[Any] = []
        self.runner.completed.connect(self._completed)
        self._event.connect(self._deliver_event, Qt.ConnectionType.QueuedConnection)
        self.closed = False
        self.can_mutate: Callable[[], bool] = lambda: True
        self.runtime_token = id(getattr(application, "search", None))
        self.bind_events()

    def bind_events(self) -> None:
        for subscription in self.subscriptions:
            subscription.close()
        self.subscriptions.clear()
        if hasattr(self.application, "subscribe"):
            generation = self.state.runtime_generation
            for event_type in DomainEventType:
                self.subscriptions.append(
                    self.application.subscribe(
                        event_type,
                        lambda event, generation=generation: self._event.emit((generation, event)),
                    )
                )

    @Slot(object)
    def _deliver_event(self, message: tuple[int, DomainEvent]) -> None:
        if not self.closed and message[0] == self.state.runtime_generation:
            self.changed.emit(message[1])

    def call(
        self,
        action: Action,
        key: str,
        callback: Callable[[Any, Exception | None], None],
        *,
        read: bool = False,
    ) -> int:
        return self.run(lambda: dispatch(self.application, action), key, callback, read=read)

    def run(
        self,
        operation: Callable[[], Any],
        key: str,
        callback: Callable[[Any, Exception | None], None],
        *,
        read: bool = False,
    ) -> int:
        if self.closed:
            raise RuntimeError("UI session is closed")
        if not read and key != "lifecycle:exclusive" and not self.can_mutate():
            raise RuntimeError("Finish layout editing or lifecycle transition first")
        if id(getattr(self.application, "search", None)) != self.runtime_token:
            self.rebind()
        generation = self.state.next_generation(key)
        serial = self.runner.submit(key, generation, self.state.runtime_generation, read, operation)
        self.callbacks[serial] = callback
        return serial

    @Slot(object, object, object)
    def _completed(self, request: Request, value: Any, error: Exception | None) -> None:
        if (
            not self.closed
            and request.key != "lifecycle:exclusive"
            and id(getattr(self.application, "search", None)) != self.runtime_token
        ):
            self.rebind()
        callback = self.callbacks.pop(request.id, None)
        if (
            callback
            and not self.closed
            and request.runtime == self.state.runtime_generation
            and (not request.read or request.generation == self.state.generations.get(request.key))
        ):
            callback(value, error)

    def rebind(self) -> None:
        self.runtime_token = id(getattr(self.application, "search", None))
        self.state.replace_runtime()
        self.callbacks.clear()
        self.bind_events()
        self.restored.emit()

    def exclusive(
        self, operation: Callable[[], Any], callback: Callable[[Any, Exception | None], None]
    ) -> None:
        """Drain accepted mutations, then run a foreground-requested transition.

        Never terminate an executing task or interleave a restore with a UI UoW.
        The runner remains closed to new submissions throughout the transition.
        """
        if not self.runner.accepting:
            raise RuntimeError("A lifecycle transition is already pending")
        self.runner.quiesce()
        waiting = self.runner.busy

        def begin() -> None:
            if self.runner.busy:
                return
            if waiting:
                self.runner.idle.disconnect(begin)
            self.runner.resume()

            def finished(value: Any, error: Exception | None) -> None:
                if not self.closed and not self.application.is_closed:
                    self.runner.resume()
                callback(value, error)

            self.run(operation, "lifecycle:exclusive", finished)
            self.runner.quiesce()

        if waiting:
            self.runner.idle.connect(begin)
        else:
            begin()

    def close(self) -> None:
        self.closed = True
        self.runner.quiesce()
        for subscription in self.subscriptions:
            subscription.close()
        self.subscriptions.clear()
        self.callbacks.clear()
