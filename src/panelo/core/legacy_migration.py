"""One-time carry-over of data from the app's old name ("ProductivityApp") to Panelo.

The old data folder is only ever read: everything is *copied* into the new folder and the old
one is left exactly as it was, so a failed or interrupted migration can always be retried (or
recovered by hand).  The new database file is the "done" signal: it is put in place last, and
the migration never runs when a Panelo database already exists.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from panelo.core.paths import (
    LEGACY_APP_AUTHOR,
    LEGACY_APP_NAME,
    LEGACY_DB_FILENAME,
    LEGACY_LOG_FILENAME,
    AppPaths,
)

_LOGGER = logging.getLogger("panelo.migration")

DB_FILENAME = "panelo.db"
LOG_FILENAME = "panelo.log"
LOG_SUBDIR = "Logs"
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
MARKER_FILENAME = "migrated-from-ProductivityApp.txt"
_STAGING_PREFIX = ".panelo-migration-"


class MigrationStatus(StrEnum):
    MIGRATED = "MIGRATED"
    NO_LEGACY_DATA = "NO_LEGACY_DATA"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: MigrationStatus
    old_dir: Path
    new_dir: Path
    detail: str = ""


def legacy_data_dir() -> Path:
    return AppPaths.for_application(LEGACY_APP_NAME, LEGACY_APP_AUTHOR).data_dir


def migrate_legacy_data(old_dir: Path, new_dir: Path) -> MigrationResult:
    """Copy ``old_dir`` into ``new_dir`` once, renaming the database and log files.

    Rules: runs only when ``old_dir/productivity.db`` exists and ``new_dir/panelo.db`` does not.
    Existing files in ``new_dir`` are never overwritten.  Raises on I/O failure (see
    :func:`migrate_legacy_data_safely`).
    """
    old_db = old_dir / LEGACY_DB_FILENAME
    new_db = new_dir / DB_FILENAME
    if new_db.exists():
        return MigrationResult(MigrationStatus.ALREADY_PRESENT, old_dir, new_dir)
    if not old_db.is_file():
        return MigrationResult(MigrationStatus.NO_LEGACY_DATA, old_dir, new_dir)
    if old_dir.resolve() == new_dir.resolve():
        raise ValueError("Old and new data folders are the same.")

    new_dir.parent.mkdir(parents=True, exist_ok=True)
    _remove_stale_staging(new_dir.parent)
    staging = new_dir.parent / f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
    try:
        shutil.copytree(old_dir, staging)
        _rename_in_staging(staging)
        (staging / MARKER_FILENAME).write_text(
            f"Copied from {old_dir} on {datetime.now(UTC).isoformat()}.\n"
            "The old folder was left untouched and can be deleted once Panelo works.\n",
            encoding="utf-8",
        )
        if new_db.exists():  # another process finished first
            return MigrationResult(MigrationStatus.ALREADY_PRESENT, old_dir, new_dir)
        if not new_dir.exists():
            try:
                os.rename(staging, new_dir)  # atomic: all or nothing
            except OSError:
                if not new_dir.exists():
                    raise
                _merge_into(staging, new_dir)  # lost a race to create the folder
        else:
            _merge_into(staging, new_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if not new_db.is_file():
        raise OSError(f"Migration finished without {new_db}.")
    return MigrationResult(MigrationStatus.MIGRATED, old_dir, new_dir)


def migrate_legacy_data_safely(old_dir: Path, new_dir: Path) -> MigrationResult:
    """:func:`migrate_legacy_data` that never raises: a failure is logged and reported."""
    try:
        return migrate_legacy_data(old_dir, new_dir)
    except Exception as exc:
        _LOGGER.exception("Could not copy data from %s to %s.", old_dir, new_dir)
        return MigrationResult(MigrationStatus.FAILED, old_dir, new_dir, f"{type(exc).__name__}: {exc}")


def log_migration_result(result: MigrationResult) -> None:
    if result.status is MigrationStatus.MIGRATED:
        _LOGGER.info(
            "Copied ProductivityApp data from %s to %s (the old folder was left as it was).",
            result.old_dir, result.new_dir,
        )
    elif result.status is MigrationStatus.FAILED:
        _LOGGER.warning(
            "ProductivityApp data in %s was not copied (%s); it is still there, untouched.",
            result.old_dir, result.detail,
        )


def _remove_stale_staging(parent: Path) -> None:
    """Folders left by an interrupted earlier attempt are only ever our own copies."""
    for candidate in parent.glob(f"{_STAGING_PREFIX}*"):
        if candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate, ignore_errors=True)


def _rename_in_staging(staging: Path) -> None:
    for suffix in ("", *SQLITE_SIDECARS):
        source = staging / f"{LEGACY_DB_FILENAME}{suffix}"
        if source.exists():
            os.rename(source, staging / f"{DB_FILENAME}{suffix}")
    for folder in (staging, staging / LOG_SUBDIR):
        if not folder.is_dir():
            continue
        # productivity-app.log, productivity-app.log.1 ... (rotated copies)
        for source in folder.glob(f"{LEGACY_LOG_FILENAME}*"):
            target = folder / (LOG_FILENAME + source.name[len(LEGACY_LOG_FILENAME):])
            if source.is_file() and not target.exists():
                os.rename(source, target)


def _merge_into(staging: Path, new_dir: Path) -> None:
    """Move staged files into an existing ``new_dir`` without replacing anything there; the
    database goes last so it only appears once everything else is in place."""
    db_names = {DB_FILENAME, *(f"{DB_FILENAME}{s}" for s in SQLITE_SIDECARS)}
    for source in sorted(staging.rglob("*")):
        relative = source.relative_to(staging)
        if relative.as_posix() in db_names or not source.is_file():
            continue
        target = new_dir / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, target)  # never replaces on Windows
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for suffix in SQLITE_SIDECARS:
        # A sidecar without its database would be replayed into the copied one: set it aside.
        stray = new_dir / f"{DB_FILENAME}{suffix}"
        if stray.exists():
            os.rename(stray, stray.with_name(f"{stray.name}.orphaned-{stamp}"))
        staged = staging / stray.name
        if staged.exists():
            os.rename(staged, stray)
    os.rename(staging / DB_FILENAME, new_dir / DB_FILENAME)  # fails if one appeared meanwhile
