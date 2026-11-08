"""Automatic backups: when one is due, pruning, and how failures behave (fake clock throughout)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from panelo.core.exceptions import BackupError
from panelo.core.ids import Uuid4Generator
from panelo.core.lifecycle import ApplicationLifecycleGate
from panelo.schemas.backups import BackupPageQuery, RestoreBackup, ValidateBackup
from panelo.schemas.settings import SettingsPatch
from panelo.schemas.tasks import CreateTask
from panelo.services.backups import BackupService

DAY = timedelta(days=1)


def _configure(application, **fields: object):
    current = application.settings.get_settings()
    return application.settings.update_settings(
        SettingsPatch(expected_version=current.version, **fields)  # type: ignore[arg-type]
    )


def _backups(application):
    return application.backups.list_backups(BackupPageQuery(limit=200))


def _kinds(application) -> list[str]:
    return [item.kind for item in _backups(application)]


def test_the_first_automatic_backup_is_made_and_recorded(application, clock) -> None:
    application.tasks.create_task(CreateTask(title="Protected"))
    assert application.settings.get_settings().last_auto_backup_at_utc is None

    created = application.backups.run_auto_backup_if_due(clock.now)

    assert created is not None and created.kind == "AUTO" and created.integrity_status == "OK"
    path = application.backups.backup_directory / created.filename
    assert path.exists() and path.name.endswith("-auto.sqlite3")
    assert created.created_at_utc == clock.now
    assert application.settings.get_settings().last_auto_backup_at_utc == clock.now
    assert [(item.filename, item.kind) for item in _backups(application)] == [(created.filename, "AUTO")]
    application.backups.validate_backup(ValidateBackup(source_path=path))


def test_nothing_is_made_until_more_than_24_hours_have_passed(application, clock) -> None:
    first = application.backups.run_auto_backup_if_due(clock.now)
    assert first is not None
    start = clock.now
    for offset in (timedelta(0), timedelta(hours=23, minutes=59, seconds=59), DAY):
        assert application.backups.run_auto_backup_if_due(start + offset) is None
    assert len(_backups(application)) == 1
    assert application.settings.get_settings().last_auto_backup_at_utc == start

    second = application.backups.run_auto_backup_if_due(start + DAY + timedelta(microseconds=1))
    assert second is not None and second.filename != first.filename
    assert len(_backups(application)) == 2
    assert application.settings.get_settings().last_auto_backup_at_utc == start + DAY + timedelta(
        microseconds=1
    )


def test_the_injected_clock_is_the_default_time(application, clock) -> None:
    assert application.backups.run_auto_backup_if_due() is not None
    clock.now += DAY - timedelta(minutes=1)
    assert application.backups.run_auto_backup_if_due() is None
    clock.now += timedelta(minutes=2)
    created = application.backups.run_auto_backup_if_due()
    assert created is not None and created.created_at_utc == clock.now


def test_turning_automatic_backups_off_stops_them_and_on_resumes_them(application, clock) -> None:
    _configure(application, auto_backup=False)
    assert application.backups.run_auto_backup_if_due(clock.now + 100 * DAY) is None
    assert _backups(application) == ()
    assert application.settings.get_settings().last_auto_backup_at_utc is None
    _configure(application, auto_backup=True)
    assert application.backups.run_auto_backup_if_due(clock.now + 100 * DAY) is not None


def test_a_last_backup_time_in_the_future_counts_as_due(application, clock) -> None:
    application.settings.record_auto_backup(clock.now + 5 * DAY)  # the system clock was set back
    assert application.backups.run_auto_backup_if_due(clock.now) is not None
    assert application.settings.get_settings().last_auto_backup_at_utc == clock.now


def test_only_the_newest_keep_backups_remain_after_each_run(application, clock) -> None:
    _configure(application, keep_backups=3)
    created = []
    for _ in range(6):
        application.tasks.create_task(CreateTask(title=f"Day {len(created)}"))
        made = application.backups.run_auto_backup_if_due(clock.now)
        assert made is not None
        created.append(made.filename)
        clock.now += DAY + timedelta(minutes=1)
    assert [item.filename for item in _backups(application)] == created[:2:-1]
    assert sorted(path.name for path in application.backups.backup_directory.iterdir()) == sorted(created[3:])


def test_manual_backups_count_toward_the_same_limit(application, clock) -> None:
    _configure(application, keep_backups=2)
    manual_old = application.backups.create_backup()
    clock.now += timedelta(hours=2)
    manual_new = application.backups.create_backup()
    clock.now += timedelta(hours=2)
    first_auto = application.backups.run_auto_backup_if_due(clock.now)
    assert first_auto is not None
    # two kept overall: the new automatic one and the newest manual one
    assert [item.filename for item in _backups(application)] == [first_auto.filename, manual_new.filename]
    assert manual_old.filename not in {item.filename for item in _backups(application)}

    clock.now += DAY + timedelta(minutes=1)
    second_auto = application.backups.run_auto_backup_if_due(clock.now)
    assert second_auto is not None
    assert [item.filename for item in _backups(application)] == [second_auto.filename, first_auto.filename]
    assert _kinds(application) == ["AUTO", "AUTO"]


def test_safety_backups_from_a_restore_count_too(application, clock) -> None:
    _configure(application, keep_backups=2)
    manual = application.backups.create_backup()
    clock.now += timedelta(hours=1)
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / manual.filename)
    )
    assert sorted(_kinds(application)) == ["MANUAL", "PRE_RESTORE"]
    # the restore replaced the settings with the ones saved in the backup: keep two again
    _configure(application, keep_backups=2)
    clock.now += timedelta(hours=1)
    created = application.backups.run_auto_backup_if_due(clock.now)
    assert created is not None
    assert len(_backups(application)) == 2 and _backups(application)[0].filename == created.filename


def test_the_backup_just_made_is_kept_even_if_the_clock_ran_backwards(application, clock) -> None:
    _configure(application, keep_backups=1)
    future = application.backups.create_backup()
    clock.now -= 10 * DAY
    created = application.backups.run_auto_backup_if_due(clock.now)
    assert created is not None
    assert [item.filename for item in _backups(application)] == [created.filename]
    assert not (application.backups.backup_directory / future.filename).exists()


def test_pruning_leaves_unmanaged_files_alone(application, clock) -> None:
    _configure(application, keep_backups=1)
    directory = application.backups.backup_directory
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "notes.txt").write_text("mine", encoding="utf-8")
    (directory / "panelo-backup-not-a-real-name.sqlite3").write_text("x", encoding="utf-8")
    application.backups.create_backup()
    clock.now += timedelta(hours=1)
    created = application.backups.run_auto_backup_if_due(clock.now)
    assert created is not None
    assert (directory / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert (directory / "panelo-backup-not-a-real-name.sqlite3").exists()
    assert [item.filename for item in _backups(application)] == [created.filename]


def test_a_backup_that_cannot_be_pruned_does_not_fail_the_run(
    application, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(application, keep_backups=1)
    old = application.backups.create_backup()
    original = Path.unlink

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == old.filename:
            raise PermissionError("file is in use")
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", refuse)
    clock.now += timedelta(hours=1)
    created = application.backups.run_auto_backup_if_due(clock.now)
    assert created is not None
    assert application.settings.get_settings().last_auto_backup_at_utc == clock.now
    assert {item.filename for item in _backups(application)} == {old.filename, created.filename}


def test_a_failed_backup_is_not_recorded_so_it_is_retried(
    application, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(self, *_args: object, **_kwargs: object) -> None:
        raise BackupError("disk full")

    monkeypatch.setattr(BackupService, "_copy_sqlite", fail)
    with pytest.raises(BackupError, match="disk full"):
        application.backups.run_auto_backup_if_due(clock.now)
    assert application.settings.get_settings().last_auto_backup_at_utc is None
    assert _backups(application) == ()
    monkeypatch.undo()
    assert application.backups.run_auto_backup_if_due(clock.now) is not None


def test_an_automatic_backup_can_be_restored(application, clock) -> None:
    task = application.tasks.create_task(CreateTask(title="Original"))
    created = application.backups.run_auto_backup_if_due(clock.now)
    assert created is not None
    application.tasks.delete_task(task.id, task.version)
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / created.filename)
    )
    assert application.tasks.get_task(task.id).deleted_at_utc is None


def test_automatic_backups_need_the_settings_service(app_config, clock) -> None:
    bare = BackupService(
        app_config.database.path, 5_000, clock, Uuid4Generator(), ApplicationLifecycleGate()
    )
    with pytest.raises(BackupError):
        bare.run_auto_backup_if_due(datetime(2026, 9, 24, tzinfo=UTC))


def test_backups_made_before_the_rename_are_still_listed(application) -> None:
    created = application.backups.create_backup()
    assert created.filename.startswith("panelo-backup-")
    directory = application.backups.backup_directory
    legacy = created.filename.replace("panelo-backup-", "productivity-backup-", 1)
    (directory / legacy).write_bytes((directory / created.filename).read_bytes())
    assert {item.filename for item in _backups(application)} == {created.filename, legacy}
