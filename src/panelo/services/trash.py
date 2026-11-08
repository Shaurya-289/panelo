"""The Trash: soft-deleted notes and tasks, restoring them, and deleting them for good.

Restoring uses ``NoteService.restore_note`` / ``TaskService.restore_task`` (each trash item
carries the version they expect).  Anything that stays in the Trash for ``TRASH_RETENTION_DAYS``
is purged by ``purge_expired``.  Purging a task or note detaches the reminders linked to it
(they keep existing, without the link) and publishes ``REMINDER_UPDATED`` for each.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from panelo.core.clock import Clock
from panelo.core.events import DomainEvent
from panelo.core.exceptions import NotFoundError
from panelo.db.models.initial import Note, Task
from panelo.db.types import datetime_to_epoch_microseconds
from panelo.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from panelo.schemas.trash import TrashItem, TrashKind

TRASH_RETENTION_DAYS: Final = 30
_PREVIEW_LENGTH: Final = 200


def _preview(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:_PREVIEW_LENGTH]
    return ""


def _item(kind: TrashKind, entity: Note | Task) -> TrashItem:
    assert entity.deleted_at_utc is not None
    text = entity.content if isinstance(entity, Note) else entity.description
    return TrashItem(
        kind=kind,
        id=entity.id,
        title=entity.title,
        preview=_preview(text),
        deleted_at_utc=entity.deleted_at_utc,
        version=entity.version,
    )


class TrashService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uows, self._clock = unit_of_work_factory, clock

    def list_trash(self) -> tuple[TrashItem, ...]:
        """Deleted notes and tasks together, the most recently deleted first."""
        with self._uows() as uow:
            assert uow.notes is not None and uow.tasks is not None
            items = [_item("NOTE", note) for note in uow.notes.list_deleted()]
            items += [_item("TASK", task) for task in uow.tasks.list_deleted()]
        items.sort(key=lambda item: (-datetime_to_epoch_microseconds(item.deleted_at_utc), item.id))
        return tuple(items)

    def delete_forever(self, kind: TrashKind, entity_id: str) -> None:
        """Hard-delete one item that is in the Trash (an item outside it is not found)."""
        now = self._clock.now_utc()
        with self._uows() as uow:
            uow.acquire_write_lock()
            self._purge(uow, kind, entity_id, now)
            uow.commit()

    def empty_trash(self) -> int:
        """Hard-delete everything in the Trash; returns how many items went."""
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None and uow.tasks is not None
            uow.acquire_write_lock()
            count = self._purge_all(uow, uow.notes.list_deleted(), uow.tasks.list_deleted(), now)
            uow.commit()
        return count

    def purge_expired(self, now: datetime | None = None, days: int = TRASH_RETENTION_DAYS) -> int:
        """Hard-delete what has been in the Trash for ``days`` days or longer."""
        if days < 0:
            raise ValueError("days must not be negative.")
        moment = now or self._clock.now_utc()
        cutoff = moment - timedelta(days=days)
        with self._uows() as uow:
            assert uow.notes is not None and uow.tasks is not None
            uow.acquire_write_lock()
            count = self._purge_all(
                uow,
                uow.notes.list_deleted(deleted_through_utc=cutoff),
                uow.tasks.list_deleted(deleted_through_utc=cutoff),
                moment,
            )
            if count:
                uow.commit()
        return count

    def _purge_all(
        self, uow: SqlAlchemyUnitOfWork, notes: list[Note], tasks: list[Task], now: datetime
    ) -> int:
        for note in notes:
            self._purge(uow, "NOTE", note.id, now)
        for task in tasks:
            self._purge(uow, "TASK", task.id, now)
        return len(notes) + len(tasks)

    @staticmethod
    def _purge(uow: SqlAlchemyUnitOfWork, kind: TrashKind, entity_id: str, now: datetime) -> None:
        assert uow.notes is not None and uow.tasks is not None and uow.reminders is not None
        if kind == "NOTE":
            note = uow.notes.get(entity_id)
            if note is None or note.deleted_at_utc is None:
                raise NotFoundError("Deleted note was not found.")
            detached = uow.reminders.clear_links(note_id=entity_id, now_utc=now)
            uow.notes.delete(entity_id)
            event, prefix = "NOTE_PURGED", "note"
        else:
            task = uow.tasks.get(entity_id)
            if task is None or task.deleted_at_utc is None:
                raise NotFoundError("Deleted task was not found.")
            detached = uow.reminders.clear_links(task_id=entity_id, now_utc=now)
            uow.tasks.delete(entity_id)
            event, prefix = "TASK_PURGED", "task"
        uow.collect_event(DomainEvent(event, entity_id, now, f"{prefix}:purged:{entity_id}"))
        for reminder_id in detached:
            uow.collect_event(
                DomainEvent(
                    "REMINDER_UPDATED",
                    reminder_id,
                    now,
                    f"reminder:unlinked:{reminder_id}:{prefix}:{entity_id}",
                )
            )
