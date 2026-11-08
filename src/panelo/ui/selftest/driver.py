"""Helpers for the self-test steps: real mouse and key events on a window, and easy access to
what the pages show.  Ported from the prototype's ``round5_tests.Driver``.

A step is ``(delay ms, action | None, (name, predicate) | None)``: wait, do something, then
assert something.  The prototype's small integer ids ("task 5") still work: ``Driver`` turns them
into the backend's ids through the seed's ``Aliases``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from panelo.ui.bridge import convert

LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier

Step = tuple[Any, ...]  # (delay ms, action | None, (name, predicate) | None)

_TASK_PROBES = {"taskRow", "taskCheck"}
_REMINDER_PROBES = {"reminderRow", "reminderSnooze", "reminderDone", "reminderLink"}
_TIMER_PROBES = {"focus.timerMenu", "focus.timerCard", "focus.timerPlay"}


def js(value: Any) -> Any:
    """A QML value as plain Python."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def local_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


def ms_of(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def day_at(offset: int, hour: int = 0, minute: int = 0) -> int:
    """Local ms of the day `offset` days from today at hour:minute."""
    base = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return ms_of(base + timedelta(days=offset))


class Driver:
    """Mouse and key helpers for the Full App, plus the store's data as plain Python."""

    def __init__(self, env: Any) -> None:
        self.env = env
        self.controller = env.controller
        self.full = self.controller.full
        self.store = self.controller.store
        self.backend = env.backend

    # -- ids ---------------------------------------------------------------------------------
    def real(self, kind: str, key: Any) -> Any:
        """The backend id for a prototype id (an id that is already real passes through)."""
        return self.env.aliases.real(kind, key)

    def alias(self, kind: str, real_id: str) -> int | str:
        """The prototype id for a backend id (the id itself when it was not seeded)."""
        table = getattr(self.env.aliases, kind)
        return next((number for number, value in table.items() if value == real_id), real_id)

    # -- events ------------------------------------------------------------------------------
    def at(self, x: float, y: float) -> QPoint:
        return QPoint(round(x), round(y))

    def probe(self, name: str, index: Any = 0) -> QPoint | None:
        if name in _TASK_PROBES:
            index = self.real("task", index)
        elif name in _REMINDER_PROBES:
            index = self.real("reminder", index)
        elif name in _TIMER_PROBES:
            index = self.real("timer", index)
        value = js(self.full.probe(name, index))
        return None if value is None else self.at(value["x"], value["y"])

    def click(self, name: str, index: Any = 0, dx: float = 0, dy: float = 0) -> None:
        p = self.probe(name, index)
        assert p is not None, f"no probe point for {name} {index}"
        QTest.mouseClick(self.full, LEFT, NO_MOD, QPoint(p.x() + round(dx), p.y() + round(dy)))

    def click_at(self, p: QPoint) -> None:
        QTest.mouseClick(self.full, LEFT, NO_MOD, p)

    def press(self, p: QPoint) -> None:
        QTest.mousePress(self.full, LEFT, NO_MOD, p)

    def move(self, p: QPoint) -> None:
        QTest.mouseMove(self.full, p)

    def release(self, p: QPoint) -> None:
        QTest.mouseRelease(self.full, LEFT, NO_MOD, p)

    def type_text(self, text: str) -> None:
        for char in text:
            cast(Any, QTest).keyClick(self.full, char)

    def key(self, key: Any, modifier: Any = NO_MOD) -> None:
        QTest.keyClick(self.full, key, modifier)

    # -- the store's data ----------------------------------------------------------------------
    def tasks(self) -> list[dict[str, Any]]:
        return js(self.store.property("tasks"))  # type: ignore[no-any-return]

    def task(self, key: Any) -> dict[str, Any]:
        wanted = self.real("task", key)
        return next(t for t in self.tasks() if t["id"] == wanted)

    def reminders(self) -> list[dict[str, Any]]:
        return js(self.store.property("reminders"))  # type: ignore[no-any-return]

    def reminder(self, key: Any) -> dict[str, Any]:
        wanted = self.real("reminder", key)
        return next(r for r in self.reminders() if r["id"] == wanted)

    def notes(self) -> list[dict[str, Any]]:
        return js(self.store.property("notes"))  # type: ignore[no-any-return]

    def note(self, key: Any) -> dict[str, Any]:
        wanted = self.real("note", key)
        return next(n for n in self.notes() if n["id"] == wanted)

    def timers(self) -> list[dict[str, Any]]:
        return js(self.store.property("timers"))  # type: ignore[no-any-return]

    def timer(self, key: Any) -> dict[str, Any]:
        wanted = self.real("timer", key)
        return next(t for t in self.timers() if t["id"] == wanted)

    def trash(self) -> list[dict[str, Any]]:
        return js(self.store.property("trashItems"))  # type: ignore[no-any-return]

    def group(self, name: str) -> list[dict[str, Any]]:
        return js(self.store.property("taskGroups"))[name]  # type: ignore[no-any-return]

    def rgroup(self, name: str) -> list[dict[str, Any]]:
        return js(self.store.property("reminderGroups"))[name]  # type: ignore[no-any-return]

    def prop(self, name: str) -> Any:
        return self.full.property(name)

    def newest(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """The most recently created of a list of tasks, reminders or timers."""
        return max(items, key=lambda item: item.get("createdAt") or 0)

    # -- settings, as they are saved ---------------------------------------------------------
    def saved(self, key: str) -> Any:
        """A setting as the database holds it (what a restarted app would read), in the page's
        own terms.  A boolean comes back as "true" / "false" (the prototype's INI text)."""
        self.backend.flush_settings()
        value = convert.settings_dict(self.env.application.settings.get_settings())[key]
        return str(value).lower() if isinstance(value, bool) else value
