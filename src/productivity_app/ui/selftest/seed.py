"""Demo data for the self-test and the screenshots: the prototype's fake store, made real.

Everything goes through the real services (with the clock moved back and forth, so notes were
"edited yesterday" and tasks "done 3 hours ago"), except the completion history of the repeating
reminders, which is written as the audit events ``Done`` leaves behind.  The prototype's small
integer ids become the backend's ids; ``Aliases`` keeps the mapping, so a check can still say
"task 5".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPageSize,
    QPainter,
    QPainterPath,
    QPdfWriter,
    QPen,
)
from sqlalchemy import update

from productivity_app.bootstrap import Application
from productivity_app.core.clock import SystemClock
from productivity_app.core.ids import Uuid4Generator
from productivity_app.core.recurrence import RepeatRule, next_occurrence
from productivity_app.db.models.initial import Reminder, ReminderEvent
from productivity_app.schemas.focus import CreateFocusSession
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.reminders import CreateReminder, ReminderRepeat
from productivity_app.schemas.settings import SettingsPatch
from productivity_app.schemas.tasks import CreateTask
from productivity_app.schemas.timers import CreateTimer
from productivity_app.ui.bridge import convert


class OffsetClock:
    """The real time plus an offset the seed (and a test) can move: ``at(moment)`` is "now"
    inside the ``with`` block."""

    def __init__(self) -> None:
        self.offset = timedelta(0)
        self._real = SystemClock()

    def now_utc(self) -> datetime:
        return self._real.now_utc() + self.offset

    def monotonic_ns(self) -> int:
        return self._real.monotonic_ns()

    def set_to(self, moment: datetime) -> None:
        self.offset = moment - self._real.now_utc()

    def reset(self) -> None:
        self.offset = timedelta(0)

    def at(self, moment: datetime) -> _At:
        return _At(self, moment)


class _At:
    def __init__(self, clock: OffsetClock, moment: datetime) -> None:
        self._clock, self._moment, self._saved = clock, moment, clock.offset

    def __enter__(self) -> None:
        self._clock.set_to(self._moment)

    def __exit__(self, *_: object) -> None:
        self._clock.offset = self._saved


@dataclass
class Aliases:
    """The prototype's ids (1, 2, 3 ...) for the seeded things, by kind."""

    note: dict[int, str] = field(default_factory=dict)
    task: dict[int, str] = field(default_factory=dict)
    reminder: dict[int, str] = field(default_factory=dict)
    timer: dict[int, str] = field(default_factory=dict)

    def real(self, kind: str, key: Any) -> Any:
        """The backend id for a prototype id (anything else passes through)."""
        return getattr(self, kind).get(key, key)


def local_now() -> datetime:
    return datetime.now(UTC)


def day_at(offset: int, hour: int = 0, minute: int = 0) -> datetime:
    """The local day ``offset`` days from today at hour:minute, as an aware UTC datetime."""
    base = datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (base + timedelta(days=offset)).astimezone(UTC)


NOTES: list[tuple[int, str, str, str, bool, bool, timedelta]] = [
    # id, title, body, tint, pinned, archived, how long ago it was edited
    (1, "Meeting notes", "- Budget review for Q3\n- Hiring: two designers\n- Ship the new glass UI before December\n- Follow up with Priya on the API", "slate", True, False, timedelta(hours=2)),
    (2, "Ideas", "A dashboard that feels like Keep but darker, with acrylic glass and a quiet right rail.", "plum", True, False, timedelta(days=1)),
    (3, "Recipes to try", "Pasta alla vodka\nThai green curry\nShakshuka", "amber", False, False, timedelta(days=2)),
    (4, "Books to read", "- Dune Messiah\n- The Pragmatic Programmer\n- Deep Work\n- Piranesi", "none", False, False, timedelta(days=4)),
    (5, "", "Call the landlord about the heating before Friday.", "rose", False, False, timedelta(days=5)),
    (6, "Journal", "Today I finally cleaned up the project folder, wrote a README and got every test passing. Next up: a UI I actually like looking at.", "none", False, False, timedelta(days=6)),
    (7, "Gift ideas", "Mom: ceramic planter\nArjun: mechanical keyboard", "teal", False, False, timedelta(days=10)),
    (8, "Workout", "Mon / Wed / Fri\nSquat 5x5, bench 5x5, row 5x5\nTue / Thu: 30 min run", "moss", False, False, timedelta(days=12)),
    (9, "Wi-Fi", "Network: home-5G\nPassword on the fridge", "sand", False, False, timedelta(days=14)),
    (10, "Quotes", "\"Simplicity is the ultimate sophistication.\"", "none", False, False, timedelta(days=17)),
    (11, "Old trip plan", "Goa, December. Flights booked.", "teal", False, True, timedelta(days=33)),
    (12, "Kinematics formulas", "v = u + at\ns = ut + 1/2 at^2\nv^2 = u^2 + 2as", "slate", False, False, timedelta(days=18)),
    (13, "Integration cheatsheet", "By parts, substitution, partial fractions. Revise trig identities first.", "none", False, False, timedelta(days=20)),
    (14, "Periodic trends", "Electronegativity rises across a period and falls down a group.", "moss", False, False, timedelta(days=23)),
]
TRASHED_NOTES: list[tuple[int, str, str, str, timedelta]] = [
    (15, "Old shopping list", "Milk, eggs, coffee, batteries", "sand", timedelta(days=2, hours=2)),
    (16, "Draft email to Priya", "Hi Priya, following up on the API review...", "none", timedelta(days=20, hours=2)),
]


def seed_demo(application: Application, clock: OffsetClock, now: datetime | None = None) -> Aliases:
    """Fill a fresh database with the prototype's demo data."""
    now = now or datetime.now(UTC)
    aliases = Aliases()
    ids = Uuid4Generator()
    tick = timedelta(seconds=1)

    # Notes: created oldest first so the newest edit is first in the list.
    for number, title, body, tint, pinned, archived, ago in reversed(NOTES):
        with clock.at(now - ago):
            note = application.notes.create_note(
                CreateNote(title=title, content=body, color=tint, is_pinned=pinned)  # type: ignore[arg-type]
            )
            if archived:
                application.notes.archive_note(note.id, note.version)
        aliases.note[number] = note.id
    for number, title, body, tint, ago in TRASHED_NOTES:
        with clock.at(now - ago - timedelta(hours=1)):
            note = application.notes.create_note(CreateNote(title=title, content=body, color=tint))  # type: ignore[arg-type]
        with clock.at(now - ago):
            application.notes.delete_note(note.id, note.version)
        aliases.note[number] = note.id

    # Tasks
    def midnight(offset: int) -> tuple[datetime, bool]:
        return day_at(offset), False

    def at(offset: int, hour: int, minute: int) -> tuple[datetime, bool]:
        return day_at(offset, hour, minute), True

    tasks: list[tuple[int, str, tuple[datetime, bool] | None, str, dict[str, Any]]] = [
        (1, "Submit expense report", midnight(-3), "HIGH", {}),
        (2, "Renew library card", at(-1, 17, 0), "NORMAL", {}),
        (3, "Finish the quarterly report", at(0, 17, 0), "URGENT", {"description": "Numbers from finance are in the shared sheet. Add the Q3 summary slide."}),
        (4, "Call the dentist", at(0, 11, 30), "NORMAL", {}),
        (5, "Review pull request", midnight(0), "HIGH", {}),
        (6, "Buy groceries", midnight(0), "NORMAL", {"done": timedelta(hours=3)}),
        (7, "Water the plants", midnight(0), "LOW", {"done": timedelta(hours=2)}),
        (8, "Prepare slides for standup", at(1, 9, 0), "NORMAL", {}),
        (9, "Gym session", at(3, 18, 0), "LOW", {}),
        (10, "Read chapter 4", midnight(5), "NORMAL", {}),
        (11, "Book flights for Goa", midnight(14), "HIGH", {}),
        (12, "Renew passport", midnight(30), "LOW", {}),
        (13, "Learn Rust basics", None, "NORMAL", {}),
        (14, "Sort old photos", None, "LOW", {}),
        (15, "Reply to Sam", midnight(-2), "NORMAL", {"done": timedelta(hours=30)}),
        (16, "Pay electricity bill", midnight(-4), "URGENT", {"done": timedelta(hours=52)}),
        (20, "Cancel gym trial", midnight(-6), "NORMAL", {"deleted": timedelta(days=5, hours=2)}),
        (21, "Return headphones", midnight(-9), "LOW", {"deleted": timedelta(days=27, hours=2)}),
        (22, "Ancient chore (purged on load)", midnight(-60), "LOW", {"deleted": timedelta(days=45)}),
    ]
    for index, (number, title, due, priority, extra) in enumerate(tasks):
        with clock.at(now - timedelta(days=4) + index * tick):
            task = application.tasks.create_task(
                CreateTask(
                    title=title,
                    description=str(extra.get("description", "")),
                    priority=priority,  # type: ignore[arg-type]
                    due_at_utc=due[0] if due else None,
                    due_has_time=due[1] if due else True,
                )
            )
        aliases.task[number] = task.id
        if "done" in extra:
            with clock.at(now - extra["done"]):
                application.tasks.complete_task(task.id, task.version)
        if "deleted" in extra:
            with clock.at(now - extra["deleted"]):
                application.tasks.delete_task(task.id, task.version)

    # Reminders
    zone = convert.local_zone()
    tz = convert.local_timezone_name()
    water_due = (now + timedelta(minutes=95)).replace(second=0, microsecond=0)
    gym_rule = RepeatRule("WEEKLY", days=(1, 3, 5))
    rent_rule = RepeatRule("MONTHLY", day_of_month=1)
    gym_due = next_occurrence(gym_rule, now, day_at(0, 18, 0), zone)
    rent_due = next_occurrence(rent_rule, now, day_at(0, 9, 0), zone)
    assert gym_due is not None and rent_due is not None

    reminders: list[dict[str, Any]] = [
        {"n": 1, "title": "Drink water", "due": now - timedelta(minutes=1), "message": "Keep a glass on the desk."},
        {"n": 2, "title": "Gym", "due": now + timedelta(minutes=52), "message": "Leg day. Bring the blue towel."},
        {"n": 3, "title": "Prepare for standup", "due": day_at(1, 9, 30), "task": 8, "snoozes": 1},
        {"n": 4, "title": "Review the meeting notes", "due": day_at(3, 10, 0), "note": 1},
        {"n": 5, "title": "Renew the domain", "due": day_at(12, 9, 0), "message": "Auto-renew is off."},
        {"n": 6, "title": "Take vitamins", "due": day_at(-1, 8, 0), "done": day_at(-1, 8, 2)},
        {"n": 7, "title": "Call Priya", "due": day_at(-2, 15, 0), "done": day_at(-2, 15, 40), "snoozes": 2},
        {"n": 8, "title": "Pay rent", "due": day_at(-5, 9, 0), "done": day_at(-5, 9, 1)},
        {"n": 9, "title": "Dentist check-in", "due": day_at(-8, 13, 0), "done": day_at(-8, 13, 5)},
        {"n": 10, "title": "Drink water", "due": water_due, "repeat": ReminderRepeat(kind="EVERY", every_n=2, every_unit="HOURS"), "history": [now - timedelta(hours=h) for h in range(1, 6)]},
        {"n": 11, "title": "Gym", "due": gym_due, "repeat": ReminderRepeat(kind="WEEKLY", days=(1, 3, 5)), "history": [day_at(-2, 18, 1), day_at(-4, 18, 3), day_at(-7, 18, 0)]},
        {"n": 12, "title": "Pay rent", "due": rent_due, "repeat": ReminderRepeat(kind="MONTHLY", day_of_month=1), "history": [day_at(-30 * m, 9, 2) for m in range(1, 6)]},
    ]
    for index, spec in enumerate(reminders):
        with clock.at(now - timedelta(days=3) + index * tick):
            view = application.reminders.create_reminder(
                CreateReminder(
                    title=spec["title"],
                    message=spec.get("message", ""),
                    due_at_utc=spec["due"],
                    scheduled_timezone=tz,
                    task_id=aliases.task[spec["task"]] if "task" in spec else None,
                    note_id=aliases.note[spec["note"]] if "note" in spec else None,
                    repeat=spec.get("repeat"),
                )
            )
        aliases.reminder[spec["n"]] = view.id
        if "done" in spec:
            with clock.at(spec["done"]):
                application.reminders.complete_reminder(view.id, view.version)
        with application.unit_of_work_factory() as uow:
            assert uow.session is not None
            if spec.get("snoozes"):
                uow.session.execute(
                    update(Reminder).where(Reminder.id == view.id).values(snooze_count=spec["snoozes"])
                )
            for done_at in spec.get("history", []):
                uow.session.add(
                    ReminderEvent(
                        id=ids.new(), reminder_id=view.id, event_type="COMPLETED_OCCURRENCE",
                        occurred_at_utc=done_at, previous_status="PENDING", new_status="PENDING",
                        old_due_at_utc=None, new_due_at_utc=None, metadata_json=None,
                    )
                )
            uow.commit()

    # Timers: Tea and Laundry running, a stretch break ready
    created = now - timedelta(hours=3)
    for index, (number, name, total, left, is_running) in enumerate(
        ((1, "Tea", 300, 180, True), (2, "Laundry", 2700, 1520, True), (3, "Stretch break", 600, 600, False))
    ):
        with clock.at(created + index * tick):
            timer = application.timers.create_timer(
                CreateTimer(name=name, planned_duration_ms=total * 1000)
            )
        aliases.timer[number] = timer.id
        if is_running:
            with clock.at(now - timedelta(seconds=total - left)):
                application.timers.start_timer(timer.id, timer.version)

    # A week of focus sessions (standalone, 25 minutes each)
    per_day = {0: 3, 1: 4, 2: 2, 3: 3, 5: 4, 6: 2, 8: 3, 9: 1}
    for days_back, count in per_day.items():
        for i in range(count):
            started = day_at(-days_back, 9 + i, 0)
            with clock.at(started):
                session = application.focus.create_session(CreateFocusSession(phase_kind="FOCUS"))
                running = application.timers.start_timer(session.timer.id, session.timer.version)
            with clock.at(started + timedelta(minutes=25, seconds=5)):
                application.timers.complete_timer(running.id, running.version)

    # Backups, and "the daily backup was just made"
    for days_ago in range(5):
        with clock.at(day_at(-days_ago, 3, 0)):
            application.backups.create_backup()
    settings = application.settings.get_settings()
    application.settings.update_settings(SettingsPatch(expected_version=settings.version, tour_done=True))
    application.settings.record_auto_backup(now)
    return aliases


# -- The Files library's demo content -------------------------------------------------------


def seed_library(root: Path) -> None:
    """The prototype's folders and files (a PDF, a picture, a text file ...)."""
    for folder in ("Study/Physics", "Study/Maths", "Study/Chemistry", "Coding", "Personal"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    write_seed_pdf(root / "Study/Physics/Chapter 3 - Kinematics.pdf")
    write_seed_png(root / "Study/Physics/Lab graph.png")
    (root / "Coding/ideas.txt").write_text(
        "Ideas\n- A glass UI that feels calm\n- Quick overlay on a hotkey\n- Keep files next to notes\n",
        encoding="utf-8",
    )
    (root / "Personal/Trip budget.csv").write_text(
        "Item,Cost\nFlights,420\nHotel,610\nFood,240\nLocal transport,90\n", encoding="utf-8"
    )
    (root / "Study/Maths/Formulas.md").write_text(
        "# Formulas\n\n- Quadratic: x = (-b ± sqrt(b^2 - 4ac)) / 2a\n- Area of a circle: pi r^2\n",
        encoding="utf-8",
    )


def write_seed_pdf(path: Path) -> None:
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(72)
    writer.setTitle("Chapter 3 - Kinematics")
    painter = QPainter(writer)
    title = QFont("Segoe UI")
    title.setPixelSize(26)
    painter.setFont(title)
    painter.drawText(60, 100, "Chapter 3 - Kinematics")
    body = QFont("Segoe UI")
    body.setPixelSize(13)
    painter.setFont(body)
    lines = (
        "Motion in a straight line, with constant acceleration.",
        "v = u + at",
        "s = ut + 1/2 at^2",
        "v^2 = u^2 + 2as",
        "Sketch the velocity-time graph before solving a problem.",
    )
    for index, line in enumerate(lines):
        painter.drawText(60, 150 + index * 24, line)
    painter.end()


def write_seed_png(path: Path) -> None:
    image = QImage(480, 320, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 480, 320)
    gradient.setColorAt(0.0, QColor("#1d2b4a"))
    gradient.setColorAt(1.0, QColor("#2a1d3d"))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
    for x in range(40, 460, 40):
        painter.drawLine(x, 30, x, 290)
    for y in range(30, 300, 40):
        painter.drawLine(40, y, 440, y)
    painter.setPen(QPen(QColor("#ffffff"), 2))
    painter.drawLine(40, 290, 440, 290)
    painter.drawLine(40, 30, 40, 290)
    curve = QPainterPath(QPointF(40, 270))
    curve.cubicTo(QPointF(140, 260), QPointF(220, 120), QPointF(440, 50))
    painter.setPen(QPen(QColor("#ffb454"), 3))
    painter.drawPath(curve)
    painter.setBrush(QColor("#ffb454"))
    for x, y in ((40, 270), (150, 235), (260, 150), (350, 85), (440, 50)):
        painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
    painter.end()
    image.save(str(path))
