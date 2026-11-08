from __future__ import annotations

import logging
from pathlib import Path

import pytest

from panelo.core import legacy_migration
from panelo.core.legacy_migration import (
    MARKER_FILENAME,
    MigrationStatus,
    migrate_legacy_data,
    migrate_legacy_data_safely,
)
from panelo.desktop.startup import build_startup_command, migrate_legacy_startup_entry
from panelo.ui.selftest.harness import FakeStartupRegistry
from panelo.windows.startup import RegistryValue


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def old_dir(tmp_path: Path) -> Path:
    root = tmp_path / "ProductivityApp" / "ProductivityApp"
    (root / "Files" / "Docs").mkdir(parents=True)
    (root / "backups").mkdir()
    (root / "Logs").mkdir()
    (root / "cache").mkdir()
    (root / "ui").mkdir()
    (root / "productivity.db").write_bytes(b"db")
    (root / "productivity.db-wal").write_bytes(b"wal")
    (root / "productivity.db-shm").write_bytes(b"shm")
    (root / "window.ini").write_text("[w]\nx=1\n")
    (root / "Files" / "Docs" / "a.txt").write_text("hello")
    (root / "backups" / "productivity-backup-20260101.db").write_bytes(b"bk")
    (root / "Logs" / "productivity-app.log").write_text("log")
    (root / "Logs" / "productivity-app.log.1").write_text("log1")
    (root / "ui" / "state.json").write_text("{}")
    return root


@pytest.fixture
def new_dir(tmp_path: Path) -> Path:
    return tmp_path / "Panelo" / "Panelo"


def test_copies_everything_and_renames_db_sidecars_and_logs(old_dir: Path, new_dir: Path) -> None:
    before = _snapshot(old_dir)
    result = migrate_legacy_data(old_dir, new_dir)
    assert result.status is MigrationStatus.MIGRATED
    copied = _snapshot(new_dir)
    assert copied["panelo.db"] == b"db"
    assert copied["panelo.db-wal"] == b"wal"
    assert copied["panelo.db-shm"] == b"shm"
    assert copied["window.ini"] == before["window.ini"]
    assert copied["Files/Docs/a.txt"] == b"hello"
    assert copied["backups/productivity-backup-20260101.db"] == b"bk"
    assert copied["Logs/panelo.log"] == b"log"
    assert copied["Logs/panelo.log.1"] == b"log1"
    assert copied["ui/state.json"] == b"{}"
    assert MARKER_FILENAME in copied
    assert not any(name.startswith("productivity.db") for name in copied)
    assert _snapshot(old_dir) == before  # the old folder is untouched
    assert not list(new_dir.parent.glob(".panelo-migration-*"))  # no staging left behind


def test_skips_when_a_panelo_database_already_exists(old_dir: Path, new_dir: Path) -> None:
    new_dir.mkdir(parents=True)
    (new_dir / "panelo.db").write_bytes(b"mine")
    result = migrate_legacy_data(old_dir, new_dir)
    assert result.status is MigrationStatus.ALREADY_PRESENT
    assert _snapshot(new_dir) == {"panelo.db": b"mine"}


def test_skips_without_an_old_database(old_dir: Path, new_dir: Path) -> None:
    (old_dir / "productivity.db").unlink()
    assert migrate_legacy_data(old_dir, new_dir).status is MigrationStatus.NO_LEGACY_DATA
    assert not new_dir.exists()


def test_skips_without_an_old_folder(tmp_path: Path, new_dir: Path) -> None:
    result = migrate_legacy_data(tmp_path / "missing", new_dir)
    assert result.status is MigrationStatus.NO_LEGACY_DATA
    assert not new_dir.exists()


def test_existing_new_files_are_never_overwritten(old_dir: Path, new_dir: Path) -> None:
    (new_dir / "Logs").mkdir(parents=True)
    (new_dir / "window.ini").write_text("new")
    (new_dir / "Logs" / "panelo.log").write_text("new log")
    (new_dir / "panelo.db-wal").write_bytes(b"stray")
    result = migrate_legacy_data(old_dir, new_dir)
    assert result.status is MigrationStatus.MIGRATED
    assert (new_dir / "window.ini").read_text() == "new"
    assert (new_dir / "Logs" / "panelo.log").read_text() == "new log"
    assert (new_dir / "Files" / "Docs" / "a.txt").read_text() == "hello"
    assert (new_dir / "panelo.db").read_bytes() == b"db"
    assert (new_dir / "panelo.db-wal").read_bytes() == b"wal"
    # the stray sidecar was set aside, not deleted
    assert [p.read_bytes() for p in new_dir.glob("panelo.db-wal.orphaned-*")] == [b"stray"]
    assert not list(new_dir.parent.glob(".panelo-migration-*"))


def test_second_run_is_a_no_op(old_dir: Path, new_dir: Path) -> None:
    assert migrate_legacy_data(old_dir, new_dir).status is MigrationStatus.MIGRATED
    (new_dir / "panelo.db").write_bytes(b"changed since")
    first = _snapshot(new_dir)
    assert migrate_legacy_data(old_dir, new_dir).status is MigrationStatus.ALREADY_PRESENT
    assert _snapshot(new_dir) == first


def test_stale_staging_from_an_interrupted_run_is_cleared(old_dir: Path, new_dir: Path) -> None:
    stale = new_dir.parent / ".panelo-migration-deadbeef"
    stale.mkdir(parents=True)
    (stale / "half.txt").write_text("x")
    assert migrate_legacy_data(old_dir, new_dir).status is MigrationStatus.MIGRATED
    assert not stale.exists()


def test_failure_is_reported_not_raised(
    old_dir: Path, new_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    before = _snapshot(old_dir)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(legacy_migration.shutil, "copytree", broken)
    with caplog.at_level(logging.ERROR, logger="panelo.migration"):
        result = migrate_legacy_data_safely(old_dir, new_dir)
    assert result.status is MigrationStatus.FAILED
    assert "disk full" in result.detail
    assert not (new_dir / "panelo.db").exists()
    assert _snapshot(old_dir) == before
    # a later attempt still works
    monkeypatch.undo()
    assert migrate_legacy_data(old_dir, new_dir).status is MigrationStatus.MIGRATED


def test_failure_before_the_database_is_placed_leaves_migration_retryable(
    old_dir: Path, new_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_dir.mkdir(parents=True)  # forces the merge path
    real_rename = legacy_migration.os.rename

    def rename(src: object, dst: object) -> None:
        if Path(str(dst)) == new_dir / "panelo.db":
            raise OSError("crash")
        real_rename(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(legacy_migration.os, "rename", rename)
    assert migrate_legacy_data_safely(old_dir, new_dir).status is MigrationStatus.FAILED
    assert not (new_dir / "panelo.db").exists()
    monkeypatch.undo()
    assert migrate_legacy_data(old_dir, new_dir).status is MigrationStatus.MIGRATED
    assert (new_dir / "panelo.db").read_bytes() == b"db"


# -- the Windows "start at sign-in" entry ----------------------------------------------------


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    path = tmp_path / "panelo.exe"
    path.write_bytes(b"")
    return path


def test_old_run_value_is_replaced_by_panelo_entry(launcher: Path) -> None:
    legacy, current = FakeStartupRegistry(), FakeStartupRegistry()
    legacy.write('"C:\\old\\productivity-app.exe" --startup')
    assert migrate_legacy_startup_entry(legacy, current, launcher) is True
    assert legacy.inspect() is None
    assert current.inspect() == RegistryValue(build_startup_command(launcher), 1)


def test_existing_panelo_entry_is_kept(launcher: Path) -> None:
    legacy, current = FakeStartupRegistry(), FakeStartupRegistry()
    legacy.write("old")
    current.write("custom")
    assert migrate_legacy_startup_entry(legacy, current, launcher) is True
    assert legacy.inspect() is None
    assert current.inspect() == RegistryValue("custom", 1)


def test_no_old_run_value_changes_nothing(launcher: Path) -> None:
    legacy, current = FakeStartupRegistry(), FakeStartupRegistry()
    assert migrate_legacy_startup_entry(legacy, current, launcher) is False
    assert current.inspect() is None


def test_old_run_value_kept_without_a_launcher() -> None:
    legacy, current = FakeStartupRegistry(), FakeStartupRegistry()
    legacy.write("old")
    assert migrate_legacy_startup_entry(legacy, current, None) is False
    assert legacy.inspect() == RegistryValue("old", 1)
    assert current.inspect() is None


def test_registry_failure_does_not_raise(launcher: Path) -> None:
    class Broken(FakeStartupRegistry):
        def write(self, command: str) -> None:
            raise OSError("access denied")

    legacy = FakeStartupRegistry()
    legacy.write("old")
    assert migrate_legacy_startup_entry(legacy, Broken(), launcher) is False
    assert legacy.inspect() == RegistryValue("old", 1)
