from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from panelo.core.events import DomainEvent
from panelo.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from panelo.db.engine import create_sqlite_engine
from panelo.db.types import datetime_to_epoch_microseconds
from panelo.repositories.reminders import ReminderRepository
from panelo.schemas.notes import CreateNote
from panelo.schemas.reminders import (
    CreateReminder,
    ReminderPageQuery,
    ReminderQuery,
    SnoozeReminder,
    UpdateReminder,
)
from panelo.schemas.tasks import CreateTask
from panelo.services.reminders import ReminderService, _translate_reminder_integrity_error

_EVENT_TYPES = (
    "REMINDER_CREATED",
    "REMINDER_UPDATED",
    "REMINDER_SNOOZED",
    "REMINDER_COMPLETED",
    "REMINDER_DISMISSED",
    "REMINDER_CANCELLED",
    "REMINDER_REOPENED",
    "REMINDER_DELETED",
)


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new(self) -> str:
        self._next += 1
        return f"00000000-0000-0000-0000-{self._next:012d}"


def _bus(application):
    return application.unit_of_work_factory()._event_bus


def _watch(application) -> list[DomainEvent]:
    observed: list[DomainEvent] = []
    for event_type in _EVENT_TYPES:
        _bus(application).subscribe(event_type, observed.append)
    return observed


def _detail(app_config, reminder_id: str):
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT title, status, due_at_utc, scheduled_timezone, snooze_count, version, "
                    "updated_at_utc, dismissed_at_utc, completed_at_utc, cancelled_at_utc "
                    "FROM reminders WHERE id=:id"
                ),
                {"id": reminder_id},
            ).one()
    finally:
        engine.dispose()


def _count(app_config, table: str, reminder_id: str) -> int:
    column = "id" if table == "reminders" else "reminder_id"
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {column}=:id"), {"id": reminder_id}
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _history(application, reminder_id: str):
    return application.reminders.list_reminder_events(reminder_id, ReminderPageQuery(limit=200))


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ("complete", "dismiss"),
        ("complete", "update"),
        ("dismiss", "update"),
        ("cancel", "update"),
        ("delete", "complete"),
        ("delete", "dismiss"),
        ("delete", "cancel"),
        ("delete", "update"),
    ),
)
def test_lifecycle_race_history_and_domain_event_identify_the_actual_winner(
    application, app_config, clock, left: str, right: str
) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Race", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed = _watch(application)
    barrier = Barrier(2)

    def run(kind: str) -> tuple[str, object]:
        barrier.wait()
        try:
            if kind == "update":
                return kind, application.reminders.update_reminder(
                    reminder.id, UpdateReminder(expected_version=reminder.version, title="Changed")
                )
            return kind, getattr(application.reminders, f"{kind}_reminder")(reminder.id, reminder.version)
        except (InvalidStateTransitionError, NotFoundError, StaleVersionError) as exc:
            return kind, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [executor.submit(run, kind) for kind in (left, right)]
        outcomes = [future.result() for future in outcomes]

    winners = [(kind, result) for kind, result in outcomes if not isinstance(result, Exception)]
    assert len(winners) == 1
    winner, result = winners[0]
    event_type = {
        "complete": "REMINDER_COMPLETED",
        "dismiss": "REMINDER_DISMISSED",
        "cancel": "REMINDER_CANCELLED",
        "delete": "REMINDER_DELETED",
        "update": "REMINDER_UPDATED",
    }[winner]
    assert [event.event_type for event in observed] == [event_type]
    if winner == "delete":
        # Deleted for good: the row and its audit history are gone, nothing is left to read.
        with pytest.raises(NotFoundError):
            application.reminders.get_reminder(reminder.id)
        assert _count(app_config, "reminders", reminder.id) == 0
        assert _count(app_config, "reminder_events", reminder.id) == 0
        return
    view = application.reminders.get_reminder(reminder.id)
    row = _detail(app_config, reminder.id)
    assert view.version == reminder.version + 1
    assert row[1] == view.status
    assert row[5] == view.version
    history_types = [event.event_type for event in _history(application, reminder.id)]
    assert sorted(history_types) == sorted([event_type, "REMINDER_CREATED"])
    if winner == "complete":
        assert view.status == "COMPLETED"
        assert view.completed_at_utc == clock.now
        assert view.dismissed_at_utc is None
        assert view.cancelled_at_utc is None
        assert row[7] is None
        assert row[8] == datetime_to_epoch_microseconds(clock.now)
        assert row[9] is None
    elif winner == "dismiss":
        assert view.status == "DISMISSED"
        assert view.dismissed_at_utc == clock.now
        assert view.completed_at_utc is None
        assert view.cancelled_at_utc is None
        assert row[7] == datetime_to_epoch_microseconds(clock.now)
        assert row[8] is None
        assert row[9] is None
    elif winner == "cancel":
        assert view.status == "CANCELLED"
        assert view.cancelled_at_utc == clock.now
        assert view.dismissed_at_utc is None
        assert view.completed_at_utc is None
        assert row[7] is None
        assert row[8] is None
        assert row[9] == datetime_to_epoch_microseconds(clock.now)
    else:
        assert view.status == "PENDING"
        assert view.title == "Changed"
        assert row[0] == "Changed"
        assert view.completed_at_utc is None and view.dismissed_at_utc is None and view.cancelled_at_utc is None


def test_competing_deletes_remove_the_reminder_exactly_once(application, app_config, clock) -> None:
    source = application.reminders.create_reminder(
        CreateReminder(title="Delete twice", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    observed = _watch(application)
    barrier = Barrier(2)

    def delete() -> object:
        barrier.wait()
        try:
            return application.reminders.delete_reminder(source.id, source.version)
        except (NotFoundError, StaleVersionError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(delete), executor.submit(delete)]
        results = [future.result() for future in futures]
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert [event.event_type for event in observed] == ["REMINDER_DELETED"]
    assert _count(app_config, "reminders", source.id) == 0
    assert _count(app_config, "reminder_events", source.id) == 0


def test_stale_reopen_and_delete_publish_no_domain_event(application, app_config, clock) -> None:
    source = application.reminders.create_reminder(
        CreateReminder(title="Reopen", due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    completed = application.reminders.complete_reminder(source.id, source.version)
    before = application.reminders.get_reminder(completed.id)
    count = len(_history(application, completed.id))
    observed = _watch(application)
    with pytest.raises(StaleVersionError):
        application.reminders.reopen_reminder(completed.id, completed.version - 1)
    with pytest.raises(StaleVersionError):
        application.reminders.delete_reminder(completed.id, completed.version - 1)
    assert application.reminders.get_reminder(completed.id) == before
    assert len(_history(application, completed.id)) == count
    assert _count(app_config, "reminders", completed.id) == 1
    assert observed == []


@pytest.mark.parametrize("legacy_status", ("PENDING", "TRIGGERED", "OVERDUE"))
def test_active_forbidden_reopen_preserves_every_field_and_publishes_nothing(
    application, clock, legacy_status: str
) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title=legacy_status, due_at_utc=clock.now, scheduled_timezone="UTC")
    )
    if legacy_status != "PENDING":
        with application.unit_of_work_factory() as uow:
            assert uow.session is not None
            uow.session.execute(text("UPDATE reminders SET status=:status WHERE id=:id"), {"status": legacy_status, "id": reminder.id})
            uow.commit()
    before = application.reminders.get_reminder(reminder.id)
    count = len(_history(application, reminder.id))
    observed = _watch(application)
    with pytest.raises(InvalidStateTransitionError):
        application.reminders.reopen_reminder(reminder.id, before.version)
    assert application.reminders.get_reminder(reminder.id) == before
    assert len(_history(application, reminder.id)) == count
    assert observed == []


def test_creation_failure_after_flush_leaves_no_rows_in_fresh_database(application, app_config, clock, monkeypatch) -> None:
    original = ReminderRepository.add_event

    def add_then_fail(self, event) -> None:
        original(self, event)
        raise RuntimeError("after reminder flush")

    observed = _watch(application)
    monkeypatch.setattr(ReminderRepository, "add_event", add_then_fail)
    with pytest.raises(RuntimeError, match="after reminder flush"):
        application.reminders.create_reminder(
            CreateReminder(title="Never persisted", due_at_utc=clock.now, scheduled_timezone="UTC")
        )
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM reminders")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM reminder_events")).scalar_one() == 0
    finally:
        engine.dispose()
    assert observed == []


def test_failing_subscriber_sees_committed_reminder_update(application, app_config, clock) -> None:
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Before", due_at_utc=clock.now, scheduled_timezone="UTC")
    )

    def fail(_: DomainEvent) -> None:
        raise RuntimeError("subscriber failed after commit")

    _bus(application).subscribe("REMINDER_UPDATED", fail)
    clock.now += timedelta(microseconds=1)
    updated = application.reminders.update_reminder(
        reminder.id, UpdateReminder(expected_version=reminder.version, title="After")
    )
    engine = create_sqlite_engine(app_config.database.path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT title, version, updated_at_utc FROM reminders WHERE id=:id"),
                {"id": reminder.id},
            ).one()
            durable_events = connection.execute(
                text(
                    "SELECT event_type, reminder_id FROM reminder_events "
                    "WHERE reminder_id=:id ORDER BY occurred_at_utc DESC, id"
                ),
                {"id": reminder.id},
            ).all()
    finally:
        engine.dispose()
    assert row == ("After", updated.version, datetime_to_epoch_microseconds(updated.updated_at_utc))
    assert durable_events[0] == ("REMINDER_UPDATED", reminder.id)
    assert len(durable_events) == 2
    assert application.reminders.get_reminder(reminder.id).title == "After"


def test_every_direct_command_has_exact_domain_and_durable_history_payload(application, clock) -> None:
    observed = _watch(application)
    due = clock.now
    reminder = application.reminders.create_reminder(
        CreateReminder(title="Original", due_at_utc=due, scheduled_timezone="UTC")
    )
    created_history = _history(application, reminder.id)[0]
    assert (
        created_history.event_type,
        created_history.reminder_id,
        created_history.occurred_at_utc,
        created_history.previous_status,
        created_history.new_status,
        created_history.old_due_at_utc,
        created_history.new_due_at_utc,
        created_history.metadata_json,
    ) == ("REMINDER_CREATED", reminder.id, clock.now, None, "PENDING", None, due, None)
    created_domain = observed[-1]
    assert (
        created_domain.event_type,
        created_domain.entity_id,
        created_domain.event_id,
        created_domain.occurred_at_utc,
        created_domain.payload,
    ) == (
        "REMINDER_CREATED",
        reminder.id,
        f"reminder:reminder_created:{reminder.id}:{reminder.version}",
        clock.now,
        {},
    )
    expected: list[tuple[str, str | None, str | None, object | None, object | None]] = [
        ("REMINDER_CREATED", None, "PENDING", None, due)
    ]
    task = application.tasks.create_task(CreateTask(title="Relink target"))

    def step(event_type: str, action: Callable[[], object], old_status: str, old_due: object) -> object:
        clock.now += timedelta(microseconds=1)
        result = action()
        view = result
        expected.append((event_type, old_status, view.status, old_due, view.due_at_utc))
        event = _history(application, reminder.id)[0]
        assert (
            event.event_type,
            event.reminder_id,
            event.occurred_at_utc,
            event.previous_status,
            event.new_status,
            event.old_due_at_utc,
            event.new_due_at_utc,
            event.metadata_json,
        ) == (event_type, reminder.id, clock.now, old_status, view.status, old_due, view.due_at_utc, None)
        domain = observed[-1]
        assert domain.event_type == event_type
        assert domain.entity_id == reminder.id and domain.occurred_at_utc == clock.now and domain.payload == {}
        assert domain.event_id == f"reminder:{event_type.lower()}:{reminder.id}:{view.version}"
        assert len(_history(application, reminder.id)) == len(expected)
        return view

    updated = step(
        "REMINDER_UPDATED",
        lambda: application.reminders.update_reminder(
            reminder.id,
            UpdateReminder(expected_version=reminder.version, title="Updated", task_id=task.id),
        ),
        "PENDING",
        due,
    )
    snoozed = step(
        "REMINDER_SNOOZED",
        lambda: application.reminders.snooze_reminder(
            reminder.id,
            SnoozeReminder(expected_version=updated.version, due_at_utc=clock.now + timedelta(minutes=1)),
        ),
        "PENDING",
        due,
    )
    completed = step(
        "REMINDER_COMPLETED",
        lambda: application.reminders.complete_reminder(reminder.id, snoozed.version),
        "PENDING",
        snoozed.due_at_utc,
    )
    reopened = step(
        "REMINDER_REOPENED",
        lambda: application.reminders.reopen_reminder(reminder.id, completed.version),
        "COMPLETED",
        completed.due_at_utc,
    )
    dismissed = step(
        "REMINDER_DISMISSED",
        lambda: application.reminders.dismiss_reminder(reminder.id, reopened.version),
        "PENDING",
        reopened.due_at_utc,
    )
    reopened_again = step(
        "REMINDER_REOPENED",
        lambda: application.reminders.reopen_reminder(reminder.id, dismissed.version),
        "DISMISSED",
        dismissed.due_at_utc,
    )
    cancelled = step(
        "REMINDER_CANCELLED",
        lambda: application.reminders.cancel_reminder(reminder.id, reopened_again.version),
        "PENDING",
        reopened_again.due_at_utc,
    )
    assert cancelled.status == "CANCELLED"
    assert [event.event_type for event in observed] == [item[0] for item in expected]

    count = len(_history(application, reminder.id))
    clock.now += timedelta(hours=1)
    application.reminders.get_reminder(reminder.id)
    application.reminders.list_due()
    application.reminders.list_overdue()
    assert len(_history(application, reminder.id)) == count

    # Delete is permanent: its domain event is published, the audit history goes with the row.
    clock.now += timedelta(microseconds=1)
    application.reminders.delete_reminder(reminder.id, cancelled.version)
    assert observed[-1].event_type == "REMINDER_DELETED"
    assert observed[-1].event_id == (
        f"reminder:reminder_deleted:{reminder.id}:{cancelled.version + 1}"
    )
    with pytest.raises(NotFoundError):
        _history(application, reminder.id)


def test_reminder_integrity_translation_covers_reminder_event_ids_and_unknowns() -> None:
    check = IntegrityError(
        "insert", {}, sqlite3.IntegrityError("CHECK constraint failed: ck_reminders_snooze_count")
    )
    with pytest.raises(ValidationError):
        _translate_reminder_integrity_error(check)
    event_duplicate = IntegrityError(
        "insert", {}, sqlite3.IntegrityError("UNIQUE constraint failed: reminder_events.id")
    )
    with pytest.raises(ConflictError):
        _translate_reminder_integrity_error(event_duplicate)
    unknown = IntegrityError("insert", {}, sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
    with pytest.raises(IntegrityError) as captured:
        _translate_reminder_integrity_error(unknown)
    assert captured.value is unknown


def test_linked_id_filters_and_complete_filter_membership(application, clock) -> None:
    task_a = application.tasks.create_task(CreateTask(title="Task A"))
    task_b = application.tasks.create_task(CreateTask(title="Task B"))
    note_a = application.notes.create_note(CreateNote(title="Note A"))
    note_b = application.notes.create_note(CreateNote(title="Note B"))
    task_a_reminder = application.reminders.create_reminder(
        CreateReminder(title="TA", due_at_utc=clock.now, scheduled_timezone="UTC", task_id=task_a.id)
    )
    task_b_reminder = application.reminders.create_reminder(
        CreateReminder(title="TB", due_at_utc=clock.now, scheduled_timezone="UTC", task_id=task_b.id)
    )
    note_a_reminder = application.reminders.create_reminder(
        CreateReminder(title="NA", due_at_utc=clock.now, scheduled_timezone="UTC", note_id=note_a.id)
    )
    note_b_reminder = application.reminders.create_reminder(
        CreateReminder(title="NB", due_at_utc=clock.now, scheduled_timezone="UTC", note_id=note_b.id)
    )
    completed = application.reminders.complete_reminder(task_a_reminder.id, task_a_reminder.version)
    deleted = application.reminders.delete_reminder(note_b_reminder.id, note_b_reminder.version)
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(link_type="TASK", linked_id=task_a.id))] == [task_a_reminder.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(link_type="TASK", linked_id=task_b.id))] == [task_b_reminder.id]
    assert [item.id for item in application.reminders.list_reminders(ReminderQuery(link_type="NOTE", linked_id=note_a.id))] == [note_a_reminder.id]
    assert application.reminders.list_reminders(ReminderQuery(link_type="NOTE", linked_id=note_b.id)) == ()
    assert {item.id for item in application.reminders.list_reminders(ReminderQuery(statuses=("PENDING", "COMPLETED")))} == {task_a_reminder.id, task_b_reminder.id, note_a_reminder.id}
    assert completed.status == "COMPLETED" and deleted.id == note_b_reminder.id
    with pytest.raises(PydanticValidationError):
        ReminderQuery(link_type="ANY", linked_id=task_a.id)


def test_pagination_and_history_ordering_use_primary_and_tie_breakers(application, clock) -> None:
    service = ReminderService(application.unit_of_work_factory, clock, SequenceIds())
    created = []
    for title in ("Z", "Y", "X", "W", "V"):
        created.append(service.create_reminder(CreateReminder(title=title, due_at_utc=clock.now, scheduled_timezone="UTC")))
    expected_by_sort: dict[str, list[str]] = {}
    for sort in ("due_soonest", "due_latest", "created_newest", "created_oldest", "updated_newest", "updated_oldest", "title", "status"):
        full = service.list_reminders(ReminderQuery(sort=sort, limit=200))
        first = service.list_reminders(ReminderQuery(sort=sort, limit=2, offset=0))
        second = service.list_reminders(ReminderQuery(sort=sort, limit=2, offset=2))
        again = service.list_reminders(ReminderQuery(sort=sort, limit=2, offset=0))
        expected_by_sort[sort] = [item.id for item in full]
        assert set(item.id for item in first).isdisjoint(item.id for item in second)
        assert [item.id for item in first + second] == expected_by_sort[sort][:4]
        assert first == again

    completed = []
    for index, source in enumerate(created[:4]):
        if index == 2:
            clock.now += timedelta(microseconds=1)
        completed.append(service.complete_reminder(source.id, source.version))
    with application.unit_of_work_factory() as uow:
        assert uow.session is not None
        uow.session.execute(
            text("UPDATE reminders SET updated_at_utc=:updated WHERE id=:id"),
            {
                "updated": datetime_to_epoch_microseconds(clock.now + timedelta(microseconds=1)),
                "id": completed[1].id,
            },
        )
        uow.commit()
    history = service.list_history(ReminderPageQuery(limit=200))
    expected = [completed[2].id, completed[3].id, completed[1].id, completed[0].id]
    assert [item.id for item in history] == expected
    history_page_one = service.list_history(ReminderPageQuery(limit=2, offset=0))
    history_page_two = service.list_history(ReminderPageQuery(limit=2, offset=2))
    assert history_page_one == service.list_history(ReminderPageQuery(limit=2, offset=0))
    assert history_page_two == service.list_history(ReminderPageQuery(limit=2, offset=2))
    assert set(item.id for item in history_page_one).isdisjoint(item.id for item in history_page_two)
    assert history_page_one + history_page_two == tuple(history)
    assert len({item.id for item in history_page_one + history_page_two}) == len(history)

    target = created[4]
    clock.now += timedelta(microseconds=1)
    first = service.update_reminder(target.id, UpdateReminder(expected_version=target.version, title="W1"))
    second = service.update_reminder(target.id, UpdateReminder(expected_version=first.version, title="W2"))
    clock.now += timedelta(microseconds=1)
    service.update_reminder(target.id, UpdateReminder(expected_version=second.version, title="W3"))
    events = service.list_reminder_events(target.id, ReminderPageQuery(limit=200))
    expected_event_ids = [event.id for event in sorted(events, key=lambda event: (-event.occurred_at_utc.timestamp(), event.id))]
    page_one = service.list_reminder_events(target.id, ReminderPageQuery(limit=2, offset=0))
    page_two = service.list_reminder_events(target.id, ReminderPageQuery(limit=2, offset=2))
    assert [event.id for event in events] == expected_event_ids
    assert set(event.id for event in page_one).isdisjoint(event.id for event in page_two)
    assert [event.id for event in page_one + page_two] == expected_event_ids[:4]
    assert page_one == service.list_reminder_events(target.id, ReminderPageQuery(limit=2, offset=0))
