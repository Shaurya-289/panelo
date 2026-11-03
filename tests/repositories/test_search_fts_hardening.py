from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from productivity_app.core.exceptions import DatabaseError
from productivity_app.db.fts_contract import (
    DOCUMENT_FTS_TRIGGER_SQL,
    EXPECTED_NORMALIZED_FTS_TRIGGER_SQL,
    normalize_fts_sql,
)
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork
from productivity_app.repositories.search import SearchRepository
from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.notes import CreateNote
from productivity_app.schemas.reminders import CreateReminder
from productivity_app.schemas.search import FullTextSearchQuery
from productivity_app.schemas.tags import CreateTag
from productivity_app.schemas.tasks import CreateTask
from productivity_app.schemas.timers import CreateTimer, CreateTimerPreset, UpdateTimer


def _fts_ids(path, expression: str) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT d.entity_id FROM unified_search_fts f "
                "JOIN search_documents d ON d.rowid=f.rowid "
                "WHERE unified_search_fts MATCH ? ORDER BY d.entity_id",
                (expression,),
            )
        ]
    finally:
        connection.close()


def _flush_then_fail(monkeypatch, assertion) -> None:
    def fail_commit(self: SqlAlchemyUnitOfWork) -> None:
        assert self.session is not None
        self.session.flush()
        assertion(self.session)
        self.session.rollback()
        self._events.clear()
        raise RuntimeError("forced rollback after FTS triggers")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)


def test_indexed_insert_rollback_removes_source_document_and_fts(
    application, app_config, monkeypatch
) -> None:
    def asserted(session) -> None:
        assert session.execute(
            text("SELECT count(*) FROM timers WHERE name='Rollback insert sentinel'")
        ).scalar_one() == 1
        assert session.execute(
            text("SELECT count(*) FROM search_documents WHERE title='Rollback insert sentinel'")
        ).scalar_one() == 1
        assert session.execute(
            text("SELECT count(*) FROM unified_search_fts WHERE unified_search_fts MATCH 'sentinel'")
        ).scalar_one() == 1

    _flush_then_fail(monkeypatch, asserted)
    with pytest.raises(RuntimeError, match="forced rollback"):
        application.timers.create_timer(
            CreateTimer(name="Rollback insert sentinel", planned_duration_ms=60_000)
        )

    with application.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM timers WHERE name='Rollback insert sentinel'")
        ) == 0
        assert connection.scalar(
            text("SELECT count(*) FROM search_documents WHERE title='Rollback insert sentinel'")
        ) == 0
    assert _fts_ids(app_config.database.path, '"sentinel"') == []


def test_indexed_update_rollback_keeps_old_source_document_and_fts(
    application, app_config, monkeypatch
) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Committed oldword", planned_duration_ms=60_000)
    )

    def asserted(session) -> None:
        assert session.execute(
            text("SELECT name FROM timers WHERE id=:id"), {"id": timer.id}
        ).scalar_one() == "Transactional newword"
        assert session.execute(
            text("SELECT title FROM search_documents WHERE entity_type='TIMER' AND entity_id=:id"),
            {"id": timer.id},
        ).scalar_one() == "Transactional newword"

    _flush_then_fail(monkeypatch, asserted)
    with pytest.raises(RuntimeError, match="forced rollback"):
        application.timers.update_timer(
            timer.id,
            UpdateTimer(expected_version=timer.version, name="Transactional newword"),
        )

    with application.engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM timers WHERE id=:id"), {"id": timer.id}) == "Committed oldword"
        assert connection.scalar(
            text("SELECT title FROM search_documents WHERE entity_type='TIMER' AND entity_id=:id"),
            {"id": timer.id},
        ) == "Committed oldword"
    assert _fts_ids(app_config.database.path, '"oldword"') == [timer.id]
    assert _fts_ids(app_config.database.path, '"newword"') == []


def test_hard_delete_rollback_restores_source_document_and_fts(application, app_config) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Rollback delete marker", planned_duration_ms=60_000)
    )
    connection = sqlite3.connect(app_config.database.path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM timers WHERE id=?", (timer.id,))
        assert connection.execute(
            "SELECT count(*) FROM search_documents WHERE entity_id=?", (timer.id,)
        ).fetchone()[0] == 0
        connection.rollback()
    finally:
        connection.close()
    with application.engine.connect() as fresh:
        assert fresh.scalar(text("SELECT count(*) FROM timers WHERE id=:id"), {"id": timer.id}) == 1
        assert fresh.scalar(
            text("SELECT count(*) FROM search_documents WHERE entity_id=:id"), {"id": timer.id}
        ) == 1
    assert _fts_ids(app_config.database.path, '"marker"') == [timer.id]


def test_trigger_failure_aborts_source_update_atomically(application, app_config) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Atomic original", planned_duration_ms=60_000)
    )
    with application.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_fts_documents_au"))
        connection.execute(text(
            "CREATE TRIGGER trg_fts_documents_au AFTER UPDATE ON search_documents "
            "BEGIN SELECT RAISE(ABORT, 'forced FTS trigger failure'); END"
        ))
    with pytest.raises(Exception, match="forced FTS trigger failure"):
        application.timers.update_timer(
            timer.id,
            UpdateTimer(expected_version=timer.version, name="Atomic changed"),
        )
    with application.engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM timers WHERE id=:id"), {"id": timer.id}) == "Atomic original"
        assert connection.scalar(
            text("SELECT title FROM search_documents WHERE entity_id=:id"), {"id": timer.id}
        ) == "Atomic original"
    assert _fts_ids(app_config.database.path, '"original"') == [timer.id]
    assert _fts_ids(app_config.database.path, '"changed"') == []


def test_uncommitted_update_and_soft_delete_are_isolated(application, app_config) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Isolation oldterm", planned_duration_ms=60_000)
    )
    note = application.notes.create_note(
        CreateNote(title="Isolation body", content="committed oldbody")
    )
    writer = sqlite3.connect(app_config.database.path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE timers SET name='Isolation newterm' WHERE id=?", (timer.id,))
        assert [item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="oldterm")
        )] == [timer.id]
        assert application.search.search_full_text(FullTextSearchQuery(text="newterm")) == ()
        writer.commit()
        assert [item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="newterm")
        )] == [timer.id]
        assert application.search.search_full_text(FullTextSearchQuery(text="oldterm")) == ()

        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE notes SET content='uncommitted newbody' WHERE id=?", (note.id,)
        )
        assert [
            item.entity_id
            for item in application.search.search_full_text(
                FullTextSearchQuery(text="oldbody")
            )
        ] == [note.id]
        assert application.search.search_full_text(
            FullTextSearchQuery(text="newbody")
        ) == ()
        writer.commit()
        assert [
            item.entity_id
            for item in application.search.search_full_text(
                FullTextSearchQuery(text="newbody")
            )
        ] == [note.id]
        assert application.search.search_full_text(
            FullTextSearchQuery(text="oldbody")
        ) == ()

        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE timers SET deleted_at_utc=1 WHERE id=?", (timer.id,))
        assert [item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="newterm")
        )] == [timer.id]
        writer.commit()
        assert application.search.search_full_text(FullTextSearchQuery(text="newterm")) == ()
        assert [item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="newterm", include_deleted=True)
        )] == [timer.id]

        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE timers SET deleted_at_utc=NULL WHERE id=?", (timer.id,))
        assert application.search.search_full_text(FullTextSearchQuery(text="newterm")) == ()
        writer.commit()
        assert [item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="newterm")
        )] == [timer.id]
    finally:
        writer.close()


def _insert_timer(connection, timer_id: str, name: str, updated: int = 1) -> None:
    connection.execute(text(
        "INSERT INTO timers(id,name,preset_id,task_id,state,planned_duration_ms,"
        "elapsed_before_run_ms,run_started_at_utc,deadline_at_utc,first_started_at_utc,"
        "ended_at_utc,created_at_utc,updated_at_utc,version,deleted_at_utc) "
        "VALUES(:id,:name,NULL,NULL,'READY',60000,0,NULL,NULL,NULL,NULL,1,:updated,1,NULL)"
    ), {"id": timer_id, "name": name, "updated": updated})


def test_full_text_ranking_tiers_entity_priority_and_id_tie_break(application) -> None:
    with application.engine.begin() as connection:
        _insert_timer(connection, "00000000-0000-0000-0000-000000000001", "alpha")
        _insert_timer(connection, "00000000-0000-0000-0000-000000000002", "alpha prefix")
        _insert_timer(connection, "00000000-0000-0000-0000-000000000003", "before alpha after")
        _insert_timer(connection, "00000000-0000-0000-0000-000000000005", "tied alpha")
        _insert_timer(connection, "00000000-0000-0000-0000-000000000004", "tied alpha")
        connection.execute(text(
            "INSERT INTO tasks(id,title,description,status,priority,folder_id,start_at_utc,due_at_utc,"
            "completed_at_utc,cancelled_at_utc,created_at_utc,updated_at_utc,version,deleted_at_utc) "
            "VALUES('10000000-0000-0000-0000-000000000001','unrelated','alpha body','TODO','NORMAL',"
            "NULL,NULL,NULL,NULL,NULL,1,1,1,NULL)"
        ))
    ordered = [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text="alpha", mode="TERMS")
    )]
    assert ordered[:2] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert ordered[-1] == "10000000-0000-0000-0000-000000000001"
    assert ordered.index("00000000-0000-0000-0000-000000000004") < ordered.index(
        "00000000-0000-0000-0000-000000000005"
    )


def test_full_text_pagination_is_repeatable_and_disjoint(application) -> None:
    with application.engine.begin() as connection:
        for number in range(12):
            _insert_timer(
                connection,
                f"20000000-0000-0000-0000-{number:012d}",
                "stable pagination",
            )

    def page(offset: int) -> tuple[str, ...]:
        return tuple(item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="stable pagination", mode="TERMS", limit=4, offset=offset)
        ))

    pages = (page(0), page(4), page(8))
    repeats = (page(0), page(4), page(8))
    assert pages == repeats
    assert not (set(pages[0]) & set(pages[1]) or set(pages[0]) & set(pages[2]) or set(pages[1]) & set(pages[2]))
    assert tuple(item for values in pages for item in values) == tuple(
        f"20000000-0000-0000-0000-{number:012d}" for number in range(12)
    )


def test_bm25_then_updated_at_then_id_are_deterministic(application) -> None:
    with application.engine.begin() as connection:
        for task_id, body in (
            ("30000000-0000-0000-0000-000000000001", "alpha"),
            ("30000000-0000-0000-0000-000000000002", "alpha alpha alpha alpha"),
        ):
            connection.execute(text(
                "INSERT INTO tasks(id,title,description,status,priority,folder_id,start_at_utc,due_at_utc,"
                "completed_at_utc,cancelled_at_utc,created_at_utc,updated_at_utc,version,deleted_at_utc) "
                "VALUES(:id,'bm25 body',:body,'TODO','NORMAL',NULL,NULL,NULL,NULL,NULL,1,1,1,NULL)"
            ), {"id": task_id, "body": body})
        _insert_timer(connection, "30000000-0000-0000-0000-000000000003", "middle alpha", 10)
        _insert_timer(connection, "30000000-0000-0000-0000-000000000004", "middle alpha", 20)
        _insert_timer(connection, "30000000-0000-0000-0000-000000000006", "same alpha", 30)
        _insert_timer(connection, "30000000-0000-0000-0000-000000000005", "same alpha", 30)
    results = application.search.search_full_text(FullTextSearchQuery(text="alpha", mode="TERMS"))
    ids = [item.entity_id for item in results]
    assert ids.index("30000000-0000-0000-0000-000000000002") < ids.index(
        "30000000-0000-0000-0000-000000000001"
    )
    assert ids.index("30000000-0000-0000-0000-000000000004") < ids.index(
        "30000000-0000-0000-0000-000000000003"
    )
    assert ids.index("30000000-0000-0000-0000-000000000005") < ids.index(
        "30000000-0000-0000-0000-000000000006"
    )


def test_entity_priority_is_final_cross_type_tie_break(application) -> None:
    common = "priority tie"
    task = application.tasks.create_task(CreateTask(title=common))
    note = application.notes.create_note(CreateNote(title=common, content=""))
    reminder = application.reminders.create_reminder(CreateReminder(
        title=common, due_at_utc=application._clock.now, scheduled_timezone="UTC"
    ))
    timer = application.timers.create_timer(CreateTimer(name=common, planned_duration_ms=60_000))
    folder = application.folders.create_folder(CreateFolder(name=common))
    tag = application.tags.create_tag(CreateTag(name=common))
    preset = application.timers.create_preset(CreateTimerPreset(name=common, duration_ms=60_000))
    expected = [task.id, note.id, reminder.id, timer.id, folder.id, tag.id, preset.id]
    actual = [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text=common, mode="PHRASE")
    )]
    assert actual == expected


def test_deleted_and_entity_filtered_pagination_remains_stable(application) -> None:
    with application.engine.begin() as connection:
        for number in range(8):
            timer_id = f"40000000-0000-0000-0000-{number:012d}"
            _insert_timer(connection, timer_id, "filtered stable")
            if number % 2:
                connection.execute(
                    text("UPDATE timers SET deleted_at_utc=2 WHERE id=:id"), {"id": timer_id}
                )
    active = application.search.search_full_text(FullTextSearchQuery(
        text="filtered stable", mode="TERMS", entity_types=("TIMER",), limit=3
    ))
    active_page_2 = application.search.search_full_text(FullTextSearchQuery(
        text="filtered stable", mode="TERMS", entity_types=("TIMER", "TIMER"), limit=3, offset=3
    ))
    included = application.search.search_full_text(FullTextSearchQuery(
        text="filtered stable", mode="TERMS", entity_types=("TIMER",), include_deleted=True,
        limit=8,
    ))
    assert [item.entity_id for item in active + active_page_2] == [
        f"40000000-0000-0000-0000-{number:012d}" for number in range(0, 8, 2)
    ]
    assert [item.entity_id for item in included] == [
        f"40000000-0000-0000-0000-{number:012d}" for number in range(8)
    ]
    assert all(item.entity_type == "TIMER" for item in included)


def test_verify_detects_document_projection_and_trigger_corruption(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Verification target", planned_duration_ms=60_000)
    )
    with application.unit_of_work_factory() as uow:
        assert uow.search is not None
        uow.search.verify_full_text_index()
    with application.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM search_documents WHERE entity_type='TIMER' AND entity_id=:id"),
            {"id": timer.id},
        )
    with application.unit_of_work_factory() as uow:
        assert uow.search is not None
        with pytest.raises(DatabaseError, match="projection"):
            uow.search.verify_full_text_index()
    application.search.rebuild_full_text_index()
    with application.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_fts_timers_au"))
    with application.unit_of_work_factory() as uow:
        assert uow.search is not None
        with pytest.raises(DatabaseError, match="triggers"):
            uow.search.verify_full_text_index()


def test_rebuild_failure_rolls_back_document_and_index_changes(
    application, app_config, monkeypatch
) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Rebuild rollback source", planned_duration_ms=60_000)
    )
    with application.engine.begin() as connection:
        connection.execute(
            text("UPDATE search_documents SET title='Committed stale index' "
                 "WHERE entity_type='TIMER' AND entity_id=:id"),
            {"id": timer.id},
        )
    original = SearchRepository.rebuild_full_text_index

    def fail_after_rebuild(self) -> None:
        original(self)
        raise RuntimeError("forced rebuild rollback")

    monkeypatch.setattr(SearchRepository, "rebuild_full_text_index", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="forced rebuild rollback"):
        application.search.rebuild_full_text_index()
    with application.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT title FROM search_documents WHERE entity_type='TIMER' AND entity_id=:id"),
            {"id": timer.id},
        ) == "Committed stale index"
    assert _fts_ids(app_config.database.path, '"stale"') == [timer.id]
    assert _fts_ids(app_config.database.path, '"source"') == []


def test_rebuild_and_search_are_serial_snapshot_safe(application) -> None:
    timers = [
        application.timers.create_timer(
            CreateTimer(name=f"Concurrent rebuild {number}", planned_duration_ms=60_000)
        )
        for number in range(6)
    ]
    barrier = Barrier(2)

    def rebuild() -> None:
        barrier.wait()
        application.search.rebuild_full_text_index()

    def search() -> tuple[str, ...]:
        barrier.wait()
        return tuple(item.entity_id for item in application.search.search_full_text(
            FullTextSearchQuery(text="concurrent rebuild", mode="TERMS")
        ))

    with ThreadPoolExecutor(max_workers=2) as executor:
        rebuild_future = executor.submit(rebuild)
        search_future = executor.submit(search)
        rebuild_future.result()
        observed = search_future.result()
    assert set(observed) == {timer.id for timer in timers}


def _damage_fts_entry(application, timer_id: str, *, replacement: str | None = None) -> None:
    with application.engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT rowid,title,body FROM search_documents "
                "WHERE entity_type='TIMER' AND entity_id=:id"
            ),
            {"id": timer_id},
        ).one()
        connection.execute(
            text(
                "INSERT INTO unified_search_fts(unified_search_fts,rowid,title,body) "
                "VALUES('delete',:rowid,:title,:body)"
            ),
            {"rowid": row.rowid, "title": row.title, "body": row.body},
        )
        if replacement is not None:
            connection.execute(
                text(
                    "INSERT INTO unified_search_fts(rowid,title,body) "
                    "VALUES(:rowid,:title,:body)"
                ),
                {"rowid": row.rowid, "title": replacement, "body": row.body},
            )


def test_rebuild_repairs_missing_fts_entry_and_restores_canonical_triggers(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Missing index sentinel", planned_duration_ms=60_000)
    )
    _damage_fts_entry(application, timer.id)
    assert application.search.search_full_text(FullTextSearchQuery(text="missing")) == ()

    application.search.rebuild_full_text_index()

    assert [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text="missing")
    )] == [timer.id]
    with application.unit_of_work_factory() as uow:
        assert uow.search is not None
        uow.search.verify_full_text_index()
    with application.engine.connect() as connection:
        actual = {
            str(name): normalize_fts_sql(str(sql))
            for name, sql in connection.execute(
                text("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_fts_%'")
            )
        }
    assert actual == EXPECTED_NORMALIZED_FTS_TRIGGER_SQL

    updated = application.timers.update_timer(
        timer.id, UpdateTimer(expected_version=timer.version, name="Synchronization restored")
    )
    assert [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text="synchronization")
    )] == [updated.id]
    assert application.search.search_full_text(FullTextSearchQuery(text="missing")) == ()


def test_rebuild_repairs_fts_content_mismatch_without_document_drift(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Authoritative index text", planned_duration_ms=60_000)
    )
    _damage_fts_entry(application, timer.id, replacement="Corrupted replacement text")
    assert application.search.search_full_text(FullTextSearchQuery(text="authoritative")) == ()
    assert [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text="corrupted")
    )] == [timer.id]

    application.search.rebuild_full_text_index()

    assert [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text="authoritative")
    )] == [timer.id]
    assert application.search.search_full_text(FullTextSearchQuery(text="corrupted")) == ()


def test_rebuild_failure_rolls_back_trigger_suppression(application, monkeypatch) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Rollback trigger sentinel", planned_duration_ms=60_000)
    )

    def fail_while_suppressed(self) -> None:
        names = set(self._session.scalars(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE 'trg_fts_documents_%'"
        )))
        assert names == set()
        raise RuntimeError("injected reconstruction failure")

    monkeypatch.setattr(SearchRepository, "_reconstruct_search_documents", fail_while_suppressed)
    with pytest.raises(RuntimeError, match="injected reconstruction failure"):
        application.search.rebuild_full_text_index()

    with application.engine.connect() as connection:
        actual = {
            str(name): normalize_fts_sql(str(sql))
            for name, sql in connection.execute(
                text("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_fts_%'")
            )
        }
    assert actual == EXPECTED_NORMALIZED_FTS_TRIGGER_SQL
    assert [item.entity_id for item in application.search.search_full_text(
        FullTextSearchQuery(text="rollback")
    )] == [timer.id]


@pytest.mark.parametrize(
    "replacement",
    [
        "CREATE TRIGGER trg_fts_tasks_au AFTER UPDATE ON tasks BEGIN SELECT 1; END",
        (
            "CREATE TRIGGER trg_fts_tasks_au AFTER UPDATE OF title, description ON tasks BEGIN "
            "UPDATE search_documents SET title=NEW.title, body=NEW.title "
            "WHERE entity_type='TASK' AND entity_id=NEW.id; END"
        ),
        (
            "CREATE TRIGGER trg_fts_tasks_au AFTER UPDATE OF title, description ON tasks BEGIN "
            "UPDATE search_documents SET title=NEW.title, body=NEW.description "
            "WHERE entity_type='task' AND entity_id=NEW.id; END"
        ),
    ],
    ids=("no-op", "wrong-projection", "changed-literal-case"),
)
def test_runtime_verify_rejects_same_name_altered_trigger(application, replacement) -> None:
    with application.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_fts_tasks_au"))
        connection.execute(text(replacement))
    with application.unit_of_work_factory() as uow:
        assert uow.search is not None
        with pytest.raises(DatabaseError, match="triggers"):
            uow.search.verify_full_text_index()


def test_runtime_verify_accepts_formatting_only_trigger_variation(application) -> None:
    name = "trg_fts_documents_ai"
    formatted = DOCUMENT_FTS_TRIGGER_SQL[name].replace(" ", "\n  ")
    with application.engine.begin() as connection:
        connection.execute(text(f"DROP TRIGGER {name}"))
        connection.execute(text(formatted))
    with application.unit_of_work_factory() as uow:
        assert uow.search is not None
        uow.search.verify_full_text_index()

