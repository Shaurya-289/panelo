"""Canonical Phase 7 FTS5 synchronization-trigger contract."""

from __future__ import annotations

from typing import Final

FTS_SOURCE_SPECS: Final[dict[str, tuple[str, str, str]]] = {
    "tasks": ("TASK", "title", "description"),
    "notes": ("NOTE", "title", "content"),
    "reminders": ("REMINDER", "title", "message"),
    "timers": ("TIMER", "name", "''"),
    "timer_presets": ("TIMER_PRESET", "name", "''"),
}


def normalize_fts_sql(value: str) -> str:
    """Collapse formatting whitespace while preserving semantic token casing."""
    return " ".join(value.split())


def _source_trigger_sql(
    table: str, entity_type: str, title: str, body: str
) -> dict[str, str]:
    body_ref = body if body == "''" else f"NEW.{body}"
    fields = title if body == "''" else f"{title}, {body}"
    return {
        f"trg_fts_{table}_ai": (
            f"CREATE TRIGGER trg_fts_{table}_ai AFTER INSERT ON {table} BEGIN "
            "INSERT INTO search_documents(entity_type, entity_id, title, body) "
            f"VALUES ('{entity_type}', NEW.id, NEW.{title}, {body_ref}); END"
        ),
        f"trg_fts_{table}_au": (
            f"CREATE TRIGGER trg_fts_{table}_au AFTER UPDATE OF {fields} ON {table} BEGIN "
            "UPDATE search_documents SET "
            f"title = NEW.{title}, body = {body_ref} "
            f"WHERE entity_type = '{entity_type}' AND entity_id = NEW.id; END"
        ),
        f"trg_fts_{table}_ad": (
            f"CREATE TRIGGER trg_fts_{table}_ad AFTER DELETE ON {table} BEGIN "
            "DELETE FROM search_documents "
            f"WHERE entity_type = '{entity_type}' AND entity_id = OLD.id; END"
        ),
    }


DOCUMENT_FTS_TRIGGER_SQL: Final[dict[str, str]] = {
    "trg_fts_documents_ai": (
        "CREATE TRIGGER trg_fts_documents_ai AFTER INSERT ON search_documents BEGIN "
        "INSERT INTO unified_search_fts(rowid, title, body) "
        "VALUES (NEW.rowid, NEW.title, NEW.body); END"
    ),
    "trg_fts_documents_ad": (
        "CREATE TRIGGER trg_fts_documents_ad AFTER DELETE ON search_documents BEGIN "
        "INSERT INTO unified_search_fts(unified_search_fts, rowid, title, body) "
        "VALUES ('delete', OLD.rowid, OLD.title, OLD.body); END"
    ),
    "trg_fts_documents_au": (
        "CREATE TRIGGER trg_fts_documents_au AFTER UPDATE ON search_documents BEGIN "
        "INSERT INTO unified_search_fts(unified_search_fts, rowid, title, body) "
        "VALUES ('delete', OLD.rowid, OLD.title, OLD.body); "
        "INSERT INTO unified_search_fts(rowid, title, body) "
        "VALUES (NEW.rowid, NEW.title, NEW.body); END"
    ),
}

EXPECTED_FTS_TRIGGER_SQL: Final[dict[str, str]] = dict(DOCUMENT_FTS_TRIGGER_SQL)
for _table, _spec in FTS_SOURCE_SPECS.items():
    EXPECTED_FTS_TRIGGER_SQL.update(_source_trigger_sql(_table, *_spec))

EXPECTED_NORMALIZED_FTS_TRIGGER_SQL: Final[dict[str, str]] = {
    name: normalize_fts_sql(sql) for name, sql in EXPECTED_FTS_TRIGGER_SQL.items()
}

