"""Bounded explicitly requested tasks, with no worker loop."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot


@dataclass(frozen=True, slots=True)
class Request:
    id: int
    key: str
    generation: int
    runtime: int
    read: bool
    operation: Callable[[], Any]


class _Signals(QObject):
    done = Signal(object, object, object)


class _Task(QRunnable):
    def __init__(self, request: Request, signals: _Signals) -> None:
        super().__init__()
        self.request, self.signals = request, signals

    def run(self) -> None:
        try:
            value = self.request.operation()
        except Exception as exc:
            self.signals.done.emit(self.request, None, exc)
        else:
            self.signals.done.emit(self.request, value, None)


class ServiceOperationRunner(QObject):
    completed = Signal(object, object, object)
    idle = Signal()

    def __init__(self, parent: QObject | None = None, capacity: int = 32) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.signals = _Signals(self)
        self.signals.done.connect(self._done, Qt.ConnectionType.QueuedConnection)
        self.queue: deque[Request] = deque()
        self.capacity, self.accepting = capacity, True
        self.active: Request | None = None
        self.serial = 0

    def submit(
        self, key: str, generation: int, runtime: int, read: bool, operation: Callable[[], Any]
    ) -> int:
        if not self.accepting:
            raise RuntimeError("UI is closing or restoring")
        if read:
            removed = [r for r in self.queue if r.read and r.key == key]
            self.queue = deque(r for r in self.queue if r not in removed)
            for request in removed:
                self.completed.emit(request, None, RuntimeError("Read superseded"))
        if len(self.queue) >= self.capacity:
            raise RuntimeError("Too many pending actions; wait and retry")
        self.serial += 1
        self.queue.append(Request(self.serial, key, generation, runtime, read, operation))
        self._next()
        return self.serial

    def _next(self) -> None:
        if self.active is None and self.queue:
            self.active = self.queue.popleft()
            self.pool.start(_Task(self.active, self.signals))

    @Slot(object, object, object)
    def _done(self, request: Request, value: Any, error: Exception | None) -> None:
        self.active = None
        self.completed.emit(request, value, error)
        self._next()
        if not self.busy:
            self.idle.emit()

    @property
    def busy(self) -> bool:
        return self.active is not None or bool(self.queue)

    def quiesce(self) -> None:
        self.accepting = False
        discarded = [r for r in self.queue if r.read]
        self.queue = deque(r for r in self.queue if not r.read)
        for request in discarded:
            self.completed.emit(request, None, RuntimeError("Read cancelled"))

    def resume(self) -> None:
        self.accepting = True
