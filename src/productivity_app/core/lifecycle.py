"""In-process lifecycle coordination for database runtime replacement."""

from __future__ import annotations

from contextlib import AbstractContextManager
from threading import RLock

from productivity_app.core.exceptions import ApplicationLifecycleError


class _Lease(AbstractContextManager["_Lease"]):
    def __init__(self, gate: ApplicationLifecycleGate) -> None:
        self._gate = gate
        self._released = False

    def __enter__(self) -> _Lease:
        return self

    def close(self) -> None:
        if not self._released:
            self._released = True
            self._gate._release()

    def require_active(self) -> None:
        """Reject a captured session/repository after its UoW has finished."""
        if self._released:
            raise ApplicationLifecycleError("This unit of work is no longer active.")
        self._gate._require_available()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class ApplicationLifecycleGate:
    """Rejects work while an application closes or replaces its runtime.

    This is deliberately process-local. SQLite remains the inter-process
    serialization mechanism; the gate only protects objects owned by one
    ``Application`` instance.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._operations = 0
        self._transitioning = False
        self._closed = False

    def operation(self) -> _Lease:
        with self._lock:
            if self._closed or self._transitioning:
                raise ApplicationLifecycleError("Application runtime is unavailable.")
            self._operations += 1
        return _Lease(self)

    def _require_available(self) -> None:
        with self._lock:
            if self._closed or self._transitioning:
                raise ApplicationLifecycleError("Application runtime is unavailable.")

    def begin_transition(self) -> None:
        with self._lock:
            if self._closed:
                raise ApplicationLifecycleError("Application runtime is closed.")
            if self._transitioning or self._operations:
                raise ApplicationLifecycleError("Application has active operations.")
            self._transitioning = True

    def abort_transition(self) -> None:
        with self._lock:
            if not self._closed:
                self._transitioning = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._transitioning = True

    def _release(self) -> None:
        with self._lock:
            self._operations -= 1
