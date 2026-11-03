from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from shutil import copy2
from threading import Barrier, Event

import pytest

from productivity_app.core.exceptions import BackupValidationError, NotFoundError, ValidationError
from productivity_app.db.fts_contract import EXPECTED_FTS_TRIGGER_SQL
from productivity_app.schemas.backups import BackupPageQuery, DeleteBackup, ValidateBackup
from productivity_app.schemas.tasks import CreateTask, UpdateTask


def test_native_backup_is_standalone_validated_and_managed(application) -> None:
    application.tasks.create_task(CreateTask(title="Backed up"))
    backup = application.backups.create_backup()
    path = application.backups.backup_directory / backup.filename

    assert path.exists()
    assert backup.integrity_status == "OK"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert connection.execute("SELECT title FROM tasks").fetchone()[0] == "Backed up"
    listed = application.backups.list_backups(BackupPageQuery())
    assert [(item.filename, item.kind, item.size_bytes) for item in listed] == [
        (backup.filename, "MANUAL", backup.size_bytes)
    ]
    application.backups.delete_backup(DeleteBackup(filename=backup.filename))
    with pytest.raises(NotFoundError):
        application.backups.delete_backup(DeleteBackup(filename=backup.filename))
    with pytest.raises(ValidationError):
        application.backups.delete_backup(DeleteBackup(filename="../outside.sqlite3"))


def test_backup_validation_rejects_non_application_sqlite(application, tmp_path) -> None:
    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=unrelated))


def _mutated_backup(application, tmp_path, mutation):
    backup = application.backups.create_backup()
    source = application.backups.backup_directory / backup.filename
    candidate = tmp_path / f"candidate-{backup.filename}"
    copy2(source, candidate)
    with sqlite3.connect(candidate) as connection:
        mutation(connection)
    return candidate


@pytest.mark.parametrize(
    "mutation",
    [
        lambda connection: connection.execute("DROP TABLE focus_sequences"),
        lambda connection: connection.execute("DROP TABLE focus_sessions"),
        lambda connection: connection.execute("DROP TABLE search_documents"),
        lambda connection: connection.execute("DROP TABLE unified_search_fts"),
        lambda connection: connection.execute("DROP TRIGGER trg_fts_tasks_ai"),
        lambda connection: connection.execute("DROP TRIGGER trg_fts_documents_au"),
        lambda connection: connection.execute("DROP INDEX ux_focus_sequences_one_active"),
        lambda connection: connection.execute("CREATE TABLE unexpected_phase7_object(id)"),
        lambda connection: connection.execute(
            "CREATE VIRTUAL TABLE unexpected_phase7_virtual USING fts5(value)"
        ),
        lambda connection: connection.execute(
            "CREATE TRIGGER unexpected_phase7_trigger AFTER UPDATE ON tasks BEGIN SELECT 1; END"
        ),
    ],
    ids=[
        "focus-sequences",
        "focus-sessions",
        "search-documents",
        "fts-table",
        "source-trigger",
        "document-trigger",
        "focus-index",
        "unexpected-table",
        "unexpected-virtual",
        "unexpected-trigger",
    ],
)
def test_backup_validation_rejects_missing_or_unexpected_phase7_objects(
    application, tmp_path, mutation
) -> None:
    candidate = _mutated_backup(application, tmp_path, mutation)

    with pytest.raises(BackupValidationError):
        application.backups.validate_backup(ValidateBackup(source_path=candidate))


def test_backup_validation_rejects_semantically_altered_fts_sql(
    application, tmp_path
) -> None:
    def alter_fts_sql(connection) -> None:
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=replace(sql, "
            "'unicode61 remove_diacritics 2', 'unicode61 remove_diacritics 1') "
            "WHERE name='unified_search_fts'"
        )
        connection.execute("PRAGMA writable_schema=OFF")

    candidate = _mutated_backup(application, tmp_path, alter_fts_sql)

    with pytest.raises(BackupValidationError, match="definition"):
        application.backups.validate_backup(ValidateBackup(source_path=candidate))


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
def test_backup_validation_rejects_same_name_altered_trigger(
    application, tmp_path, replacement
) -> None:
    def replace_trigger(connection) -> None:
        connection.execute("DROP TRIGGER trg_fts_tasks_au")
        connection.execute(replacement)

    candidate = _mutated_backup(application, tmp_path, replace_trigger)
    with pytest.raises(BackupValidationError, match="triggers"):
        application.backups.validate_backup(ValidateBackup(source_path=candidate))


def test_backup_validation_accepts_canonical_trigger_contract(application, tmp_path) -> None:
    def recreate_trigger(connection) -> None:
        connection.execute("DROP TRIGGER trg_fts_tasks_au")
        connection.execute(EXPECTED_FTS_TRIGGER_SQL["trg_fts_tasks_au"].replace(" ", "\n  "))

    candidate = _mutated_backup(application, tmp_path, recreate_trigger)
    validated = application.backups.validate_backup(ValidateBackup(source_path=candidate))
    assert validated.integrity_status == "OK"


def test_backup_concurrent_with_indexed_update_is_one_consistent_snapshot(
    application, monkeypatch
) -> None:
    task = application.tasks.create_task(CreateTask(title="Snapshot old"))
    barrier = Barrier(2)
    original_copy = application.backups._copy_sqlite

    def synchronized_copy(source, destination) -> None:
        barrier.wait()
        original_copy(source, destination)

    monkeypatch.setattr(application.backups, "_copy_sqlite", synchronized_copy)

    def update_task() -> None:
        barrier.wait()
        application.tasks.update_task(
            task.id,
            UpdateTask(expected_version=task.version, title="Snapshot new"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        backup_future = pool.submit(application.backups.create_backup)
        update_future = pool.submit(update_task)
        backup = backup_future.result(timeout=20)
        update_future.result(timeout=20)

    artifact = application.backups.backup_directory / backup.filename
    application.backups.validate_backup(ValidateBackup(source_path=artifact))
    with sqlite3.connect(artifact) as connection:
        source_title = connection.execute(
            "SELECT title FROM tasks WHERE id=?", (task.id,)
        ).fetchone()[0]
        document_title = connection.execute(
            "SELECT title FROM search_documents WHERE entity_type='TASK' AND entity_id=?",
            (task.id,),
        ).fetchone()[0]
        old_match = connection.execute(
            "SELECT count(*) FROM unified_search_fts WHERE unified_search_fts MATCH ?",
            ('"old"',),
        ).fetchone()[0]
        new_match = connection.execute(
            "SELECT count(*) FROM unified_search_fts WHERE unified_search_fts MATCH ?",
            ('"new"',),
        ).fetchone()[0]

    assert source_title == document_title
    assert (source_title, old_match, new_match) in {
        ("Snapshot old", 1, 0),
        ("Snapshot new", 0, 1),
    }


def test_backup_during_uncommitted_multi_entity_indexed_transaction_is_atomic(
    application, app_config
) -> None:
    first = application.tasks.create_task(CreateTask(title="Atomic first old"))
    second = application.tasks.create_task(CreateTask(title="Atomic second old"))
    updates_ready = Event()
    backup_finished = Event()

    def update_both() -> None:
        with sqlite3.connect(app_config.database.path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE tasks SET title='Atomic first new' WHERE id=?", (first.id,)
            )
            connection.execute(
                "UPDATE tasks SET title='Atomic second new' WHERE id=?", (second.id,)
            )
            updates_ready.set()
            assert backup_finished.wait(timeout=20)
            connection.commit()

    def take_backup():
        assert updates_ready.wait(timeout=20)
        try:
            return application.backups.create_backup()
        finally:
            backup_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(update_both)
        backup = pool.submit(take_backup).result(timeout=30)
        writer.result(timeout=30)

    artifact = application.backups.backup_directory / backup.filename
    application.backups.validate_backup(ValidateBackup(source_path=artifact))
    with sqlite3.connect(artifact) as connection:
        source_titles = tuple(
            row[0]
            for row in connection.execute(
                "SELECT title FROM tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(sorted((first.id, second.id))),
            )
        )
        document_titles = tuple(
            row[0]
            for row in connection.execute(
                "SELECT title FROM search_documents "
                "WHERE entity_type='TASK' AND entity_id IN (?, ?) ORDER BY entity_id",
                tuple(sorted((first.id, second.id))),
            )
        )
        new_matches = connection.execute(
            "SELECT count(*) FROM unified_search_fts WHERE unified_search_fts MATCH ?",
            ('"new"',),
        ).fetchone()[0]

    assert source_titles == document_titles
    assert set(source_titles) == {"Atomic first old", "Atomic second old"}
    assert new_matches == 0
