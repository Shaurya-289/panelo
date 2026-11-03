"""Persistence queries for reusable tags."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import Tag


class TagRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tag_id: str) -> Tag | None:
        return self._session.get(Tag, tag_id)

    def get_active(self, tag_id: str) -> Tag | None:
        tag = self.get(tag_id)
        return tag if tag is not None and tag.deleted_at_utc is None else None

    def name_exists(self, normalized_name: str, *, exclude_id: str | None = None) -> bool:
        query = select(Tag.id).where(
            Tag.normalized_name == normalized_name, Tag.deleted_at_utc.is_(None)
        )
        if exclude_id is not None:
            query = query.where(Tag.id != exclude_id)
        return self._session.scalar(query) is not None

    def list(self, *, include_deleted: bool = False) -> list[Tag]:
        query = select(Tag)
        if not include_deleted:
            query = query.where(Tag.deleted_at_utc.is_(None))
        return list(self._session.scalars(query.order_by(Tag.normalized_name, Tag.id)))

    def add(self, tag: Tag) -> None:
        self._session.add(tag)

    def claim_version(
        self, tag_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Tag)
                .where(
                    Tag.id == tag_id,
                    Tag.version == expected_version,
                    Tag.deleted_at_utc.is_not(None) if deleted else Tag.deleted_at_utc.is_(None),
                )
                .values(version=Tag.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1
