"""The ``Backend`` object the QML pages talk to (through the ``Store`` facade).

It owns no data of its own: every list the pages show is rebuilt from the application
services whenever a domain event says it may have changed, and every change the user makes is
a service call.  Dates cross into QML as epoch milliseconds, ids stay the backend's string ids,
and lists travel as JSON text (so ``null`` stays ``null`` in JavaScript).

Threading: CRUD runs on the GUI thread (local SQLite is fast).  Backups and restores run on a
worker thread and report back with signals; while a restore runs the object is ``busy`` and
refuses new changes.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from productivity_app.bootstrap import Application
from productivity_app.core.clock import Clock, SystemClock
from productivity_app.core.events import DomainEvent, DomainEventType, EventSubscription
from productivity_app.core.exceptions import (
    AppError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.schemas.backups import BackupPageQuery, DeleteBackup, RestoreBackup
from productivity_app.schemas.focus import (
    AdvanceFocusSequence,
    CancelFocusSequence,
    CreateFocusSequence,
    FocusSequenceQuery,
    FocusSequenceView,
)
from productivity_app.schemas.notes import CreateNote, NoteQuery, UpdateNote
from productivity_app.schemas.reminders import (
    CreateReminder,
    ReminderPageQuery,
    ReminderQuery,
    ReminderSummary,
    SnoozeReminder,
    UpdateReminder,
)
from productivity_app.schemas.settings import SettingsPatch, SettingsView
from productivity_app.schemas.tasks import CreateTask, TaskQuery, UpdateTask
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    TimerPresetQuery,
    TimerQuery,
)
from productivity_app.ui.bridge import convert

_LOGGER = logging.getLogger("productivity_app.ui.bridge")

PAGE: Final = 200
SNOOZE: Final = timedelta(minutes=10)
COMPLETED_TASK_DAYS: Final = 14
SETTINGS_DEBOUNCE_MS: Final = 250
DEFAULT_PRESETS: Final = ((5, "5m"), (10, "10m"), (15, "15m"), (60, "1h"))
_NOTE_TINTS: Final = frozenset({"none", "plum", "rose", "amber", "moss", "teal", "slate", "sand"})
_PRIORITIES: Final = frozenset({"low", "normal", "high", "urgent"})
# The order lists refresh in (settings first: the others read them).
_KINDS: Final = (
    "settings", "notes", "tasks", "reminders", "timers", "presets", "trash", "backups", "focus",
    "stats",
)


def _dump(data: object) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _json_property(key: str, signal: Signal) -> Property:
    return Property(str, lambda self: self._json[key], notify=signal)


def kinds_for(event_type: str) -> set[str]:
    """Which lists a domain event can change."""
    tail = event_type.rsplit("_", 1)[-1]
    gone_or_back = tail in {"DELETED", "RESTORED", "PURGED"}
    if event_type.startswith("NOTE_"):
        return {"notes", "trash"} if gone_or_back else {"notes"}
    if event_type.startswith("TASK_"):
        kinds = {"tasks"}
        if gone_or_back:
            kinds.add("trash")
        if tail == "PURGED":
            kinds.add("reminders")
        return kinds
    if event_type.startswith("REMINDER_"):
        return {"reminders"}
    if event_type.startswith("TIMER_PRESET_"):
        return {"presets"}
    if event_type.startswith("TIMER_"):
        return {"timers", "focus", "stats"} if tail == "COMPLETED" else {"timers", "focus"}
    if event_type.startswith("FOCUS_"):
        return {"focus", "timers", "stats"}
    if event_type == "SETTINGS_CHANGED":
        return {"settings", "focus"}
    return set()


class Backend(QObject):
    """See the module docstring.  QML sees it as the context property ``Backend``."""

    settingsChanged = Signal()
    notesChanged = Signal()
    tasksChanged = Signal()
    remindersChanged = Signal()
    timersChanged = Signal()
    presetsChanged = Signal()
    trashChanged = Signal()
    backupsChanged = Signal()
    focusChanged = Signal()
    statsChanged = Signal()
    busyChanged = Signal()

    # For the shell (Python only)
    reminderFired = Signal(object)    # the reminder's dict
    focusCompleted = Signal(str)      # the toast text
    timerFinished = Signal(str)       # the timer's name
    backupRestored = Signal(str)      # the toast text
    notice = Signal(str)              # a small message to show the user (an error)

    _eventArrived = Signal(str)       # from any thread to the GUI thread
    _jobDone = Signal(object)

    settingsJson = _json_property("settings", settingsChanged)
    notesJson = _json_property("notes", notesChanged)
    tasksJson = _json_property("tasks", tasksChanged)
    remindersJson = _json_property("reminders", remindersChanged)
    timersJson = _json_property("timers", timersChanged)
    presetsJson = _json_property("presets", presetsChanged)
    trashJson = _json_property("trash", trashChanged)
    backupsJson = _json_property("backups", backupsChanged)
    focusJson = _json_property("focus", focusChanged)
    statsJson = _json_property("stats", statsChanged)

    def __init__(
        self,
        application: Application,
        *,
        clock: Clock | None = None,
        sync_jobs: bool = False,
        timezone: str | None = None,
    ) -> None:
        super().__init__()
        self.app = application
        self.clock: Clock = clock or SystemClock()
        self.sync_jobs = sync_jobs
        self.timezone = timezone or convert.local_timezone_name()
        self._busy = False
        self._json: dict[str, str] = {kind: "null" for kind in _KINDS}
        self.data: dict[str, Any] = {}
        self._versions: dict[str, dict[str, int]] = {"note": {}, "task": {}, "reminder": {}, "timer": {}}
        self._trash_versions: dict[tuple[str, str], int] = {}
        self._subscriptions: list[EventSubscription] = []
        self._dirty: set[str] = set()
        self._notified: set[tuple[str, int]] = set()
        self._pending_settings: dict[str, Any] = {}
        self._settings: SettingsView | None = None
        self.startup_handler: Callable[[bool], str] | None = None
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(SETTINGS_DEBOUNCE_MS)
        self._settings_timer.timeout.connect(self.flush_settings)
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(0)
        self._flush_timer.timeout.connect(self._flush)
        self._eventArrived.connect(self._on_event)
        self._jobDone.connect(self._finish_job)
        self.subscribe()
        self.ensure_presets()
        self.refresh_all()
        self.prime_notified()

    # ── Plumbing ────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self.clock.now_utc()

    def now_ms(self) -> int:
        return convert.to_ms(self.now()) or 0

    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        if self._busy != busy:
            self._busy = busy
            self.busyChanged.emit()

    def subscribe(self) -> None:
        """(Re)subscribe to every domain event.  A restore swaps the event bus, so the
        subscriptions are made again afterwards."""
        for subscription in self._subscriptions:
            try:
                subscription.close()
            except Exception:  # an old, closed bus
                _LOGGER.debug("could not close an old event subscription", exc_info=True)
        self._subscriptions = [
            self.app.subscribe(event_type, self._on_domain_event) for event_type in DomainEventType
        ]

    def close(self) -> None:
        self._settings_timer.stop()
        self.flush_settings()
        for subscription in self._subscriptions:
            try:
                subscription.close()
            except Exception:
                _LOGGER.debug("could not close an event subscription", exc_info=True)
        self._subscriptions = []

    def _on_domain_event(self, event: DomainEvent) -> None:
        self._eventArrived.emit(event.event_type)    # queued when it comes from another thread

    @Slot(str)
    def _on_event(self, event_type: str) -> None:
        kinds = kinds_for(event_type)
        if kinds:
            self._dirty |= kinds
            self._flush_timer.start()                # a mutation slot flushes sooner, below

    def _notice(self, message: str) -> None:
        _LOGGER.warning("%s", message)
        self.notice.emit(message)

    def _set(self, kind: str, data: Any) -> None:
        text = _dump(data)
        self.data[kind] = data
        if text != self._json.get(kind):
            self._json[kind] = text
            getattr(self, f"{kind}Changed").emit()

    def refresh(self, *kinds: str) -> None:
        for kind in _KINDS:
            if kind in kinds:
                try:
                    getattr(self, f"_refresh_{kind}")()
                except AppError as error:
                    _LOGGER.warning("could not refresh %s: %s", kind, error)

    def refresh_all(self) -> None:
        self.refresh(*_KINDS)

    @Slot()
    def refreshAll(self) -> None:  # noqa: N802
        self.refresh_all()

    @Slot()
    def _flush(self) -> None:
        if not self._dirty or self._busy:       # (a restore refreshes everything when it ends)
            return
        kinds, self._dirty = self._dirty, set()
        self.refresh(*kinds)

    def _call(self, kind: str, entity_id: str, action: Callable[[int], object]) -> bool:
        """Run a versioned service call: on a stale version reload once and retry, else tell the
        user.  Returns whether the change went through."""
        if self._busy:
            return False
        try:
            try:
                action(self._current_version(kind, entity_id))
            except StaleVersionError:
                action(self._fetch_version(kind, entity_id))
        except StaleVersionError:
            self._notice("That item was changed somewhere else. Please try again.")
            self._dirty.add(kind + "s")
            return False
        except NotFoundError:
            self._dirty.add(kind + "s")
            return False
        except ValidationError as error:
            if "does not change" in str(error):
                return True
            self._notice(str(error))
            return False
        except AppError as error:
            self._notice(str(error))
            return False
        return True

    def _current_version(self, kind: str, entity_id: str) -> int:
        cached = self._versions[kind].get(entity_id)
        return cached if cached is not None else self._fetch_version(kind, entity_id)

    def _fetch_version(self, kind: str, entity_id: str) -> int:
        if kind == "note":
            return self.app.notes.get_note(entity_id).version
        if kind == "task":
            return self.app.tasks.get_task(entity_id).version
        if kind == "reminder":
            return self.app.reminders.get_reminder(entity_id).version
        return self.app.timers.get_timer(entity_id).version

    def _guard(self, action: Callable[[], object]) -> Any:
        """Run a non-versioned service call; errors become a notice."""
        if self._busy:
            return None
        try:
            return action()
        except AppError as error:
            self._notice(str(error))
            return None

    def _pages(self, fetch: Callable[[int], tuple[Any, ...]]) -> list[Any]:
        found: list[Any] = []
        while True:
            page = fetch(len(found))
            found.extend(page)
            if len(page) < PAGE:
                return found

    def _run_job(
        self, work: Callable[[], object], done: Callable[[object, Exception | None], None]
    ) -> None:
        """Run ``work`` off the GUI thread, then ``done(result, error)`` on it."""
        if self.sync_jobs:
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            done(result, error)
            return

        def target() -> None:
            payload: tuple[Any, ...]
            try:
                payload = (done, work(), None)
            except Exception as exc:
                payload = (done, None, exc)
            self._jobDone.emit(payload)

        threading.Thread(target=target, name="bridge-job", daemon=True).start()

    @Slot(object)
    def _finish_job(self, payload: tuple[Callable[[object, Exception | None], None], object, Exception | None]) -> None:
        done, result, error = payload
        done(result, error)

    # ── Settings ────────────────────────────────────────────────────
    @property
    def settings(self) -> SettingsView:
        assert self._settings is not None
        return self._settings

    def _refresh_settings(self) -> None:
        self._settings = self.app.settings.get_settings()
        values = convert.settings_dict(self._settings)
        values.update(self._pending_settings)          # what the user just changed wins
        self._set("settings", values)

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:  # noqa: N802
        """A setting the user changed (QML key, QML value).  Saved after a short pause, so a
        slider being dragged writes once."""
        if key not in convert.SETTING_KEYS or self._busy:
            return
        if convert.settings_dict(self.settings).get(key) == value and key not in self._pending_settings:
            return
        if key == "startWithWindows" and self.startup_handler is not None:
            error = self.startup_handler(bool(value))
            if error:
                self._notice(error)
            self.refresh("settings")
            return
        self._pending_settings[key] = value
        self._settings_timer.start()

    def set_setting_now(self, key: str, value: Any) -> None:
        self.setSetting(key, value)
        self.flush_settings()

    @Slot()
    def flushSettings(self) -> None:  # noqa: N802
        self.flush_settings()

    def flush_settings(self) -> None:
        if not self._pending_settings:
            return
        self._settings_timer.stop()
        pending, self._pending_settings = self._pending_settings, {}
        fields: dict[str, Any] = {}
        for key, value in pending.items():
            try:
                field, converted = convert.setting_field(key, value)
            except (KeyError, TypeError, ValueError):
                continue
            fields[field] = converted
        if not fields:
            return
        try:
            for attempt in range(2):
                try:
                    patch = SettingsPatch(expected_version=self.settings.version, **fields)
                    self._settings = self.app.settings.update_settings(patch)
                    break
                except StaleVersionError:
                    if attempt:
                        raise
                    self._settings = self.app.settings.get_settings()
        except (AppError, ValueError) as error:
            self._notice(f"Could not save a setting: {error}")
        self.refresh("settings")
        self._flush()

    # ── Notes ───────────────────────────────────────────────────────
    def _refresh_notes(self) -> None:
        notes = self._pages(
            lambda offset: self.app.notes.list_notes(
                NoteQuery(limit=PAGE, offset=offset, sort="updated_desc")
            )
        )
        self._versions["note"] = {note.id: note.version for note in notes}
        self._set("notes", [convert.note_dict(note) for note in notes])

    @Slot(str, str, str, result=str)
    def addNote(self, title: str, body: str, tint: str) -> str:  # noqa: N802
        if title.strip() == "" and body.strip() == "":
            return ""
        view = self._guard(
            lambda: self.app.notes.create_note(
                CreateNote(title=title[:300], content=body, color=tint if tint in _NOTE_TINTS else "none")  # type: ignore[arg-type]
            )
        )
        self._flush()
        return str(view.id) if view else ""

    @Slot(str, "QVariantMap")
    def updateNote(self, note_id: str, fields: dict[str, Any]) -> None:  # noqa: N802
        values: dict[str, Any] = {}
        if "title" in fields:
            values["title"] = str(fields["title"])[:300]
        if "body" in fields:
            values["content"] = str(fields["body"])
        if "tint" in fields and fields["tint"] in _NOTE_TINTS:
            values["color"] = fields["tint"]
        if "pinned" in fields:
            values["is_pinned"] = bool(fields["pinned"])
        if "archived" in fields:
            values["is_archived"] = bool(fields["archived"])
        current = next((n for n in self.data.get("notes", []) if n["id"] == note_id), None)
        if current is not None:     # saving what did not change would only reorder the notes
            same = {"title": "title", "content": "body", "color": "tint", "is_pinned": "pinned", "is_archived": "archived"}
            values = {k: v for k, v in values.items() if current[same[k]] != v}
        if values:
            self._call(
                "note", note_id,
                lambda v: self.app.notes.update_note(note_id, UpdateNote(expected_version=v, **values)),
            )
        self._flush()

    @Slot(str)
    def deleteNote(self, note_id: str) -> None:  # noqa: N802
        self._call("note", note_id, lambda v: self.app.notes.delete_note(note_id, v))
        self._flush()

    # ── Tasks ───────────────────────────────────────────────────────
    def _refresh_tasks(self) -> None:
        since = self.now() - timedelta(days=COMPLETED_TASK_DAYS)
        open_tasks = self._pages(
            lambda offset: self.app.tasks.list_tasks(
                TaskQuery(statuses=("TODO", "IN_PROGRESS"), limit=PAGE, offset=offset, sort="created_oldest")
            )
        )
        done_tasks = self._pages(
            lambda offset: self.app.tasks.list_tasks(
                TaskQuery(
                    statuses=("COMPLETED",), completed_from_utc=since, limit=PAGE, offset=offset,
                    sort="created_oldest",
                )
            )
        )
        tasks = [*open_tasks, *done_tasks]
        self._versions["task"] = {task.id: task.version for task in tasks}
        self._set("tasks", [convert.task_dict(task) for task in tasks])

    @staticmethod
    def _due(fields: Mapping[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if "due" in fields:
            due = fields["due"]
            values["due_at_utc"] = None if due is None else convert.from_ms(due)
            values["due_has_time"] = bool(fields.get("hasTime", False)) if due is not None else True
        elif "hasTime" in fields:
            values["due_has_time"] = bool(fields["hasTime"])
        return values

    @Slot(str, "QVariantMap", result=str)
    def addTask(self, title: str, fields: dict[str, Any]) -> str:  # noqa: N802
        if title.strip() == "":
            return ""
        priority = str(fields.get("priority", "normal"))
        view = self._guard(
            lambda: self.app.tasks.create_task(
                CreateTask(
                    title=title.strip()[:300],
                    description=str(fields.get("description", "")),
                    priority=(priority if priority in _PRIORITIES else "normal").upper(),  # type: ignore[arg-type]
                    **self._due(fields),
                )
            )
        )
        self._flush()
        return str(view.id) if view else ""

    @Slot(str, "QVariantMap")
    def updateTask(self, task_id: str, fields: dict[str, Any]) -> None:  # noqa: N802
        values = self._due(fields)
        if "title" in fields:
            values["title"] = str(fields["title"]).strip()[:300]
        if "description" in fields:
            values["description"] = str(fields["description"])
        if "priority" in fields and fields["priority"] in _PRIORITIES:
            values["priority"] = str(fields["priority"]).upper()
        current = next((t for t in self.data.get("tasks", []) if t["id"] == task_id), None)
        if current is not None:
            same = {"title": current["title"], "description": current["description"], "priority": current["priority"].upper()}
            values = {k: v for k, v in values.items() if same.get(k, object()) != v}
            if "due_at_utc" in values and convert.to_ms(values["due_at_utc"]) == current["due"] and values.get("due_has_time") == current["hasTime"]:
                values.pop("due_at_utc")
                values.pop("due_has_time", None)
        if values:
            self._call(
                "task", task_id,
                lambda v: self.app.tasks.update_task(task_id, UpdateTask(expected_version=v, **values)),
            )
        if "done" in fields:
            self._set_task_done(task_id, bool(fields["done"]))
        self._flush()

    def _set_task_done(self, task_id: str, done: bool) -> None:
        current = next((t for t in self.data.get("tasks", []) if t["id"] == task_id), None)
        if current is None or bool(current["done"]) == done:
            return
        self._call(
            "task", task_id,
            lambda v: (self.app.tasks.complete_task if done else self.app.tasks.reopen_task)(task_id, v),
        )

    @Slot(str)
    def toggleTask(self, task_id: str) -> None:  # noqa: N802
        current = next((t for t in self.data.get("tasks", []) if t["id"] == task_id), None)
        if current is not None:
            self._set_task_done(task_id, not current["done"])
        self._flush()

    @Slot(str)
    def deleteTask(self, task_id: str) -> None:  # noqa: N802
        self._call("task", task_id, lambda v: self.app.tasks.delete_task(task_id, v))
        self._flush()

    # ── Reminders ───────────────────────────────────────────────────
    def _refresh_reminders(self) -> None:
        active = self._pages(
            lambda offset: self.app.reminders.list_reminders(
                ReminderQuery(
                    statuses=("PENDING", "TRIGGERED", "OVERDUE"), limit=PAGE, offset=offset,
                    sort="due_soonest",
                )
            )
        )
        past = self.app.reminders.list_history(ReminderPageQuery(limit=60))
        reminders: list[ReminderSummary] = [
            *active, *(r for r in past if r.status in convert.PAST_STATUSES)
        ]
        self._versions["reminder"] = {reminder.id: reminder.version for reminder in reminders}
        listing = [convert.reminder_dict(reminder) for reminder in reminders]
        self._set("reminders", listing)
        keys = {(r["id"], r["due"]) for r in listing}
        self._notified &= keys

    def prime_notified(self) -> None:
        """Reminders already due when the app starts are shown as due, without an alert."""
        now = self.now_ms()
        for reminder in self.data.get("reminders", []):
            if not reminder["done"] and reminder["due"] <= now:
                self._notified.add((reminder["id"], reminder["due"]))

    def due_reminders(self) -> list[dict[str, Any]]:
        """Reminders that came due and have not alerted yet (each is returned once)."""
        now = self.now_ms()
        fired: list[dict[str, Any]] = []
        for reminder in self.data.get("reminders", []):
            key = (reminder["id"], reminder["due"])
            if not reminder["done"] and reminder["due"] <= now and key not in self._notified:
                self._notified.add(key)
                fired.append(reminder)
        return fired

    def fire_due_reminders(self) -> int:
        fired = self.due_reminders()
        for reminder in fired:
            self.reminderFired.emit(reminder)
        return len(fired)

    def alert_reminder(self, reminder_id: str) -> None:
        """Show the alert for a reminder now (whether or not it already alerted)."""
        for reminder in self.data.get("reminders", []):
            if reminder["id"] == reminder_id:
                self._notified.add((reminder["id"], reminder["due"]))
                self.reminderFired.emit(reminder)

    def _mark_if_due(self, view: ReminderSummary) -> None:
        if view.due_at_utc <= self.now():
            self._notified.add((view.id, convert.to_ms(view.due_at_utc) or 0))

    @staticmethod
    def _link(link: Mapping[str, Any] | None) -> dict[str, str | None]:
        task_id = note_id = None
        if link and link.get("kind") == "task":
            task_id = str(link["id"])
        elif link and link.get("kind") == "note":
            note_id = str(link["id"])
        return {"task_id": task_id, "note_id": note_id}

    @Slot(str, float, "QVariantMap", result=str)
    def addReminder(self, title: str, due: float, fields: dict[str, Any]) -> str:  # noqa: N802
        if title.strip() == "":
            return ""
        view = self._guard(
            lambda: self.app.reminders.create_reminder(
                CreateReminder(
                    title=title.strip()[:300],
                    message=str(fields.get("message", "")),
                    due_at_utc=convert.from_ms(due),
                    scheduled_timezone=self.timezone,
                    repeat=convert.rule_from_qml(fields.get("repeat")),
                    **self._link(fields.get("link")),
                )
            )
        )
        if view is not None:
            self._mark_if_due(view)       # already due when created: shown as due, no alert
        self._flush()
        return str(view.id) if view else ""

    def _reminder(self, reminder_id: str) -> dict[str, Any] | None:
        return next((r for r in self.data.get("reminders", []) if r["id"] == reminder_id), None)

    def _reopen_if_done(self, reminder_id: str) -> None:
        current = self._reminder(reminder_id)
        if current is not None and current["done"]:
            self._call("reminder", reminder_id, lambda v: self.app.reminders.reopen_reminder(reminder_id, v))

    @Slot(str, "QVariantMap")
    def updateReminder(self, reminder_id: str, fields: dict[str, Any]) -> None:  # noqa: N802
        current = self._reminder(reminder_id)
        values: dict[str, Any] = {}
        for key in ("title", "message"):
            if key in fields and (current is None or fields[key] != current[key]):
                values[key] = str(fields[key]).strip()[:300] if key == "title" else str(fields[key])
        if "link" in fields:
            values.update(self._link(fields["link"]))
        if values and current is not None and current["done"]:
            self._notice("Reopen this reminder to edit it.")
        elif values:
            self._call(
                "reminder", reminder_id,
                lambda v: self.app.reminders.update_reminder(
                    reminder_id, UpdateReminder(expected_version=v, **values)
                ),
            )
        self._flush()

    @Slot(str)
    def snoozeReminder(self, reminder_id: str) -> None:  # noqa: N802
        due = self.now() + SNOOZE
        self._call(
            "reminder", reminder_id,
            lambda v: self.app.reminders.snooze_reminder(
                reminder_id, SnoozeReminder(expected_version=v, due_at_utc=due)
            ),
        )
        self._flush()

    @Slot(str, float)
    def setReminderTime(self, reminder_id: str, ms: float) -> None:  # noqa: N802
        """A new time: the reminder is open again and alerts again."""
        self._reopen_if_done(reminder_id)
        due = convert.from_ms(ms)
        self._call(
            "reminder", reminder_id,
            lambda v: self.app.reminders.update_reminder(
                reminder_id, UpdateReminder(expected_version=v, due_at_utc=due)
            ),
        )
        self._flush()
        current = self._reminder(reminder_id)
        if current is not None and current["due"] <= self.now_ms():
            self._notified.add((reminder_id, current["due"]))

    @Slot(str, "QVariantMap")
    def setReminderRepeat(self, reminder_id: str, rule: Any) -> None:  # noqa: N802
        """Pick a repeat rule (``null``: never).  The due time moves to the next valid
        occurrence at or after the current one; a finished reminder is open again."""
        repeat = convert.rule_from_qml(rule if isinstance(rule, Mapping) else None)
        current = self._reminder(reminder_id)
        if current is None:
            return
        changes: dict[str, Any] = {"repeat": repeat}
        if repeat is not None and current["done"]:
            changes["due_at_utc"] = convert.from_ms(max(current["due"], self.now_ms()))
        if current["done"]:
            self._reopen_if_done(reminder_id)
        self._call(
            "reminder", reminder_id,
            lambda v: self.app.reminders.update_reminder(
                reminder_id, UpdateReminder(expected_version=v, **changes)
            ),
        )
        self._flush()
        after = self._reminder(reminder_id)
        if after is not None and after["due"] <= self.now_ms():
            self._notified.add((reminder_id, after["due"]))

    @Slot(str)
    def completeReminder(self, reminder_id: str) -> None:  # noqa: N802
        """Done: a one-time reminder moves to Past; a repeating one jumps to its next occurrence."""
        self._call("reminder", reminder_id, lambda v: self.app.reminders.complete_reminder(reminder_id, v))
        self._flush()

    @Slot(str)
    def deleteReminder(self, reminder_id: str) -> None:  # noqa: N802
        self._call("reminder", reminder_id, lambda v: self.app.reminders.delete_reminder(reminder_id, v))
        self._flush()

    # ── Timers and presets ──────────────────────────────────────────
    def _refresh_timers(self) -> None:
        timers = self._pages(
            lambda offset: self.app.timers.list_timers(
                TimerQuery(
                    states=("READY", "RUNNING", "PAUSED", "FINISHED"), exclude_focus=True,
                    limit=PAGE, offset=offset, sort="created_oldest",
                )
            )
        )
        self._versions["timer"] = {timer.id: timer.version for timer in timers}
        self._set("timers", [convert.timer_dict(timer) for timer in timers])

    def ensure_presets(self) -> None:
        try:
            if self.app.timers.list_presets(TimerPresetQuery(limit=1)):
                return
            for minutes, name in DEFAULT_PRESETS:
                self.app.timers.create_preset(CreateTimerPreset(name=name, duration_ms=minutes * 60000))
        except AppError as error:
            _LOGGER.warning("could not create the timer presets: %s", error)

    def _refresh_presets(self) -> None:
        presets = self.app.timers.list_presets(TimerPresetQuery(limit=PAGE))
        self._set("presets", [convert.preset_dict(preset) for preset in presets])

    def _timer(self, timer_id: str) -> dict[str, Any] | None:
        return next((t for t in self.data.get("timers", []) if t["id"] == timer_id), None)

    @Slot(str)
    def toggleTimer(self, timer_id: str) -> None:  # noqa: N802
        current = self._timer(timer_id)
        if current is None:
            return
        timers = self.app.timers
        if current["state"] == "RUNNING" and (current["deadline"] or 0) <= self.now_ms():
            self.reconcile_timers()               # it ran out a moment ago
            current = self._timer(timer_id) or current
        state = current["state"]
        if state == "RUNNING":
            self._call("timer", timer_id, lambda v: timers.pause_timer(timer_id, v))
        elif state == "PAUSED":
            self._call("timer", timer_id, lambda v: timers.resume_timer(timer_id, v))
        elif state == "READY":
            self._call("timer", timer_id, lambda v: timers.start_timer(timer_id, v))
        else:       # finished (or just ran out): start again from the full time
            if self._call("timer", timer_id, lambda v: timers.reset_timer(timer_id, v)):
                self._call("timer", timer_id, lambda v: timers.start_timer(timer_id, v))
        self._flush()

    @Slot(str, int, bool, result=str)
    def addTimer(self, name: str, seconds: int, start: bool) -> str:  # noqa: N802
        if seconds <= 0 or self._busy:
            return ""
        view = self._guard(
            lambda: self.app.timers.create_timer(
                CreateTimer(name=name.strip()[:120] or "Timer", planned_duration_ms=int(seconds) * 1000)
            )
        )
        if view is None:
            return ""
        if start:
            self._call("timer", view.id, lambda v: self.app.timers.start_timer(view.id, v))
        self._flush()
        return str(view.id)

    @Slot(str, result=str)
    def startPreset(self, preset_id: str) -> str:  # noqa: N802
        preset = next((p for p in self.data.get("presets", []) if p["id"] == preset_id), None)
        if preset is None:
            return ""
        view = self._guard(
            lambda: self.app.timers.create_timer(
                CreateTimer(name=convert.preset_timer_name(preset["ms"]), preset_id=preset_id)
            )
        )
        if view is None:
            return ""
        self._call("timer", view.id, lambda v: self.app.timers.start_timer(view.id, v))
        self._flush()
        return str(view.id)

    @Slot(str)
    def resetTimer(self, timer_id: str) -> None:  # noqa: N802
        current = self._timer(timer_id)
        if current is not None and current["state"] != "READY":
            self._call("timer", timer_id, lambda v: self.app.timers.reset_timer(timer_id, v))
        self._flush()

    @Slot(str)
    def deleteTimer(self, timer_id: str) -> None:  # noqa: N802
        current = self._timer(timer_id)
        if current is None:
            return
        if current["state"] in {"RUNNING", "PAUSED"}:
            self._call("timer", timer_id, lambda v: self.app.timers.cancel_timer(timer_id, v))
        self._call("timer", timer_id, lambda v: self.app.timers.delete_timer(timer_id, v))
        self._flush()

    def reconcile_timers(self) -> None:
        """Finish every timer whose time is up and announce it (a focus phase moves on)."""
        if self._busy:
            return
        try:
            completed = self.app.timers.reconcile_expired_timers()
        except AppError as error:
            _LOGGER.warning("could not reconcile timers: %s", error)
            return
        if not completed:
            return
        sequence = self._active_sequence()
        for timer in completed:
            if sequence is not None and timer.id == sequence.current_session.timer.id:
                self.focus_phase_finished(sequence)
            else:
                self.timerFinished.emit(timer.name)
        self._flush()

    def next_deadline_ms(self) -> int | None:
        """The earliest moment a running timer ends (a focus phase included)."""
        deadlines = [t["deadline"] for t in self.data.get("timers", []) if t["deadline"]]
        focus = self.data.get("focus") or {}
        if focus.get("deadline"):
            deadlines.append(focus["deadline"])
        return min(deadlines, default=None)

    # ── Focus (the Pomodoro cycle is a focus sequence) ──────────────
    def _active_sequence(self) -> FocusSequenceView | None:
        try:
            active = self.app.focus.list_sequences(FocusSequenceQuery(states=("ACTIVE",), limit=1))
            return self.app.focus.get_sequence(active[0].id) if active else None
        except AppError:
            return None

    def _refresh_focus(self) -> None:
        settings = self.settings
        sequence = self._active_sequence()
        if sequence is None:
            seconds = round(settings.pomodoro_work_duration_ms / 1000)
            state: dict[str, Any] = {
                "active": False, "sequenceId": None, "phase": 0, "total": seconds,
                "remaining": seconds, "running": False, "deadline": None,
            }
        else:
            timer = sequence.current_session.timer
            running = timer.state == "RUNNING" and timer.effective_state != "FINISHED"
            state = {
                "active": True,
                "sequenceId": sequence.id,
                "phase": sequence.current_phase_index,
                "total": round(timer.planned_duration_ms / 1000),
                "remaining": 0 if timer.effective_state == "FINISHED" else -(-timer.remaining_ms // 1000),
                "running": running,
                "deadline": convert.to_ms(timer.deadline_at_utc) if running else None,
            }
        self._set("focus", state)

    def _refresh_stats(self) -> None:
        zone = ZoneInfo(self.timezone)
        totals = self.app.focus.daily_totals(7, zone)
        today = totals[-1]
        self._set(
            "stats",
            {
                "sessionsToday": today.sessions,
                "minutesToday": today.minutes,
                "weekMinutes": sum(day.minutes for day in totals),
                "streak": self.app.focus.streak(zone),
            },
        )

    def focus_message(self, finished_phase: int) -> str:
        short = round(self.settings.short_break_duration_ms / 60000)
        long = round(self.settings.long_break_duration_ms / 60000)
        if finished_phase == 7:
            return "Break over — ready for session 1?"
        if finished_phase == 6:
            return f"Cycle complete — take a {long} min break"
        if finished_phase % 2 == 0:
            return f"Focus session done — time for a {short} min break"
        return f"Break over — ready for session {(finished_phase + 1) // 2 + 1}?"

    def focus_phase_finished(self, sequence: FocusSequenceView) -> None:
        """A phase's timer ran out: move the sequence on and tell the user."""
        try:
            try:
                self.app.focus.advance_sequence(
                    sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
                )
            except StaleVersionError:
                fresh = self._active_sequence()
                if fresh is None:
                    return
                self.app.focus.advance_sequence(
                    fresh.id, AdvanceFocusSequence(expected_version=fresh.version)
                )
        except (AppError, ValueError) as error:
            _LOGGER.warning("could not advance the focus sequence: %s", error)
            return
        self.focusCompleted.emit(self.focus_message(sequence.current_phase_index))

    def focus_catch_up(self) -> None:
        """A phase that ended while the app was closed: move on (and say so)."""
        sequence = self._active_sequence()
        if sequence is not None and sequence.current_session.timer.state == "FINISHED":
            self.focus_phase_finished(sequence)
            self._flush()

    def _focus_sequence_or_new(self) -> FocusSequenceView:
        sequence = self._active_sequence()
        return sequence if sequence is not None else self.app.focus.create_sequence(CreateFocusSequence())

    def _focus_try(self, action: Callable[[], object]) -> None:
        if self._busy:
            return
        for attempt in range(2):
            try:
                action()
                break
            except StaleVersionError:
                if attempt:
                    self._notice("The timer changed somewhere else. Please try again.")
            except (AppError, ValueError) as error:
                self._notice(str(error))
                break
        self._dirty |= {"focus", "timers"}
        self._flush()

    @Slot(bool)
    def focusSetRunning(self, running: bool) -> None:  # noqa: N802
        def action() -> None:
            timers = self.app.timers
            if running:
                timer = self._focus_sequence_or_new().current_session.timer
                if timer.state == "READY":
                    timers.start_timer(timer.id, timer.version)
                elif timer.state == "PAUSED":
                    timers.resume_timer(timer.id, timer.version)
            else:
                sequence = self._active_sequence()
                if sequence is not None and sequence.current_session.timer.state == "RUNNING":
                    timer = sequence.current_session.timer
                    timers.pause_timer(timer.id, timer.version)

        self._focus_try(action)

    @Slot()
    def focusSkip(self) -> None:  # noqa: N802
        def action() -> None:
            timers = self.app.timers
            sequence = self._focus_sequence_or_new()
            timer = sequence.current_session.timer
            was_running = timer.state == "RUNNING"
            if timer.state == "READY":
                timer = timers.start_timer(timer.id, timer.version)
            if timer.state in {"RUNNING", "PAUSED"}:
                timer = timers.complete_timer(timer.id, timer.version)
            advanced = self.app.focus.advance_sequence(
                sequence.id, AdvanceFocusSequence(expected_version=sequence.version)
            )
            if was_running and advanced.state == "ACTIVE":
                nxt = advanced.current_session.timer
                timers.start_timer(nxt.id, nxt.version)

        self._focus_try(action)

    @Slot()
    def focusReset(self) -> None:  # noqa: N802
        """Pause and put the current session back to its full length."""
        def action() -> None:
            sequence = self._active_sequence()
            if sequence is not None:
                timer = sequence.current_session.timer
                if timer.state in {"RUNNING", "PAUSED"}:
                    self.app.timers.reset_timer(timer.id, timer.version)

        self._focus_try(action)

    @Slot()
    def focusStartOver(self) -> None:  # noqa: N802
        """Pause and go back to the first session at its full length."""
        def action() -> None:
            sequence = self._active_sequence()
            if sequence is None:
                return
            timer = sequence.current_session.timer
            if timer.state in {"RUNNING", "PAUSED"}:
                self.app.timers.reset_timer(timer.id, timer.version)
            self.app.focus.cancel_sequence(
                sequence.id, CancelFocusSequence(expected_version=sequence.version)
            )

        self._focus_try(action)

    # ── Trash ───────────────────────────────────────────────────────
    def _refresh_trash(self) -> None:
        items = self.app.trash.list_trash()
        self._trash_versions = {(item.kind.lower(), item.id): item.version for item in items}
        self._set("trash", [convert.trash_dict(item) for item in items])

    @Slot(str, str)
    def restoreItem(self, kind: str, item_id: str) -> None:  # noqa: N802
        version = self._trash_versions.get((kind, item_id))
        if version is None:
            return
        restore = self.app.notes.restore_note if kind == "note" else self.app.tasks.restore_task
        for _ in range(2):
            try:
                restore(item_id, self._trash_versions[(kind, item_id)])
                break
            except StaleVersionError:
                self._refresh_trash()
                if (kind, item_id) not in self._trash_versions:
                    break
            except AppError as error:
                self._notice(str(error))
                break
        self._dirty |= {"notes", "tasks", "trash"}
        self._flush()

    @Slot(str, str)
    def deleteForever(self, kind: str, item_id: str) -> None:  # noqa: N802
        self._guard(lambda: self.app.trash.delete_forever(kind.upper(), item_id))  # type: ignore[arg-type]
        self._dirty |= {"trash", "reminders"}
        self._flush()

    @Slot()
    def emptyTrash(self) -> None:  # noqa: N802
        self._guard(self.app.trash.empty_trash)
        self._dirty |= {"trash", "reminders"}
        self._flush()

    @Slot()
    def purgeTrash(self) -> None:  # noqa: N802
        """Anything that has been in the Trash for 30 days is deleted for good."""
        self._guard(self.app.trash.purge_expired)
        self._flush()

    # ── Backups ─────────────────────────────────────────────────────
    def _refresh_backups(self) -> None:
        backups = self.app.backups.list_backups(BackupPageQuery(limit=PAGE))
        listing = [convert.backup_dict(backup) for backup in backups]
        listing.sort(key=lambda item: item["at"], reverse=True)
        self._set("backups", listing)

    def _trim_backups(self) -> None:
        """Keep the newest ``keep_backups`` backups (the list shows every kind)."""
        keep = self.settings.keep_backups
        listing = [convert.backup_dict(b) for b in self.app.backups.list_backups(BackupPageQuery(limit=PAGE))]
        listing.sort(key=lambda item: item["at"], reverse=True)
        for old in listing[keep:]:
            try:
                self.app.backups.delete_backup(DeleteBackup(filename=old["id"]))
            except AppError as error:
                _LOGGER.warning("could not delete the old backup %s: %s", old["id"], error)

    @Slot()
    def backUpNow(self) -> None:  # noqa: N802
        if self._busy:
            return

        def work() -> object:
            created = self.app.backups.create_backup()
            self._trim_backups()
            return created

        self._run_job(work, self._backup_done)

    def _backup_done(self, _result: object, error: Exception | None) -> None:
        if error is not None:
            self._notice(f"Backup failed: {error}")
        self.refresh("backups", "settings")

    def auto_backup_if_due(self) -> None:
        """At startup and hourly: a daily automatic backup, pruned to the newest few."""
        if self._busy:
            return
        self._run_job(
            lambda: self.app.backups.run_auto_backup_if_due(self.now()), self._backup_done
        )

    @Slot(str)
    def deleteBackup(self, backup_id: str) -> None:  # noqa: N802
        self._guard(lambda: self.app.backups.delete_backup(DeleteBackup(filename=backup_id)))
        self.refresh("backups")

    @Slot(str)
    def restoreBackup(self, backup_id: str) -> None:  # noqa: N802
        if self._busy:
            return
        backup = next((b for b in self.data.get("backups", []) if b["id"] == backup_id), None)
        if backup is None:
            return
        path = Path(self.app.backups.backup_directory) / backup_id
        self._set_busy(True)

        def work() -> object:
            return self.app.restore_backup(RestoreBackup(source_path=path))

        def done(_result: object, error: Exception | None) -> None:
            self._set_busy(False)
            self.subscribe()          # a restore replaces the event bus
            self._pending_settings.clear()
            self.refresh_all()
            self.prime_notified()
            if error is not None:
                self._notice(f"Restore failed: {error}")
                return
            self.backupRestored.emit(f"Backup from {datetime.fromtimestamp(backup['at'] / 1000):%b %d} restored")

        self._run_job(work, done)

    # ── Clock-dependent work the scheduler calls ────────────────────
    def refresh_clock_dependent(self) -> None:
        """Once a minute (and after waking up): the day may have changed."""
        self.refresh("stats", "tasks", "trash")
