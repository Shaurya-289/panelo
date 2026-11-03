from __future__ import annotations

import pytest

from productivity_app.bootstrap import Application
from productivity_app.core.exceptions import (
    ApplicationLifecycleError,
    BackupRestoreError,
)
from productivity_app.repositories.search import SearchRepository
from productivity_app.schemas.backups import RestoreBackup
from productivity_app.schemas.search import FullTextSearchQuery
from productivity_app.schemas.tasks import CreateTask, UpdateTask
from productivity_app.services.search import SearchService


def test_restore_replaces_runtime_and_retains_pre_restore_backup(application) -> None:
    original = application.tasks.create_task(CreateTask(title="Original"))
    backup = application.backups.create_backup()
    application.tasks.update_task(original.id, UpdateTask(expected_version=original.version, title="Mutated"))
    old_tasks = application.tasks

    restored = application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))

    assert application.tasks.get_task(original.id).title == "Original"
    assert restored.safety_backup.kind == "PRE_RESTORE"
    with pytest.raises(ApplicationLifecycleError):
        old_tasks.get_task(original.id)


def test_restore_rejects_active_uow(application) -> None:
    backup = application.backups.create_backup()
    with application.unit_of_work_factory(), pytest.raises(ApplicationLifecycleError):
        application.restore_backup(RestoreBackup(source_path=application.backups.backup_directory / backup.filename))


def test_restore_rebuilds_and_verifies_before_publish_and_is_immediately_searchable(
    application, monkeypatch
) -> None:
    original = application.tasks.create_task(CreateTask(title="Restored searchable sentinel"))
    backup = application.backups.create_backup()
    application.tasks.update_task(
        original.id,
        UpdateTask(expected_version=original.version, title="Mutated after backup"),
    )
    order: list[str] = []
    original_rebuild = SearchService.rebuild_full_text_index
    original_verify = SearchRepository.verify_full_text_index
    original_install = Application._install

    def tracked_rebuild(self) -> None:
        order.append("rebuild")
        original_rebuild(self)

    def tracked_verify(self) -> None:
        order.append("verify")
        original_verify(self)

    def tracked_install(self, other) -> None:
        original_install(self, other)
        order.append("installed")

    monkeypatch.setattr(SearchService, "rebuild_full_text_index", tracked_rebuild)
    monkeypatch.setattr(SearchRepository, "verify_full_text_index", tracked_verify)
    monkeypatch.setattr(Application, "_install", tracked_install)

    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )

    assert order[-3:] == ["rebuild", "verify", "installed"]
    results = application.search.search_full_text(
        FullTextSearchQuery(text="searchable sentinel")
    )
    assert [item.entity_id for item in results] == [original.id]


@pytest.mark.parametrize("failure_point", ["rebuild", "verify"])
def test_restore_search_preparation_failure_never_publishes_candidate_runtime(
    application, monkeypatch, failure_point
) -> None:
    original = application.tasks.create_task(CreateTask(title="Safety original"))
    backup = application.backups.create_backup()
    mutated = application.tasks.update_task(
        original.id,
        UpdateTask(expected_version=original.version, title="Safety current"),
    )
    old_tasks = application.tasks
    calls = 0
    original_rebuild = SearchService.rebuild_full_text_index
    original_verify = SearchRepository.verify_full_text_index

    if failure_point == "rebuild":

        def fail_once(self) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("injected restored-index rebuild failure")
            original_rebuild(self)

        monkeypatch.setattr(SearchService, "rebuild_full_text_index", fail_once)
    else:

        def fail_once_verify(self) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("injected restored-index verification failure")
            original_verify(self)

        monkeypatch.setattr(SearchRepository, "verify_full_text_index", fail_once_verify)

    with pytest.raises(BackupRestoreError, match="original database was recovered"):
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
        )

    assert calls >= 2
    assert application.tasks.get_task(original.id).title == mutated.title
    assert application.search.search_full_text(
        FullTextSearchQuery(text="safety current")
    )[0].entity_id == original.id
    with pytest.raises(ApplicationLifecycleError):
        old_tasks.get_task(original.id)
