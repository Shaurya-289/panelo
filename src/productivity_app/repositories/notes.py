"""Persistence queries for notes and note-tag associations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import Note, Tag, note_tags
from productivity_app.schemas.notes import NoteQuery


class NoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, note_id: str) -> Note | None:
        return self._session.get(Note, note_id)

    def add(self, note: Note) -> None:
        self._session.add(note)

    def tag_ids(self, note_id: str, *, active_only: bool = True) -> tuple[str, ...]:
        query = select(note_tags.c.tag_id).where(note_tags.c.note_id == note_id)
        if active_only:
            query = query.join(Tag, Tag.id == note_tags.c.tag_id).where(
                Tag.deleted_at_utc.is_(None)
            )
        return tuple(self._session.scalars(query.order_by(note_tags.c.tag_id)))

    def replace_tags(self, note_id: str, tag_ids: tuple[str, ...]) -> None:
        self._session.execute(delete(note_tags).where(note_tags.c.note_id == note_id))
        if tag_ids:
            self._session.execute(
                note_tags.insert(), [{"note_id": note_id, "tag_id": tag_id} for tag_id in tag_ids]
            )

    def list_notes(self, query_data: NoteQuery) -> list[Note]:
        query = select(Note)
        if not query_data.include_deleted:
            query = query.where(Note.deleted_at_utc.is_(None))
        if query_data.folder_id is not None:
            query = query.where(Note.folder_id == query_data.folder_id)
        if query_data.tag_id is not None:
            query = query.join(note_tags).join(Tag, Tag.id == note_tags.c.tag_id).where(
                note_tags.c.tag_id == query_data.tag_id,
                Tag.deleted_at_utc.is_(None),
            )
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
