"""Folder workflows, hierarchy validation, and dense sibling ordering."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    ConflictError,
    DuplicateNameError,
    FolderCycleError,
    NotFoundError,
    StaleVersionError,
)
from productivity_app.core.ids import IdGenerator, Uuid4Generator
from productivity_app.core.normalization import normalize_label
from productivity_app.db.models.initial import Folder
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from productivity_app.schemas.folders import (
    CreateFolder,
    FolderTreeNode,
    FolderView,
    MoveFolder,
    RenameFolder,
)
from productivity_app.services._integrity import commit_with_translation, translate_integrity_error


def _view(folder: Folder) -> FolderView:
    return FolderView(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        sort_order=folder.sort_order,
        created_at_utc=folder.created_at_utc,
        updated_at_utc=folder.updated_at_utc,
        version=folder.version,
        deleted_at_utc=folder.deleted_at_utc,
    )


class FolderService:
    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock, ids: IdGenerator | None = None
    ) -> None:
        self._uows, self._clock, self._ids = unit_of_work_factory, clock, ids or Uuid4Generator()

    def _event(self, uow: SqlAlchemyUnitOfWork, kind: str, folder: Folder, now: datetime) -> None:
        uow.collect_event(
            DomainEvent(kind, folder.id, now, f"folder:{kind}:{folder.id}:{folder.version}")
        )

    def _reorder(
        self,
        uow: SqlAlchemyUnitOfWork,
        siblings: list[Folder],
        now: datetime,
        *,
        already_versioned_ids: frozenset[str] = frozenset(),
    ) -> None:
        assert uow.folders is not None
        try:
            uow.folders.reorder(
                siblings, now, already_versioned_ids=already_versioned_ids
            )
        except IntegrityError as exc:
            translate_integrity_error(exc)

    def get_folder(self, folder_id: str, *, include_deleted: bool = False) -> FolderView:
        with self._uows() as uow:
            assert uow.folders is not None
            folder = uow.folders.get(folder_id)
            if folder is None or (folder.deleted_at_utc is not None and not include_deleted):
                raise NotFoundError("Folder was not found.")
            return _view(folder)

    def list_children(self, parent_id: str | None = None) -> tuple[FolderView, ...]:
        with self._uows() as uow:
            assert uow.folders is not None
            return tuple(_view(item) for item in uow.folders.list_children(parent_id))

    def get_tree(self) -> tuple[FolderTreeNode, ...]:
        with self._uows() as uow:
            assert uow.folders is not None
            items = uow.folders.list_active()
            by_parent: dict[str | None, list[Folder]] = {}
            for item in items:
                by_parent.setdefault(item.parent_id, []).append(item)

            def node(item: Folder) -> FolderTreeNode:
                return FolderTreeNode(
                    **_view(item).model_dump(),
                    children=tuple(node(child) for child in by_parent.get(item.id, [])),
                )

            return tuple(node(item) for item in by_parent.get(None, []))

    def create_folder(self, command: CreateFolder) -> FolderView:
        display, normalized = normalize_label(command.name)
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.folders is not None
            uow.acquire_write_lock()
            if command.parent_id is not None:
                parent = uow.folders.get(command.parent_id)
                if parent is None or parent.deleted_at_utc is not None:
                    raise NotFoundError("Parent folder was not found.")
            if uow.folders.name_exists(command.parent_id, normalized):
                raise DuplicateNameError("An active sibling already has this name.")
            siblings = uow.folders.list_children(command.parent_id)
            position = min(
                command.position if command.position is not None else len(siblings), len(siblings)
            )
            folder = Folder(
                id=self._ids.new(),
                name=display,
                normalized_name=normalized,
                parent_id=command.parent_id,
                sort_order=len(siblings),
                created_at_utc=now,
                updated_at_utc=now,
                version=1,
            )
            uow.folders.add(folder)
            siblings.insert(position, folder)
            self._reorder(uow, siblings, now)
            self._event(uow, "FOLDER_CREATED", folder, now)
            commit_with_translation(uow)
            return _view(folder)

    def rename_folder(self, folder_id: str, command: RenameFolder) -> FolderView:
        display, normalized = normalize_label(command.name)
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.folders is not None
            uow.acquire_write_lock()
            folder = uow.folders.get(folder_id)
            if folder is None or folder.deleted_at_utc is not None:
                raise NotFoundError("Folder was not found.")
            if not uow.folders.claim_version(
                folder.id, command.expected_version, now, deleted=False
            ):
                raise StaleVersionError("Folder changed; refresh and retry.")
            if uow.folders.name_exists(folder.parent_id, normalized, exclude_id=folder.id):
                raise DuplicateNameError("An active sibling already has this name.")
            folder.name, folder.normalized_name = display, normalized
            self._event(uow, "FOLDER_UPDATED", folder, now)
            commit_with_translation(uow)
            return _view(folder)

    def move_folder(self, folder_id: str, command: MoveFolder) -> FolderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.folders is not None
            uow.acquire_write_lock()
            folder = uow.folders.get(folder_id)
            if folder is None or folder.deleted_at_utc is not None:
                raise NotFoundError("Folder was not found.")
            if not uow.folders.claim_version(
                folder.id, command.expected_version, now, deleted=False
            ):
                raise StaleVersionError("Folder changed; refresh and retry.")
            if command.parent_id == folder.id or (
                command.parent_id is not None
                and uow.folders.is_descendant(command.parent_id, folder.id)
            ):
                raise FolderCycleError("A folder cannot be moved beneath itself or a descendant.")
            if command.parent_id is not None:
                parent = uow.folders.get(command.parent_id)
                if parent is None or parent.deleted_at_utc is not None:
                    raise NotFoundError("Destination folder was not found.")
            if uow.folders.name_exists(
                command.parent_id, folder.normalized_name, exclude_id=folder.id
            ):
                raise DuplicateNameError("Destination already contains this name.")
            old = uow.folders.list_children(folder.parent_id)
            old.remove(folder)
            new = (
                old
                if command.parent_id == folder.parent_id
                else uow.folders.list_children(command.parent_id)
            )
            if new is not old:
                # Move the target out of its source uniqueness scope before
                # compacting source siblings.  Its temporary destination position
                # cannot collide with dense active positions.
                folder.parent_id = command.parent_id
                folder.sort_order = 2_000_000 + len(new)
                assert uow.session is not None
                try:
                    uow.session.flush()
                except IntegrityError as exc:
                    translate_integrity_error(exc)
                self._reorder(uow, old, now)
            else:
                folder.parent_id = command.parent_id
            new.insert(min(command.position, len(new)), folder)
            self._reorder(
                uow, new, now, already_versioned_ids=frozenset({folder.id})
            )
            self._event(uow, "FOLDER_MOVED", folder, now)
            commit_with_translation(uow)
            return _view(folder)

    def reorder_folder(self, folder_id: str, position: int, expected_version: int) -> FolderView:
        return self.move_folder(
            folder_id,
            MoveFolder(
                parent_id=self.get_folder(folder_id).parent_id,
                position=position,
                expected_version=expected_version,
            ),
        )

    def delete_folder(self, folder_id: str, expected_version: int) -> FolderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.folders is not None
            uow.acquire_write_lock()
            folder = uow.folders.get(folder_id)
            if folder is None or folder.deleted_at_utc is not None:
                raise NotFoundError("Folder was not found.")
            if not uow.folders.claim_version(folder.id, expected_version, now, deleted=False):
                raise StaleVersionError("Folder changed; refresh and retry.")
            destination = uow.folders.list_children(folder.parent_id)
            promoted = uow.folders.list_children(folder.id)
            names = {item.normalized_name for item in destination if item.id != folder.id}
            if any(item.normalized_name in names for item in promoted):
                raise ConflictError("Promoting children would create a name conflict.")
            destination.remove(folder)
            for child in promoted:
                child.parent_id = folder.parent_id
            for note in uow.folders.notes_in_folder(folder.id):
                note.folder_id = folder.parent_id
                note.version += 1
                note.updated_at_utc = now
            for task in uow.folders.tasks_in_folder(folder.id):
                task.folder_id = folder.parent_id
                task.version += 1
                task.updated_at_utc = now
            folder.deleted_at_utc = now
            destination[folder.sort_order : folder.sort_order] = promoted
            self._reorder(uow, destination, now)
            self._event(uow, "FOLDER_DELETED", folder, now)
            commit_with_translation(uow)
            return _view(folder)

    def restore_folder(self, folder_id: str, expected_version: int) -> FolderView:
        now = self._clock.now_utc()
        with self._uows() as uow:
            assert uow.folders is not None
            uow.acquire_write_lock()
            folder = uow.folders.get(folder_id)
            if folder is None:
                raise NotFoundError("Folder was not found.")
            if not uow.folders.claim_version(folder.id, expected_version, now, deleted=True):
                raise StaleVersionError("Folder changed; refresh and retry.")
            if folder.parent_id is not None:
                parent = uow.folders.get(folder.parent_id)
                if parent is None or parent.deleted_at_utc is not None:
                    raise ConflictError("Original parent is unavailable.")
            if uow.folders.name_exists(
                folder.parent_id, folder.normalized_name, exclude_id=folder.id
            ):
                raise DuplicateNameError("An active sibling already has this name.")
            siblings = uow.folders.list_children(folder.parent_id)
            folder.deleted_at_utc = None
            siblings.insert(min(folder.sort_order, len(siblings)), folder)
            self._reorder(
                uow, siblings, now, already_versioned_ids=frozenset({folder.id})
            )
            self._event(uow, "FOLDER_RESTORED", folder, now)
            commit_with_translation(uow)
            return _view(folder)
