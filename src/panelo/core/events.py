"""A deliberately small post-commit in-process event bus."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import RLock


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    entity_id: str
    occurred_at_utc: datetime
    event_id: str
    payload: dict[str, object] = field(default_factory=dict)


EventCallback = Callable[[DomainEvent], None]


class DomainEventType(StrEnum):
    NOTE_CREATED = "NOTE_CREATED"
    NOTE_UPDATED = "NOTE_UPDATED"
    NOTE_PINNED = "NOTE_PINNED"
    NOTE_UNPINNED = "NOTE_UNPINNED"
    NOTE_ARCHIVED = "NOTE_ARCHIVED"
    NOTE_UNARCHIVED = "NOTE_UNARCHIVED"
    NOTE_DELETED = "NOTE_DELETED"
    NOTE_RESTORED = "NOTE_RESTORED"
    NOTE_PURGED = "NOTE_PURGED"
    TASK_CREATED = "TASK_CREATED"
    TASK_UPDATED = "TASK_UPDATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_RETURNED_TO_TODO = "TASK_RETURNED_TO_TODO"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_REOPENED = "TASK_REOPENED"
    TASK_DELETED = "TASK_DELETED"
    TASK_RESTORED = "TASK_RESTORED"
    TASK_PURGED = "TASK_PURGED"
    REMINDER_CREATED = "REMINDER_CREATED"
    REMINDER_UPDATED = "REMINDER_UPDATED"
    REMINDER_SNOOZED = "REMINDER_SNOOZED"
    REMINDER_COMPLETED = "REMINDER_COMPLETED"
    REMINDER_DISMISSED = "REMINDER_DISMISSED"
    REMINDER_CANCELLED = "REMINDER_CANCELLED"
    REMINDER_REOPENED = "REMINDER_REOPENED"
    REMINDER_DELETED = "REMINDER_DELETED"
    TIMER_CREATED = "TIMER_CREATED"
    TIMER_UPDATED = "TIMER_UPDATED"
    TIMER_STARTED = "TIMER_STARTED"
    TIMER_PAUSED = "TIMER_PAUSED"
    TIMER_RESUMED = "TIMER_RESUMED"
    TIMER_RESET = "TIMER_RESET"
    TIMER_COMPLETED = "TIMER_COMPLETED"
    TIMER_CANCELLED = "TIMER_CANCELLED"
    TIMER_DELETED = "TIMER_DELETED"
    TIMER_RESTORED = "TIMER_RESTORED"
    TIMER_PRESET_CREATED = "TIMER_PRESET_CREATED"
    TIMER_PRESET_UPDATED = "TIMER_PRESET_UPDATED"
    TIMER_PRESET_MOVED = "TIMER_PRESET_MOVED"
    TIMER_PRESET_DELETED = "TIMER_PRESET_DELETED"
    TIMER_PRESET_RESTORED = "TIMER_PRESET_RESTORED"
    FOCUS_SEQUENCE_STARTED = "FOCUS_SEQUENCE_STARTED"
    FOCUS_PHASE_ADVANCED = "FOCUS_PHASE_ADVANCED"
    FOCUS_SEQUENCE_COMPLETED = "FOCUS_SEQUENCE_COMPLETED"
    FOCUS_SEQUENCE_CANCELLED = "FOCUS_SEQUENCE_CANCELLED"
    SETTINGS_CHANGED = "SETTINGS_CHANGED"


class EventSubscription:
    def __init__(self, bus: InProcessEventBus, event_type: str, callback: EventCallback) -> None:
        self._bus = bus
        self._event_type = event_type
        self._callback = callback
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._unsubscribe(self._event_type, self._callback)


class InProcessEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_type: str, callback: EventCallback) -> None:
        with self._lock:
            self._subscribers[event_type].append(callback)

    def observe(self, event_type: str, callback: EventCallback) -> EventSubscription:
        self.subscribe(event_type, callback)
        return EventSubscription(self, event_type, callback)

    def _unsubscribe(self, event_type: str, callback: EventCallback) -> None:
        with self._lock:
            callbacks = self._subscribers.get(event_type)
            if callbacks is not None:
                with suppress(ValueError):
                    callbacks.remove(callback)

    def publish(self, event: DomainEvent) -> list[Exception]:
        failures: list[Exception] = []
        with self._lock:
            callbacks = tuple(self._subscribers[event.event_type])
        for callback in callbacks:
            try:
                callback(event)
            except Exception as exc:  # Subscribers never undo a committed transaction.
                failures.append(exc)
        return failures
