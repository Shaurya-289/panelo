"""Focused regression proofs for the Phase 5 lifecycle and backup hardening."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

import productivity_app.bootstrap as bootstrap_module
import productivity_app.db.unit_of_work as uow_module
from productivity_app.core.exceptions import (
    ApplicationLifecycleError,
    BackupError,
    BackupRestoreError,
    BackupValidationError,
)
from productivity_app.schemas.backups import DeleteBackup, RestoreBackup
from productivity_app.schemas.tasks import CreateTask


def test_captured_repository_and_session_are_unusable_after_uow_exit(application) -> None:
    task = application.tasks.create_task(CreateTask(title="Captured"))
    with application.unit_of_work_factory() as uow:
        assert uow.tasks is not None
        assert uow.session is not None
        repository, session = uow.tasks, uow.session

    with pytest.raises(ApplicationLifecycleError):
        repository.get(task.id)
    with pytest.raises(ApplicationLifecycleError):
        session.execute(text("SELECT 1"))


def test_restore_failure_after_dispose_reopens_a_fresh_original_runtime(application, monkeypatch) -> None:
    task = application.tasks.create_task(CreateTask(title="Original survives"))
    backup = application.backups.create_backup()

    def fail_replace(_source, _destination) -> None:  # type: ignore[no-untyped-def]
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(bootstrap_module.os, "replace", fail_replace)
    with pytest.raises(BackupRestoreError):
        application.restore_backup(
            RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
        )

    # The original engine was disposed, so this proves a new, usable runtime
    # was installed rather than merely reopening old service objects.
    assert application.tasks.get_task(task.id).title == "Original survives"


def test_backup_publication_retries_filename_collision_without_overwrite(application, monkeypatch) -> None:
    real_link = os.link
    attempts = 0

    def collide_once(source, destination, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FileExistsError("simulated collision")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr("productivity_app.services.backups.os.link", collide_once)
    created = application.backups.create_backup()

    assert attempts == 2
    assert (application.backups.backup_directory / created.filename).is_file()


def test_restore_revalidates_source_identity_immediately_before_copy(application, monkeypatch) -> None:
    task = application.tasks.create_task(CreateTask(title="Live state"))
    backup = application.backups.create_backup()
    source = application.backups.backup_directory / backup.filename
    stage_source = application.backups.stage_approved_restore_source
    original_stat = source.stat()

    def replace_before_staging(original, validated, destination):  # type: ignore[no-untyped-def]
        # Preserve the size and recorded mtime so the staged-content digest,
        # rather than only metadata, must reject the replacement.
        original.write_bytes(b"x" * original_stat.st_size)
        os.utime(original, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return stage_source(original, validated, destination)

    monkeypatch.setattr(application.backups, "stage_approved_restore_source", replace_before_staging)
    with pytest.raises(BackupValidationError):
        application.restore_backup(RestoreBackup(source_path=source))

    assert application.tasks.get_task(task.id).title == "Live state"


def test_staged_restore_source_is_bound_before_safety_backup(application, monkeypatch) -> None:
    original = application.tasks.create_task(CreateTask(title="Approved"))
    backup = application.backups.create_backup()
    source = application.backups.backup_directory / backup.filename
    safety_backup = application.backups._create_backup

    def replace_source_after_staging(kind: str):
        source.write_bytes(b"unapproved replacement after staging")
        return safety_backup(kind)

    monkeypatch.setattr(application.backups, "_create_backup", replace_source_after_staging)
    restored = application.restore_backup(RestoreBackup(source_path=source))

    assert restored.source_sha256 == backup.sha256
    assert application.tasks.get_task(original.id).title == "Approved"


def test_backup_source_stat_and_open_errors_are_backup_errors(application, monkeypatch) -> None:
    backup = application.backups.create_backup()
    source = application.backups.backup_directory / backup.filename
    original_lstat = type(source).lstat

    def lstat_permission_failure(self):  # type: ignore[no-untyped-def]
        if self == source:
            raise PermissionError("denied")
        return original_lstat(self)

    with monkeypatch.context() as context:
        context.setattr(type(source), "lstat", lstat_permission_failure)
        with pytest.raises(BackupError) as stat_error:
            application.backups.validate_backup(type("Command", (), {"source_path": source})())
    assert isinstance(stat_error.value.__cause__, PermissionError)

    def lstat_os_failure(self):  # type: ignore[no-untyped-def]
        if self == source:
            raise OSError("drive unavailable")
        return original_lstat(self)

    with monkeypatch.context() as context:
        context.setattr(type(source), "lstat", lstat_os_failure)
        with pytest.raises(BackupError) as generic_stat_error:
            application.backups.validate_backup(type("Command", (), {"source_path": source})())
    assert isinstance(generic_stat_error.value.__cause__, OSError)

    def regular_file_failure(_path):  # type: ignore[no-untyped-def]
        raise PermissionError("denied")

    with monkeypatch.context() as context:
        context.setattr(application.backups, "_is_regular_file", regular_file_failure)
        with pytest.raises(BackupError) as regular_file_error:
            application.backups.validate_backup(type("Command", (), {"source_path": source})())
    assert isinstance(regular_file_error.value.__cause__, PermissionError)

    def open_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == source:
            raise PermissionError("denied")
        return original_open(self, *args, **kwargs)

    original_open = type(source).open
    with monkeypatch.context() as context:
        context.setattr(type(source), "open", open_failure)
        with pytest.raises(BackupError) as open_error:
            application.backups.validate_backup(type("Command", (), {"source_path": source})())
    assert isinstance(open_error.value.__cause__, PermissionError)

    def generic_open_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == source:
            raise OSError("device unavailable")
        return original_open(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(type(source), "open", generic_open_failure)
        with pytest.raises(BackupError) as generic_open_error:
            application.backups.validate_backup(type("Command", (), {"source_path": source})())
    assert isinstance(generic_open_error.value.__cause__, OSError)


def test_managed_backup_filesystem_failures_are_operational_errors(application, monkeypatch) -> None:
    """Public file operations must not turn permissions/I/O into validation failures."""
    backup_directory = application.backups.backup_directory
    original_mkdir = type(backup_directory).mkdir

    def denied_mkdir(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == backup_directory:
            raise PermissionError("denied")
        return original_mkdir(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(type(backup_directory), "mkdir", denied_mkdir)
        with pytest.raises(BackupError) as create_error:
            application.backups.create_backup()
    assert isinstance(create_error.value.__cause__, PermissionError)

    created = application.backups.create_backup()
    original_iterdir = type(backup_directory).iterdir

    def denied_iterdir(self):  # type: ignore[no-untyped-def]
        if self == backup_directory:
            raise PermissionError("denied")
        return original_iterdir(self)

    with monkeypatch.context() as context:
        context.setattr(type(backup_directory), "iterdir", denied_iterdir)
        with pytest.raises(BackupError) as list_error:
            application.backups.list_backups()
    assert isinstance(list_error.value.__cause__, PermissionError)

    target = backup_directory / created.filename
    original_unlink = type(target).unlink

    def denied_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == target:
            raise PermissionError("denied")
        return original_unlink(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(type(target), "unlink", denied_unlink)
        with pytest.raises(BackupError) as delete_error:
            application.backups.delete_backup(DeleteBackup(filename=created.filename))
    assert isinstance(delete_error.value.__cause__, PermissionError)


def test_backup_staging_fsync_and_copy_failures_are_operational_errors(application, monkeypatch) -> None:
    def copy_failure(_source, _destination):  # type: ignore[no-untyped-def]
        raise OSError("temporary destination unavailable")

    with monkeypatch.context() as context:
        context.setattr(application.backups, "_copy_sqlite", copy_failure)
        with pytest.raises(BackupError) as copy_error:
            application.backups.create_backup()
    assert isinstance(copy_error.value.__cause__, OSError)

    def fsync_failure(_descriptor):  # type: ignore[no-untyped-def]
        raise OSError("flush failed")

    with monkeypatch.context() as context:
        context.setattr("productivity_app.services.backups.os.fsync", fsync_failure)
        with pytest.raises(BackupError) as fsync_error:
            application.backups.create_backup()
    assert isinstance(fsync_error.value.__cause__, OSError)

    backup = application.backups.create_backup()
    source = application.backups.backup_directory / backup.filename
    approved = application.backups._validate_path(source)
    destination = source.parent / ".destination-open-failure.partial"
    original_open = type(source).open

    def destination_open_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == destination:
            raise OSError("staging unavailable")
        return original_open(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(type(source), "open", destination_open_failure)
        with pytest.raises(BackupError) as destination_error:
            application.backups.stage_approved_restore_source(source, approved, destination)
    assert isinstance(destination_error.value.__cause__, OSError)


def test_restore_source_disappearance_and_unreadability_leave_live_runtime_usable(
    application, monkeypatch, tmp_path
) -> None:
    task = application.tasks.create_task(CreateTask(title="Live"))
    backup = application.backups.create_backup()
    source = application.backups.backup_directory / backup.filename
    approved = application.backups._validate_path(source)
    destination = tmp_path / "approved-stage.sqlite3"

    source.unlink()
    with pytest.raises(BackupValidationError) as missing:
        application.backups.stage_approved_restore_source(source, approved, destination)
    assert isinstance(missing.value.__cause__, FileNotFoundError)
    assert application.tasks.get_task(task.id).title == "Live"

    replacement = application.backups.create_backup()
    source = application.backups.backup_directory / replacement.filename
    approved = application.backups._validate_path(source)
    destination = tmp_path / "unreadable-stage.sqlite3"
    original_open = type(source).open

    def denied_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == source:
            raise PermissionError("denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(source), "open", denied_open)
    with pytest.raises(BackupError) as unreadable:
        application.backups.stage_approved_restore_source(source, approved, destination)
    assert isinstance(unreadable.value.__cause__, PermissionError)
    assert application.tasks.get_task(task.id).title == "Live"


def test_uow_entry_constructor_failure_releases_lifecycle_lease(application, monkeypatch) -> None:
    class BrokenSettingsRepository:
        def __init__(self, _session: object) -> None:
            raise RuntimeError("simulated repository construction failure")

    monkeypatch.setattr(uow_module, "SettingsRepository", BrokenSettingsRepository)
    with pytest.raises(RuntimeError, match="construction failure"):
        application.unit_of_work_factory().__enter__()

    # A leaked lease would reject this otherwise independent operation.
    assert application.backups.create_backup().integrity_status == "OK"
