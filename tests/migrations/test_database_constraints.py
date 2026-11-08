"""Direct-SQL constraint tests against a database created only by Alembic."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from panelo.bootstrap import _alembic_config
from panelo.db.engine import create_sqlite_engine, sqlite_url


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Iterator[Engine]:
    path = tmp_path / "constraints.db"
    command.upgrade(_alembic_config(sqlite_url(path), 5_000), "head")
    engine = create_sqlite_engine(path)
    try:
        yield engine
    finally:
        engine.dispose()


_AUDIT = {"created_at_utc": 1, "updated_at_utc": 1, "version": 1}
_BASE: dict[str, dict[str, object]] = {
    "notes": {
        "id": "n",
        "title": "N",
        "content": "",
        "content_format": "PLAIN_TEXT",
        "is_pinned": 0,
        "is_archived": 0,
        **_AUDIT,
    },
    "tasks": {
        "id": "t",
        "title": "T",
        "description": "",
        "status": "TODO",
        "priority": "NORMAL",
        **_AUDIT,
    },
    "reminders": {
        "id": "r",
        "title": "R",
        "message": "",
        "due_at_utc": 1,
        "scheduled_timezone": "UTC",
        "status": "PENDING",
        "snooze_count": 0,
        **_AUDIT,
    },
    "reminder_events": {"id": "e", "reminder_id": "reminder", "event_type": "x", "occurred_at_utc": 1},
    "timer_presets": {
        "id": "p2",
        "name": "P2",
        "normalized_name": "p2",
        "duration_ms": 2000,
        "sort_order": 1,
        "is_builtin": 0,
        **_AUDIT,
    },
    "timers": {
        "id": "x",
        "name": "X",
        "state": "READY",
        "planned_duration_ms": 1000,
        "elapsed_before_run_ms": 0,
        **_AUDIT,
    },
    "timer_runs": {
        "id": "run",
        "timer_id": "timer",
        "planned_duration_ms": 1000,
        "actual_duration_ms": 0,
        "started_at_utc": 1,
    },
    "app_settings": {
        "id": 1,
        "theme": "SYSTEM",
        "global_hotkey": "Ctrl+Alt+Space",
        "start_with_windows": 0,
        "minimize_to_tray": 1,
        "notifications_enabled": 1,
        "default_timer_duration_ms": 1000,
        "pomodoro_work_duration_ms": 1000,
        "short_break_duration_ms": 1000,
        "long_break_duration_ms": 1000,
        "updated_at_utc": 1,
        "version": 1,
    },
    "notification_deliveries": {
        "id": "nd",
        "kind": "k",
        "aggregate_type": "a",
        "aggregate_id": "a",
        "dedupe_key": "d",
        "title": "t",
        "body": "b",
        "status": "PENDING",
        "attempt_count": 0,
        "available_at_utc": 1,
        "created_at_utc": 1,
    },
}


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _insert(table: str, **overrides: object) -> str:
    values = {**_BASE[table], **overrides}
    columns = ", ".join(values)
    return f"INSERT INTO {table} ({columns}) VALUES ({', '.join(_literal(v) for v in values.values())})"


def _seed(connection: object) -> None:
    execute = connection.execute  # type: ignore[attr-defined]
    execute(text(_insert("notes", id="note", title="Note")))
    execute(text(_insert("tasks", id="task", title="Task")))
    execute(text(_insert("timer_presets", id="preset", name="Preset", normalized_name="preset", sort_order=0)))
    execute(text(_insert("timers", id="timer", name="Timer", preset_id="preset", task_id="task")))
    execute(text(_insert("reminders", id="reminder", title="Reminder", task_id="task")))


def _reminder(**overrides: object) -> str:
    return _insert("reminders", id="r2", **overrides)


_ANCHOR = {"anchor_at_utc": 5}
_INVALID_WRITES: list[tuple[str, list[str]]] = [
    ("preset duplicate name", [_insert("timer_presets", normalized_name="preset")]),
    ("preset duplicate order", [_insert("timer_presets", sort_order=0)]),
    ("preset non-positive duration", [_insert("timer_presets", duration_ms=0)]),
    ("note invalid color", [_insert("notes", id="n2", color="chartreuse")]),
    ("task invalid status", [_insert("tasks", id="t2", status="BAD")]),
    ("task invalid priority", [_insert("tasks", id="t2", priority="BAD")]),
    ("task start after due", [_insert("tasks", id="t2", start_at_utc=2, due_at_utc=1)]),
    ("task completed missing timestamp", [_insert("tasks", id="t2", status="COMPLETED")]),
    ("task cancelled has completed timestamp", [_insert("tasks", id="t2", status="CANCELLED", completed_at_utc=1, cancelled_at_utc=2)]),
    ("task date-only flag not boolean", [_insert("tasks", id="t2", due_has_time=2)]),
    ("reminder invalid status", [_reminder(status="BAD")]),
    ("reminder dual links", [_reminder(task_id="task", note_id="note")]),
    ("reminder terminal mismatch", [_reminder(status="DISMISSED")]),
    ("reminder negative snooze", [_reminder(snooze_count=-1)]),
    ("repeat kind invalid", [_reminder(repeat_kind="HOURLY", **_ANCHOR)]),
    ("repeat unit invalid", [_reminder(repeat_kind="EVERY", repeat_every_n=1, repeat_every_unit="YEARS", **_ANCHOR)]),
    ("repeat fields without a kind", [_reminder(repeat_days=3)]),
    ("repeat anchor without a kind", [_reminder(**_ANCHOR)]),
    ("repeat kind without an anchor", [_reminder(repeat_kind="DAILY")]),
    ("daily with weekdays", [_reminder(repeat_kind="DAILY", repeat_days=3, **_ANCHOR)]),
    ("weekdays with interval", [_reminder(repeat_kind="WEEKDAYS", repeat_every_n=2, **_ANCHOR)]),
    ("yearly with a day of month", [_reminder(repeat_kind="YEARLY", repeat_day_of_month=3, **_ANCHOR)]),
    ("weekly without days", [_reminder(repeat_kind="WEEKLY", **_ANCHOR)]),
    ("weekly with no day set", [_reminder(repeat_kind="WEEKLY", repeat_days=0, **_ANCHOR)]),
    ("weekly day mask out of range", [_reminder(repeat_kind="WEEKLY", repeat_days=128, **_ANCHOR)]),
    ("weekly with a day of month", [_reminder(repeat_kind="WEEKLY", repeat_days=1, repeat_day_of_month=1, **_ANCHOR)]),
    ("monthly without a day", [_reminder(repeat_kind="MONTHLY", **_ANCHOR)]),
    ("monthly day zero", [_reminder(repeat_kind="MONTHLY", repeat_day_of_month=0, **_ANCHOR)]),
    ("monthly day 32", [_reminder(repeat_kind="MONTHLY", repeat_day_of_month=32, **_ANCHOR)]),
    ("monthly with an interval", [_reminder(repeat_kind="MONTHLY", repeat_day_of_month=1, repeat_every_n=1, **_ANCHOR)]),
    ("every without interval or unit", [_reminder(repeat_kind="EVERY", **_ANCHOR)]),
    ("every without an interval", [_reminder(repeat_kind="EVERY", repeat_every_unit="DAYS", **_ANCHOR)]),
    ("every without a unit", [_reminder(repeat_kind="EVERY", repeat_every_n=2, **_ANCHOR)]),
    ("every interval zero", [_reminder(repeat_kind="EVERY", repeat_every_n=0, repeat_every_unit="DAYS", **_ANCHOR)]),
    ("every with weekdays", [_reminder(repeat_kind="EVERY", repeat_every_n=1, repeat_every_unit="DAYS", repeat_days=1, **_ANCHOR)]),
    ("timer invalid state", [_insert("timers", state="BAD")]),
    ("timer running fields", [_insert("timers", state="RUNNING", deadline_at_utc=1)]),
    ("timer paused fields", [_insert("timers", state="PAUSED", run_started_at_utc=1, first_started_at_utc=1)]),
    ("timer ready fields", [_insert("timers", elapsed_before_run_ms=1)]),
    ("timer terminal fields", [_insert("timers", state="FINISHED")]),
    ("timer run invalid outcome", [_insert("timer_runs", ended_at_utc=2, outcome="BAD")]),
    ("duplicate open timer run", [_insert("timer_runs", id="run1"), _insert("timer_runs", id="run2", started_at_utc=2)]),
    ("settings theme", [_insert("app_settings", id=1, theme="BAD")]),
    ("settings version", [_insert("app_settings", id=1, version=0)]),
    ("settings boolean", [_insert("app_settings", id=1, start_with_windows=2)]),
    ("settings duration", [_insert("app_settings", id=1, default_timer_duration_ms=999)]),
    ("settings user name too long", [_insert("app_settings", id=1, user_name="x" * 61)]),
    ("settings tour flag", [_insert("app_settings", id=1, tour_done=2)]),
    ("settings notification style", [_insert("app_settings", id=1, notification_style="LOUD")]),
    ("settings sound path too long", [_insert("app_settings", id=1, notification_sound="x" * 261)]),
    ("settings blur too high", [_insert("app_settings", id=1, backdrop_blur=101)]),
    ("settings blur negative", [_insert("app_settings", id=1, backdrop_blur=-1)]),
    ("settings dim too high", [_insert("app_settings", id=1, backdrop_dim=101)]),
    ("settings overlay too small", [_insert("app_settings", id=1, overlay_size_pct=59)]),
    ("settings overlay too large", [_insert("app_settings", id=1, overlay_size_pct=91)]),
    ("settings files view", [_insert("app_settings", id=1, files_view_mode="TREE")]),
    ("settings library path too long", [_insert("app_settings", id=1, library_path="x" * 1025)]),
    ("settings auto backup flag", [_insert("app_settings", id=1, auto_backup=2)]),
    ("settings keep zero backups", [_insert("app_settings", id=1, keep_backups=0)]),
    ("settings keep too many backups", [_insert("app_settings", id=1, keep_backups=61)]),
    ("notification status", [_insert("notification_deliveries", id="n", status="BAD")]),
    ("notification attempts", [_insert("notification_deliveries", id="n", attempt_count=-1)]),
    ("notification dedupe", [_insert("notification_deliveries", id="n1"), _insert("notification_deliveries", id="n2")]),
    ("reminder task foreign key", [_reminder(task_id="missing")]),
    ("reminder note foreign key", [_reminder(note_id="missing")]),
    ("reminder event foreign key", [_insert("reminder_events", reminder_id="missing")]),
    ("timer preset foreign key", [_insert("timers", preset_id="missing")]),
    ("timer task foreign key", [_insert("timers", task_id="missing")]),
    ("timer run timer foreign key", [_insert("timer_runs", timer_id="missing")]),
    ("timer run task foreign key", [_insert("timer_runs", task_id="missing")]),
]


@pytest.mark.parametrize(("_name", "statements"), _INVALID_WRITES, ids=[name for name, _ in _INVALID_WRITES])
def test_migration_schema_rejects_invalid_direct_writes(
    migrated_engine: Engine, _name: str, statements: list[str]
) -> None:
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _seed(connection)
            with pytest.raises(IntegrityError):
                for statement in statements:
                    connection.execute(text(statement))
        finally:
            transaction.rollback()


_VALID_REPEATS: list[tuple[str, dict[str, object]]] = [
    ("one-time", {}),
    ("daily", {"repeat_kind": "DAILY", **_ANCHOR}),
    ("weekdays", {"repeat_kind": "WEEKDAYS", **_ANCHOR}),
    ("yearly", {"repeat_kind": "YEARLY", **_ANCHOR}),
    ("weekly", {"repeat_kind": "WEEKLY", "repeat_days": 0b0101010, **_ANCHOR}),
    ("weekly every day", {"repeat_kind": "WEEKLY", "repeat_days": 127, **_ANCHOR}),
    ("monthly", {"repeat_kind": "MONTHLY", "repeat_day_of_month": 31, **_ANCHOR}),
    ("every hours", {"repeat_kind": "EVERY", "repeat_every_n": 2, "repeat_every_unit": "HOURS", **_ANCHOR}),
    ("every days", {"repeat_kind": "EVERY", "repeat_every_n": 1, "repeat_every_unit": "DAYS", **_ANCHOR}),
    ("every weeks", {"repeat_kind": "EVERY", "repeat_every_n": 3, "repeat_every_unit": "WEEKS", **_ANCHOR}),
]


@pytest.mark.parametrize(("_name", "fields"), _VALID_REPEATS, ids=[name for name, _ in _VALID_REPEATS])
def test_every_valid_repeat_rule_shape_is_accepted(
    migrated_engine: Engine, _name: str, fields: dict[str, object]
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text(_reminder(**fields)))
        stored = connection.execute(
            text("SELECT repeat_kind, anchor_at_utc FROM reminders WHERE id = 'r2'")
        ).one()
        assert stored == (fields.get("repeat_kind"), fields.get("anchor_at_utc"))


def test_several_timers_may_be_active_at_once(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text(_insert("timers", id="running", state="RUNNING", run_started_at_utc=1, deadline_at_utc=9, first_started_at_utc=1)))
        connection.execute(text(_insert("timers", id="paused", state="PAUSED", first_started_at_utc=1)))
        connection.execute(text(_insert("timers", id="running2", state="RUNNING", run_started_at_utc=2, deadline_at_utc=9, first_started_at_utc=2)))
        assert connection.execute(
            text("SELECT count(*) FROM timers WHERE state IN ('RUNNING', 'PAUSED')")
        ).scalar_one() == 3


def test_new_columns_take_their_documented_defaults(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text(_insert("notes")))
        connection.execute(text(_insert("tasks")))
        connection.execute(text(_insert("app_settings")))
        assert connection.execute(text("SELECT color FROM notes")).scalar_one() == "none"
        assert connection.execute(text("SELECT due_has_time FROM tasks")).scalar_one() == 1
        assert tuple(
            connection.execute(
                text(
                    "SELECT user_name, tour_done, notification_style, notification_sound, "
                    "backdrop_blur, backdrop_dim, overlay_size_pct, overlay_layout_json, "
                    "files_view_mode, library_path, auto_backup, keep_backups, "
                    "last_auto_backup_at_utc FROM app_settings"
                )
            ).one()
        ) == ("", 0, "TOAST", "", 100, 45, 85, "", "LIST", "", 1, 7, None)
