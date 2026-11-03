"""Tag workflows."""

from __future__ import annotations

from datetime import datetime

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import DuplicateNameError, NotFoundError, StaleVersionError
from productivity_app.core.ids import IdGenerator, Uuid4Generator
from productivity_app.core.normalization import normalize_label
from productivity_app.db.models.initial import Tag
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.schemas.tags import CreateTag, RenameTag, TagView
from productivity_app.services._integrity import commit_with_translation


def _view(tag: Tag) -> TagView:
    return TagView(
        id=tag.id,
        name=tag.name,
        created_at_utc=tag.created_at_utc,
        updated_at_utc=tag.updated_at_utc,
        version=tag.version,
        deleted_at_utc=tag.deleted_at_utc,
    )


class TagService:
    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None
    ) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    def _event(self, uow: SqlAlchemyUnitOfWork, kind: str, tag: Tag, now: datetime) -> None:
        uow.collect_event(DomainEvent(kind, tag.id, now, f"tag:{kind}:{tag.id}:{tag.version}"))

    def create_tag(self, command: CreateTag) -> TagView:
        name, normalized = normalize_label(command.name)
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tags is not None
            uow.acquire_write_lock()
            if uow.tags.name_exists(normalized):
                raise DuplicateNameError("An active tag already has this name.")
            tag = Tag(
                id=self._ids.new(),
                name=name,
                normalized_name=normalized,
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            uow.tags.add(tag)
            self._event(uow, "TAG_CREATED", tag, now)
            commit_with_translation(uow)
            return _view(tag)

    def get_tag(self, tag_id: str, *, include_deleted: bool = False) -> TagView:
        with self._uows() as uow:
            assert uow.tags is not None
            tag = uow.tags.get(tag_id)
            if tag is None or (tag.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Tag was not found.")
            return _view(tag)

    def list_tags(self, *, include_deleted: bool = False) -> tuple[TagView, ...]:
        with self._uows() as uow:
            assert uow.tags is not None
            return tuple(_view(tag) for tag in uow.tags.list(include_deleted=include_deleted))

    def rename_tag(self, tag_id: str, command: RenameTag) -> TagView:
        name, normalized = normalize_label(command.name)
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tags is not None
            uow.acquire_write_lock()
            tag = uow.tags.get(tag_id)
            if tag is None or tag.deleted_at_utc is not None:
                raise NotFoundError("Tag was not found.")
            if not uow.tags.claim_version(tag.id, command.expected_version, now, deleted=False):
                raise StaleVersionError("Tag changed; refresh and retry.")
            if uow.tags.name_exists(normalized, exclude_id=tag.id):
                raise DuplicateNameError("An active tag already has this name.")
            tag.name, tag.normalized_name = name, normalized
            self._event(uow, "TAG_UPDATED", tag, now)
            commit_with_translation(uow)
            return _view(tag)

    def delete_tag(self, tag_id: str, expected_version: int) -> TagView:
        return self._set_deleted(tag_id, expected_version, True)

    def restore_tag(self, tag_id: str, expected_version: int) -> TagView:
        return self._set_deleted(tag_id, expected_version, False)

    def _set_deleted(self, tag_id: str, expected_version: int, deleted: bool) -> TagView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.tags is not None
            uow.acquire_write_lock()
            tag = uow.tags.get(tag_id)
            if (
                tag is None
                or (not deleted and tag.deleted_at_utc is None)
                or (deleted and tag.deleted_at_utc is not None)
            ):
                raise NotFoundError("Tag was not found in the requested state.")
            if not uow.tags.claim_version(tag.id, expected_version, now, deleted=not deleted):
                raise StaleVersionError("Tag changed; refresh and retry.")
            if not deleted and uow.tags.name_exists(tag.normalized_name, exclude_id=tag.id):
                raise DuplicateNameError("An active tag already has this name.")
            tag.deleted_at_utc = now if deleted else None
            self._event(uow, "TAG_DELETED" if deleted else "TAG_RESTORED", tag, now)
            commit_with_translation(uow)
            return _view(tag)
