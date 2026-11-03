"""Persistence queries for notes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import Note
from productivity_app.schemas.notes import NoteQuery


class NoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, note_id: str) -> Note | None:
        return self._session.get(Note, note_id)

    def add(self, note: Note) -> None:
        self._session.add(note)

    def list_deleted(self, *, deleted_through_utc: datetime | None = None) -> list[Note]:
        """Soft-deleted notes, newest deletion first (optionally only those deleted by an instant)."""
        query = select(Note).where(Note.deleted_at_utc.is_not(None))
        if deleted_through_utc is not None:
            query = query.where(Note.deleted_at_utc <= deleted_through_utc)
        return list(self._session.scalars(query.order_by(Note.deleted_at_utc.desc(), Note.id)))

    def delete(self, note_id: str) -> None:
        """Remove the row for good (the caller clears reminder links first)."""
        self._session.execute(delete(Note).where(Note.id == note_id))

    def list_notes(self, query_data: NoteQuery) -> list[Note]:
        query = select(Note)
        if not query_data.include_deleted:
            query = query.where(Note.deleted_at_utc.is_(None))
        if query_data.is_pinned is not None:
            query = query.where(Note.is_pinned == query_data.is_pinned)
        if query_data.is_archived is not None:
            query = query.where(Note.is_archived == query_data.is_archived)
        ordering = {
            "updated_desc": (Note.updated_at_utc.desc(), Note.id),
            "updated_asc": (Note.updated_at_utc, Note.id),
            "created_desc": (Note.created_at_utc.desc(), Note.id),
            "created_asc": (Note.created_at_utc, Note.id),
            "title": (Note.title, Note.id),
            "pinned_first": (Note.is_pinned.desc(), Note.updated_at_utc.desc(), Note.id),
        }[query_data.sort]
        return list(
            self._session.scalars(
                query.order_by(*ordering).offset(query_data.offset).limit(query_data.limit)
            )
        )

    def search_like(
        self, literal_query: str, escaped_query: str, *, include_archived: bool, limit: int
    ) -> list[Note]:
        pattern = f"%{escaped_query}%"
        exact = case(
            (Note.title == literal_query, 0),
            (Note.title.like(f"{escaped_query}%", escape="\\"), 1),
            else_=2,
        )
        query = select(Note).where(
            Note.deleted_at_utc.is_(None),
            (Note.title.like(pattern, escape="\\") | Note.content.like(pattern, escape="\\")),
        )
        if not include_archived:
            query = query.where(Note.is_archived.is_(False))
        return list(
            self._session.scalars(
                query.order_by(exact, Note.updated_at_utc.desc(), Note.id).limit(limit)
            )
        )

    def claim_version(
        self, note_id: str, expected_version: int, now_utc: datetime, *, deleted: bool
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Note)
                .where(
                    Note.id == note_id,
                    Note.version == expected_version,
                    Note.deleted_at_utc.is_not(None) if deleted else Note.deleted_at_utc.is_(None),
                )
                .values(version=Note.version + 1, updated_at_utc=now_utc)
            ),
        )
        return result.rowcount == 1
