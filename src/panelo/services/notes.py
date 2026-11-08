"""Note workflows, filtering, and escaped LIKE search."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from panelo.core.clock import Clock
from panelo.core.events import DomainEvent
from panelo.core.exceptions import NotFoundError, StaleVersionError, ValidationError
from panelo.core.ids import IdGenerator, Uuid4Generator
from panelo.db.models.initial import Note
from panelo.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from panelo.schemas.notes import (
    CreateNote,
    NoteColor,
    NoteQuery,
    NoteSearchQuery,
    NoteView,
    UpdateNote,
)
from panelo.services._integrity import commit_with_translation


def _view(note: Note) -> NoteView:
    return NoteView(
        id=note.id,
        title=note.title,
        content=note.content,
        is_pinned=note.is_pinned,
        is_archived=note.is_archived,
        color=cast(NoteColor, note.color),
        created_at_utc=note.created_at_utc,
        updated_at_utc=note.updated_at_utc,
        version=note.version,
        deleted_at_utc=note.deleted_at_utc,
    )


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class NoteService:
    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None
    ) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    def _event(self, uow: SqlAlchemyUnitOfWork, kind: str, note: Note, now: datetime) -> None:
        uow.collect_event(DomainEvent(kind, note.id, now, f"note:{kind}:{note.id}:{note.version}"))

    def create_note(self, command: CreateNote) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None
            uow.acquire_write_lock()
            note = Note(
                id=self._ids.new(),
                title=command.title,
                content=command.content,
                content_format="PLAIN_TEXT",
                is_pinned=command.is_pinned,
                is_archived=False,
                color=command.color,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            uow.notes.add(note)
            self._event(uow, "NOTE_CREATED", note, now)
            commit_with_translation(uow)
            return _view(note)

    def get_note(self, note_id: str, *, include_deleted: bool = False) -> NoteView:
        with self._uows() as uow:
            assert uow.notes is not None
            note = uow.notes.get(note_id)
            if note is None or (note.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Note was not found.")
            return _view(note)

    def list_notes(self, query: NoteQuery | None = None) -> tuple[NoteView, ...]:
        data = query or NoteQuery()
        with self._uows() as uow:
            assert uow.notes is not None
            return tuple(_view(note) for note in uow.notes.list_notes(data))

    def update_note(self, note_id: str, patch: UpdateNote) -> NoteView:
        values = patch.model_dump(exclude={"expected_version"}, exclude_unset=True)
        if not values:
            raise ValidationError("Note update must include at least one field.")
        if any(value is None for value in values.values()):
            raise ValidationError("Note update fields cannot be null.")
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is not None:
                raise NotFoundError("Note was not found.")
            if not uow.notes.claim_version(note.id, patch.expected_version, now, deleted=False):
                raise StaleVersionError("Note changed; refresh and retry.")
            for key, value in values.items():
                setattr(note, key, value)
            self._event(uow, "NOTE_UPDATED", note, now)
            commit_with_translation(uow)
            return _view(note)

    def pin_note(self, note_id: str, expected_version: int) -> NoteView:
        return self._mutate(note_id, expected_version, "NOTE_PINNED", is_pinned=True)

    def unpin_note(self, note_id: str, expected_version: int) -> NoteView:
        return self._mutate(note_id, expected_version, "NOTE_UNPINNED", is_pinned=False)

    def archive_note(self, note_id: str, expected_version: int) -> NoteView:
        return self._mutate(note_id, expected_version, "NOTE_ARCHIVED", is_archived=True)

    def restore_from_archive(self, note_id: str, expected_version: int) -> NoteView:
        return self._mutate(note_id, expected_version, "NOTE_UNARCHIVED", is_archived=False)

    def _mutate(
        self, note_id: str, expected_version: int, event: str, **values: object
    ) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is not None:
                raise NotFoundError("Note was not found.")
            if not uow.notes.claim_version(note.id, expected_version, now, deleted=False):
                raise StaleVersionError("Note changed; refresh and retry.")
            for key, value in values.items():
                setattr(note, key, value)
            self._event(uow, event, note, now)
            commit_with_translation(uow)
            return _view(note)

    def delete_note(self, note_id: str, expected_version: int) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is not None:
                raise NotFoundError("Note was not found.")
            if not uow.notes.claim_version(note.id, expected_version, now, deleted=False):
                raise StaleVersionError("Note changed; refresh and retry.")
            note.deleted_at_utc = now
            self._event(uow, "NOTE_DELETED", note, now)
            commit_with_translation(uow)
            return _view(note)

    def restore_note(self, note_id: str, expected_version: int) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is None:
                raise NotFoundError("Deleted note was not found.")
            if not uow.notes.claim_version(note.id, expected_version, now, deleted=True):
                raise StaleVersionError("Note changed; refresh and retry.")
            note.deleted_at_utc = None
            self._event(uow, "NOTE_RESTORED", note, now)
            commit_with_translation(uow)
            return _view(note)

    def search_notes(self, query: NoteSearchQuery) -> tuple[NoteView, ...]:
        term = query.query.strip()
        if not term:
            raise ValidationError("Search query must not be empty.")
        with self._uows() as uow:
            assert uow.notes is not None
            return tuple(
                _view(note)
                for note in uow.notes.search_like(
                    term,
                    _escaped_like(term),
                    include_archived=query.include_archived,
                    limit=query.limit,
                )
            )
