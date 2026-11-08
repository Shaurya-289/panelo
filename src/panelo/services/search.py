"""DTO-only unified read service."""

from __future__ import annotations

from typing import Any, cast

from panelo.core.clock import Clock
from panelo.db.unit_of_work import UnitOfWorkFactory
from panelo.schemas.search import FullTextSearchQuery, SearchResult, UnifiedSearchQuery


class SearchService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def search(self, query: UnifiedSearchQuery) -> tuple[SearchResult, ...]:
        now_utc = self._clock.now_utc()
        with self._unit_of_work_factory() as uow:
            assert uow.search is not None
            rows = uow.search.search(query, now_utc)
        return tuple(
            SearchResult(
                entity_type=cast(Any, row.entity_type),
                entity_id=row.entity_id,
                title=row.title,
                matched_field=cast(Any, row.matched_field),
                persisted_state=row.persisted_state,
                derived_state=row.derived_state,
                updated_at_utc=row.updated_at_utc,
                is_deleted=row.is_deleted,
            )
            for row in rows
        )

    def search_full_text(self, query: FullTextSearchQuery) -> tuple[SearchResult, ...]:
        now_utc = self._clock.now_utc()
        with self._unit_of_work_factory() as uow:
            assert uow.search is not None
            rows = uow.search.search_full_text(query, now_utc)
        return tuple(
            SearchResult(
                entity_type=cast(Any, row.entity_type), entity_id=row.entity_id, title=row.title,
                matched_field=cast(Any, row.matched_field), persisted_state=row.persisted_state,
                derived_state=row.derived_state, updated_at_utc=row.updated_at_utc, is_deleted=row.is_deleted,
            ) for row in rows
        )

    def rebuild_full_text_index(self) -> None:
        with self._unit_of_work_factory() as uow:
            uow.acquire_write_lock()
            assert uow.search is not None
            uow.search.rebuild_full_text_index()
            uow.commit()
