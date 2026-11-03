"""Single-statement unified SQLite search projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, case, literal, select, text, union_all
from sqlalchemy.orm import Session

from productivity_app.core.exceptions import DatabaseError
from productivity_app.db.fts_contract import (
    DOCUMENT_FTS_TRIGGER_SQL,
    EXPECTED_NORMALIZED_FTS_TRIGGER_SQL,
    normalize_fts_sql,
)
from productivity_app.db.models.initial import Folder, Note, Reminder, Tag, Task, Timer, TimerPreset
from productivity_app.db.types import datetime_to_epoch_microseconds
from productivity_app.schemas.search import FullTextSearchQuery, UnifiedSearchQuery

_ENTITY_PRIORITY = {
    "TASK": 0,
    "NOTE": 1,
    "REMINDER": 2,
    "TIMER": 3,
    "FOLDER": 4,
    "TAG": 5,
    "TIMER_PRESET": 6,
}
_ACTIVE_REMINDER_STATES = ("PENDING", "TRIGGERED", "OVERDUE")
@dataclass(frozen=True, slots=True)
class SearchRow:
    entity_type: str
    entity_id: str
    title: str
    matched_field: str
    persisted_state: str | None
    derived_state: str | None
    updated_at_utc: datetime
    is_deleted: bool


def escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_fts5_expression(value: str, mode: str) -> str:
    """Produce quoted FTS5 syntax; caller text never becomes operator syntax."""
    def quote(item: str) -> str:
        return '"' + item.replace('"', '""') + '"'

    if mode == "PHRASE":
        return quote(value)
    terms = value.split()
    if not terms:
        return '""'
    suffix = "*" if mode == "PREFIX" else ""
    return " AND ".join(f"{quote(item)}{suffix}" for item in terms)


class SearchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, query: UnifiedSearchQuery, now_utc: datetime) -> list[SearchRow]:
        escaped = escaped_like(query.query)
        contains = f"%{escaped}%"
        prefix = f"{escaped}%"
        requested: set[str] = set(query.entity_types) or set(_ENTITY_PRIORITY.keys())

        def branch(
            entity_type: str,
            model: Any,
            title_column: Any,
            body_column: Any | None = None,
            state_column: Any | None = None,
            derived_column: Any | None = None,
        ) -> Any:
            title = title_column  # SQLAlchemy expressions are intentionally structural here.
            title_exact = title.collate("NOCASE") == query.query
            title_prefix = title.like(prefix, escape="\\")
            title_contains = title.like(contains, escape="\\")
            body_contains = (
                body_column.like(contains, escape="\\") if body_column is not None else literal(False)
            )
            matches = title_contains | body_contains
            rank = case(
                (title_exact, 0),
                (title_prefix, 1),
                (title_contains, 2),
                else_=3,
            )
            matched = case(
                (title_exact | title_prefix | title_contains, "name" if entity_type in {"FOLDER", "TAG", "TIMER", "TIMER_PRESET"} else "title"),
                else_={"TASK": "description", "NOTE": "content", "REMINDER": "message"}.get(entity_type, "name"),
            )
            stmt = select(
                literal(entity_type).label("entity_type"),
                model.id.label("entity_id"),
                title.label("title"),
                matched.label("matched_field"),
                (state_column if state_column is not None else literal(None)).label("persisted_state"),
                (derived_column if derived_column is not None else literal(None)).label("derived_state"),
                model.updated_at_utc.label("updated_at_utc"),
                (model.deleted_at_utc.is_not(None)).label("is_deleted"),
                rank.label("rank"),
                literal(_ENTITY_PRIORITY[entity_type]).label("type_rank"),
            ).where(matches)
            if not query.include_deleted:
                stmt = stmt.where(model.deleted_at_utc.is_(None))
            return stmt

        branches: list[Any] = []
        if "TASK" in requested:
            branches.append(branch("TASK", Task, Task.title, Task.description, Task.status))
        if "NOTE" in requested:
            branches.append(branch("NOTE", Note, Note.title, Note.content))
        if "REMINDER" in requested:
            reminder_timing = case(
                (Reminder.status.not_in(_ACTIVE_REMINDER_STATES), None),
                (Reminder.due_at_utc > now_utc, "UPCOMING"),
                (Reminder.due_at_utc == now_utc, "DUE"),
                else_="OVERDUE",
            )
            branches.append(
                branch("REMINDER", Reminder, Reminder.title, Reminder.message, Reminder.status, reminder_timing)
            )
        if "TIMER" in requested:
            timer_effective = case(
                ((Timer.state == "RUNNING") & (Timer.deadline_at_utc <= now_utc), "FINISHED"),
                else_=Timer.state,
            )
            branches.append(branch("TIMER", Timer, Timer.name, state_column=Timer.state, derived_column=timer_effective))
        if "FOLDER" in requested:
            branches.append(branch("FOLDER", Folder, Folder.name))
        if "TAG" in requested:
            branches.append(branch("TAG", Tag, Tag.name))
        if "TIMER_PRESET" in requested:
            branches.append(branch("TIMER_PRESET", TimerPreset, TimerPreset.name))

        combined = union_all(*branches).subquery("unified_search")
        statement = (
            select(combined)
            .order_by(
                combined.c.rank.asc(),
                combined.c.updated_at_utc.desc(),
                combined.c.type_rank.asc(),
                combined.c.entity_id.asc(),
            )
            .offset(query.offset)
            .limit(query.limit)
        )
        return [
            SearchRow(
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                title=row.title,
                matched_field=row.matched_field,
                persisted_state=row.persisted_state,
                derived_state=row.derived_state,
                updated_at_utc=row.updated_at_utc,
                is_deleted=bool(row.is_deleted),
            )
            for row in self._session.execute(statement).all()
        ]

    def search_full_text(self, query: FullTextSearchQuery, now_utc: datetime) -> list[SearchRow]:
        expression = build_fts5_expression(query.text, query.mode)
        types: set[str] = {str(item) for item in query.entity_types} or set(_ENTITY_PRIORITY)
        now = datetime_to_epoch_microseconds(now_utc)
        # Source data stays authoritative for state/deletion; FTS only supplies matching rowids.
        sql = text("""
WITH matches AS (
  SELECT d.entity_type, d.entity_id, bm25(unified_search_fts, 8.0, 1.0) AS score,
         CASE WHEN highlight(unified_search_fts, 0, char(1), char(2)) <> d.title THEN 1 ELSE 0 END AS title_hit
  FROM unified_search_fts
  JOIN search_documents d ON d.rowid = unified_search_fts.rowid
  WHERE unified_search_fts MATCH :match
), entities AS (
  SELECT 'TASK' entity_type,id entity_id,title,'description' body_field,status persisted_state,NULL derived_state,updated_at_utc,deleted_at_utc FROM tasks
  UNION ALL SELECT 'NOTE',id,title,'content',NULL,NULL,updated_at_utc,deleted_at_utc FROM notes
  UNION ALL SELECT 'REMINDER',id,title,'message',status,CASE WHEN status NOT IN ('PENDING','TRIGGERED','OVERDUE') THEN NULL WHEN due_at_utc > :now THEN 'UPCOMING' WHEN due_at_utc = :now THEN 'DUE' ELSE 'OVERDUE' END,updated_at_utc,deleted_at_utc FROM reminders
  UNION ALL SELECT 'TIMER',id,name,'name',state,CASE WHEN state='RUNNING' AND deadline_at_utc <= :now THEN 'FINISHED' ELSE state END,updated_at_utc,deleted_at_utc FROM timers
  UNION ALL SELECT 'FOLDER',id,name,'name',NULL,NULL,updated_at_utc,deleted_at_utc FROM folders
  UNION ALL SELECT 'TAG',id,name,'name',NULL,NULL,updated_at_utc,deleted_at_utc FROM tags
  UNION ALL SELECT 'TIMER_PRESET',id,name,'name',NULL,NULL,updated_at_utc,deleted_at_utc FROM timer_presets
)
SELECT e.entity_type,e.entity_id,e.title,
       CASE WHEN m.title_hit=1 THEN CASE WHEN e.entity_type IN ('TIMER','FOLDER','TAG','TIMER_PRESET') THEN 'name' ELSE 'title' END ELSE e.body_field END matched_field,
       e.persisted_state,e.derived_state,e.updated_at_utc,(e.deleted_at_utc IS NOT NULL) is_deleted,
       CASE WHEN e.title COLLATE NOCASE = :raw THEN 0 WHEN e.title LIKE :prefix ESCAPE '\\' THEN 1 WHEN m.title_hit=1 THEN 2 ELSE 3 END tier,
       CASE e.entity_type WHEN 'TASK' THEN 0 WHEN 'NOTE' THEN 1 WHEN 'REMINDER' THEN 2 WHEN 'TIMER' THEN 3 WHEN 'FOLDER' THEN 4 WHEN 'TAG' THEN 5 ELSE 6 END type_rank
FROM matches m JOIN entities e ON e.entity_type=m.entity_type AND e.entity_id=m.entity_id
WHERE (:deleted=1 OR e.deleted_at_utc IS NULL)
  AND e.entity_type IN :types
ORDER BY tier ASC,m.score ASC,e.updated_at_utc DESC,type_rank ASC,e.entity_id ASC
LIMIT :limit OFFSET :offset
""").bindparams(bindparam("types", expanding=True))
        escaped = escaped_like(query.text)
        rows = self._session.execute(sql, {"match": expression, "raw": query.text, "prefix": f"{escaped}%", "now": now, "deleted": int(query.include_deleted), "types": list(types), "limit": query.limit, "offset": query.offset}).mappings().all()
        from datetime import UTC, timedelta
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        return [SearchRow(
            entity_type=row["entity_type"], entity_id=row["entity_id"], title=row["title"],
            matched_field=row["matched_field"], persisted_state=row["persisted_state"], derived_state=row["derived_state"],
            updated_at_utc=epoch + timedelta(microseconds=int(row["updated_at_utc"])), is_deleted=bool(row["is_deleted"]),
        ) for row in rows]

    def verify_full_text_index(self) -> None:
        trigger_rows = self._session.execute(text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'trg_fts_%'"
        )).all()
        actual_triggers = {
            str(name): normalize_fts_sql(str(sql)) for name, sql in trigger_rows
        }
        if actual_triggers != EXPECTED_NORMALIZED_FTS_TRIGGER_SQL:
            raise DatabaseError("Full-text synchronization triggers are inconsistent.")
        mismatch = self._session.scalar(text("""
WITH expected(entity_type, entity_id, title, body) AS (
  SELECT 'TASK', id, title, description FROM tasks
  UNION ALL SELECT 'NOTE', id, title, content FROM notes
  UNION ALL SELECT 'REMINDER', id, title, message FROM reminders
  UNION ALL SELECT 'TIMER', id, name, '' FROM timers
  UNION ALL SELECT 'FOLDER', id, name, '' FROM folders
  UNION ALL SELECT 'TAG', id, name, '' FROM tags
  UNION ALL SELECT 'TIMER_PRESET', id, name, '' FROM timer_presets
)
SELECT 1 FROM (
  SELECT entity_type, entity_id, title, body FROM expected
  EXCEPT SELECT entity_type, entity_id, title, body FROM search_documents
)
UNION ALL
SELECT 1 FROM (
  SELECT entity_type, entity_id, title, body FROM search_documents
  EXCEPT SELECT entity_type, entity_id, title, body FROM expected
)
LIMIT 1
"""))
        if mismatch is not None:
            raise DatabaseError("Full-text document projection is inconsistent.")
        self._session.execute(text("INSERT INTO unified_search_fts(unified_search_fts, rank) VALUES('integrity-check', 1)"))

    def rebuild_full_text_index(self) -> None:
        # A damaged external-content index may reject delete commands emitted by
        # the normal document triggers. Suppress only those three triggers while
        # reconstructing the projection, then rebuild FTS from the final rows.
        # The caller holds BEGIN IMMEDIATE, so SQLite DDL and data changes roll
        # back together if any step or final verification fails.
        for name in DOCUMENT_FTS_TRIGGER_SQL:
            self._session.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        self._reconstruct_search_documents()
        self._session.execute(text("INSERT INTO unified_search_fts(unified_search_fts) VALUES('rebuild')"))
        for sql in DOCUMENT_FTS_TRIGGER_SQL.values():
            self._session.execute(text(sql))
        self.verify_full_text_index()

    def _reconstruct_search_documents(self) -> None:
        """Replace the derived projection while its FTS triggers are suppressed."""
        self._session.execute(text("DELETE FROM search_documents"))
        self._session.execute(text("""
INSERT INTO search_documents(entity_type, entity_id, title, body)
SELECT 'TASK', id, title, description FROM tasks
UNION ALL SELECT 'NOTE', id, title, content FROM notes
UNION ALL SELECT 'REMINDER', id, title, message FROM reminders
UNION ALL SELECT 'TIMER', id, name, '' FROM timers
UNION ALL SELECT 'FOLDER', id, name, '' FROM folders
UNION ALL SELECT 'TAG', id, name, '' FROM tags
UNION ALL SELECT 'TIMER_PRESET', id, name, '' FROM timer_presets
"""))
