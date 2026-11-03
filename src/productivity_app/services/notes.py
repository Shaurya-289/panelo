"""Note workflows, tag assignment, filtering, and escaped LIKE search."""

from __future__ import annotations

from datetime import datetime

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import NotFoundError, StaleVersionError, ValidationError
from productivity_app.core.ids import IdGenerator, Uuid4Generator
from productivity_app.db.models.initial import Note
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.schemas.notes import (
    CreateNote,
    NoteQuery,
    NoteSearchQuery,
    NoteView,
    UpdateNote,
)
from productivity_app.services._integrity import commit_with_translation


def _view(note: Note, tag_ids: tuple[str, ...]) -> NoteView:
    return NoteView(
        id=note.id,
        title=note.title,
        content=note.content,
        folder_id=note.folder_id,
        tag_ids=tag_ids,
        is_pinned=note.is_pinned,
        is_archived=note.is_archived,
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

    def _active_folder(self, uow: SqlAlchemyUnitOfWork, folder_id: str | None) -> None:
        if folder_id is None:
            return
        assert uow.folders is not None
        folder = uow.folders.get(folder_id)
        if folder is None or folder.deleted_at_utc is not None:
            raise NotFoundError("Folder was not found.")

    def _active_tags(self, uow: SqlAlchemyUnitOfWork, tag_ids: tuple[str, ...]) -> tuple[str, ...]:
        unique = tuple(dict.fromkeys(tag_ids))
        for tag_id in unique:
            assert uow.tags is not None
            if uow.tags.get_active(tag_id) is None:
                raise NotFoundError("Tag was not found.")
        return unique

    def create_note(self, command: CreateNote) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None and uow.folders is not None and uow.tags is not None
            uow.acquire_write_lock()
            self._active_folder(uow, command.folder_id)
            tag_ids = self._active_tags(uow, command.tag_ids)
            note = Note(
                id=self._ids.new(),
                title=command.title,
                content=command.content,
                content_format="PLAIN_TEXT",
                folder_id=command.folder_id,
                is_pinned=command.is_pinned,
                is_archived=False,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            uow.notes.add(note)
            assert uow.session is not None
            uow.session.flush()
            uow.notes.replace_tags(note.id, tag_ids)
            self._event(uow, "NOTE_CREATED", note, now)
            commit_with_translation(uow)
            return _view(note, tag_ids)

    def get_note(self, note_id: str, *, include_deleted: bool = False) -> NoteView:
        with self._uows() as uow:
            assert uow.notes is not None
            note = uow.notes.get(note_id)
            if note is None or (note.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Note was not found.")
            return _view(note, uow.notes.tag_ids(note.id))

    def list_notes(self, query: NoteQuery | None = None) -> tuple[NoteView, ...]:
        data = query or NoteQuery()
        with self._uows() as uow:
            assert uow.notes is not None
            return tuple(
                _view(note, uow.notes.tag_ids(note.id)) for note in uow.notes.list_notes(data)
            )

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
            return _view(note, uow.notes.tag_ids(note.id))

    def move_note(self, note_id: str, folder_id: str | None, expected_version: int) -> NoteView:
        return self._mutate(note_id, expected_version, "NOTE_MOVED", folder_id=folder_id)

    def set_note_tags(
        self, note_id: str, tag_ids: tuple[str, ...], expected_version: int
    ) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None and uow.tags is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is not None:
                raise NotFoundError("Note was not found.")
            if not uow.notes.claim_version(note.id, expected_version, now, deleted=False):
                raise StaleVersionError("Note changed; refresh and retry.")
            active = self._active_tags(uow, tag_ids)
            uow.notes.replace_tags(note.id, active)
            self._event(uow, "NOTE_TAGS_CHANGED", note, now)
            commit_with_translation(uow)
            return _view(note, active)

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
            assert uow.notes is not None and uow.folders is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is not None:
                raise NotFoundError("Note was not found.")
            if not uow.notes.claim_version(note.id, expected_version, now, deleted=False):
                raise StaleVersionError("Note changed; refresh and retry.")
            if "folder_id" in values:
                self._active_folder(
                    uow, values["folder_id"] if isinstance(values["folder_id"], str) else None
                )
            for key, value in values.items():
                setattr(note, key, value)
            self._event(uow, event, note, now)
            commit_with_translation(uow)
            return _view(note, uow.notes.tag_ids(note.id))

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
            return _view(note, uow.notes.tag_ids(note.id, active_only=False))

    def restore_note(self, note_id: str, expected_version: int) -> NoteView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.notes is not None and uow.folders is not None
            uow.acquire_write_lock()
            note = uow.notes.get(note_id)
            if note is None or note.deleted_at_utc is None:
                raise NotFoundError("Deleted note was not found.")
            if not uow.notes.claim_version(note.id, expected_version, now, deleted=True):
                raise StaleVersionError("Note changed; refresh and retry.")
            if note.folder_id is not None:
                folder = uow.folders.get(note.folder_id)
                if folder is None or folder.deleted_at_utc is not None:
                    note.folder_id = None
            note.deleted_at_utc = None
            self._event(uow, "NOTE_RESTORED", note, now)
            commit_with_translation(uow)
            return _view(note, uow.notes.tag_ids(note.id))

    def search_notes(self, query: NoteSearchQuery) -> tuple[NoteView, ...]:
        term = query.query.strip()
        if not term:
            raise ValidationError("Search query must not be empty.")
        with self._uows() as uow:
            assert uow.notes is not None
            return tuple(
                _view(note, uow.notes.tag_ids(note.id))
                for note in uow.notes.search_like(
                    term,
                    _escaped_like(term),
                    include_archived=query.include_archived,
                    limit=query.limit,
                )
            )
