"""Persistence queries for folders; workflow decisions stay in FolderService."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import inspect, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import Folder, Note, Task


class FolderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, folder_id: str) -> Folder | None:
        return self._session.get(Folder, folder_id)

    def list_children(
        self, parent_id: str | None, *, include_deleted: bool = False
    ) -> list[Folder]:
        query = select(Folder).where(Folder.parent_id == parent_id)
        if not include_deleted:
            query = query.where(Folder.deleted_at_utc.is_(None))
        return list(self._session.scalars(query.order_by(Folder.sort_order, Folder.id)))

    def list_active(self) -> list[Folder]:
        return list(
            self._session.scalars(
                select(Folder)
                .where(Folder.deleted_at_utc.is_(None))
                .order_by(Folder.parent_id, Folder.sort_order, Folder.id)
            )
        )

    def name_exists(
        self, parent_id: str | None, normalized_name: str, *, exclude_id: str | None = None
    ) -> bool:
        query = select(Folder.id).where(
            Folder.parent_id == parent_id,
            Folder.normalized_name == normalized_name,
            Folder.deleted_at_utc.is_(None),
        )
        if exclude_id is not None:
            query = query.where(Folder.id != exclude_id)
        return self._session.scalar(query) is not None

    def add(self, folder: Folder) -> None:
        self._session.add(folder)

    def is_descendant(self, candidate_id: str, ancestor_id: str) -> bool:
        current = self.get(candidate_id)
        seen: set[str] = set()
        while current is not None and current.parent_id is not None and current.id not in seen:
            if current.parent_id == ancestor_id:
                return True
            seen.add(current.id)
            current = self.get(current.parent_id)
        return False

    def reorder(
        self,
        siblings: list[Folder],
        now_utc: datetime,
        *,
        already_versioned_ids: frozenset[str] = frozenset(),
    ) -> None:
        # Offset first so the partial unique index cannot observe transient collisions.
        versioned: list[Folder] = []
        for index, folder in enumerate(siblings):
            state = inspect(folder)
            parent_changed = state.attrs.parent_id.history.has_changes()
            if (
                state.persistent
                and folder.id not in already_versioned_ids
                and (folder.sort_order != index or parent_changed)
            ):
                versioned.append(folder)
        for index, folder in enumerate(siblings):
            folder.sort_order = 1_000_000 + index
        self._session.flush()
        for index, folder in enumerate(siblings):
            folder.sort_order = index
        for folder in versioned:
            folder.version += 1
            folder.updated_at_utc = now_utc
        self._session.flush()

    def notes_in_folder(self, folder_id: str) -> list[Note]:
        return list(self._session.scalars(select(Note).where(Note.folder_id == folder_id)))

    def tasks_in_folder(self, folder_id: str) -> list[Task]:
        """Return direct tasks, including soft-deleted tasks, for folder relocation."""
        return list(self._session.scalars(select(Task).where(Task.folder_id == folder_id)))

    def claim_version(
        self, folder_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Folder)
                .where(
                    Folder.id == folder_id,
                    Folder.version == expected_version,
                    Folder.deleted_at_utc.is_not(None)
                    if deleted
                    else Folder.deleted_at_utc.is_(None),
                )
                .values(version=Folder.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1
