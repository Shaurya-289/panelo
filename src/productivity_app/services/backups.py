"""Native SQLite backup artifacts and strict read-only validation."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast

from productivity_app.core.clock import Clock
from productivity_app.core.exceptions import (
    BackupError,
    BackupValidationError,
    NotFoundError,
    UnsupportedBackupVersionError,
    ValidationError,
)
from productivity_app.core.ids import IdGenerator
from productivity_app.core.lifecycle import ApplicationLifecycleGate
from productivity_app.db.fts_contract import (
    EXPECTED_NORMALIZED_FTS_TRIGGER_SQL,
    normalize_fts_sql,
)
from productivity_app.schemas.backups import (
    BackupKind,
    BackupPageQuery,
    BackupValidationView,
    BackupView,
    DeleteBackup,
    ValidateBackup,
)
from productivity_app.services.settings import SettingsService

_REVISION: Final = "20261103_0003"
_AUTO_BACKUP_INTERVAL: Final = timedelta(hours=24)
_LOGGER = logging.getLogger(__name__)
_KINDS: Final = {"manual": "MANUAL", "auto": "AUTO", "pre-restore": "PRE_RESTORE"}
_FILENAME = re.compile(
    r"^productivity-backup-(?P<stamp>\d{8}T\d{6}\.\d{6}Z)-(?P<suffix>[0-9a-f]{12})-(?P<kind>manual|auto|pre-restore)\.sqlite3$"
)
_EXPECTED_COLUMNS: Final[dict[str, set[str]]] = {
    "alembic_version": {"version_num"},
    "app_settings": {"id", "theme", "global_hotkey", "start_with_windows", "minimize_to_tray", "notifications_enabled", "default_timer_duration_ms", "pomodoro_work_duration_ms", "short_break_duration_ms", "long_break_duration_ms", "updated_at_utc", "version", "user_name", "tour_done", "notification_style", "notification_sound", "backdrop_blur", "backdrop_dim", "overlay_size_pct", "overlay_layout_json", "files_view_mode", "library_path", "auto_backup", "keep_backups", "last_auto_backup_at_utc"},
    "notes": {"id", "title", "content", "content_format", "is_pinned", "is_archived", "color", "created_at_utc", "updated_at_utc", "version", "deleted_at_utc"},
    "tasks": {"id", "title", "description", "status", "priority", "start_at_utc", "due_at_utc", "due_has_time", "completed_at_utc", "cancelled_at_utc", "created_at_utc", "updated_at_utc", "version", "deleted_at_utc"},
    "reminders": {"id", "title", "message", "due_at_utc", "scheduled_timezone", "status", "task_id", "note_id", "last_triggered_at_utc", "dismissed_at_utc", "completed_at_utc", "cancelled_at_utc", "snooze_count", "repeat_kind", "repeat_days", "repeat_day_of_month", "repeat_every_n", "repeat_every_unit", "anchor_at_utc", "created_at_utc", "updated_at_utc", "version"},
    "reminder_events": {"id", "reminder_id", "event_type", "occurred_at_utc", "previous_status", "new_status", "old_due_at_utc", "new_due_at_utc", "metadata_json"},
    "timer_presets": {"id", "name", "normalized_name", "duration_ms", "sort_order", "is_builtin", "created_at_utc", "updated_at_utc", "version", "deleted_at_utc"},
    "timers": {"id", "name", "preset_id", "task_id", "state", "planned_duration_ms", "elapsed_before_run_ms", "run_started_at_utc", "deadline_at_utc", "first_started_at_utc", "ended_at_utc", "created_at_utc", "updated_at_utc", "version", "deleted_at_utc"},
    "timer_runs": {"id", "timer_id", "task_id", "planned_duration_ms", "actual_duration_ms", "started_at_utc", "ended_at_utc", "outcome"},
    "notification_deliveries": {"id", "kind", "aggregate_type", "aggregate_id", "dedupe_key", "title", "body", "status", "attempt_count", "available_at_utc", "lease_until_utc", "last_error_code", "last_error_message", "provider_message_id", "created_at_utc", "delivered_at_utc"},
    "external_notifications": {"id", "source", "source_device_id", "external_id", "app_name", "title", "body", "received_at_utc", "is_read", "is_dismissed", "created_at_utc"},
    "focus_sequences": {"id", "state", "task_id", "current_phase_index", "work_duration_ms", "short_break_duration_ms", "long_break_duration_ms", "completed_at_utc", "cancelled_at_utc", "created_at_utc", "updated_at_utc", "version"},
    "focus_sessions": {"id", "sequence_id", "timer_id", "phase_kind", "phase_index", "created_at_utc"},
    "search_documents": {"rowid", "entity_type", "entity_id", "title", "body"},
}
_EXPECTED_FTS_SHADOWS: Final[frozenset[str]] = frozenset(
    {
        "unified_search_fts_config",
        "unified_search_fts_data",
        "unified_search_fts_docsize",
        "unified_search_fts_idx",
    }
)
_EXPECTED_FTS_SQL: Final = (
    "create virtual table unified_search_fts using fts5(title, body, "
    "content='search_documents', content_rowid='rowid', "
    "tokenize='unicode61 remove_diacritics 2')"
)


@dataclass(frozen=True, slots=True)
class _ValidatedBackup:
    path: Path
    filename: str
    kind: str
    created_at_utc: datetime
    size_bytes: int
    sha256: str
    identity: tuple[int, int, int, int]


class BackupService:
    def __init__(
        self,
        database_path: Path,
        busy_timeout_ms: int,
        clock: Clock,
        ids: IdGenerator,
        gate: ApplicationLifecycleGate,
        settings: SettingsService | None = None,
    ) -> None:
        self._settings = settings
        self._database_path = database_path.resolve()
        self._busy_timeout_ms = busy_timeout_ms
        self._clock = clock
        self._ids = ids
        self._gate = gate
        self._backup_directory = database_path.parent / "backups"

    @property
    def backup_directory(self) -> Path:
        return self._backup_directory

    def create_backup(self) -> BackupValidationView:
        with self._gate.operation():
            return self._create_backup("MANUAL")

    def run_auto_backup_if_due(self, now: datetime | None = None) -> BackupValidationView | None:
        """Back up when automatic backups are on and the last one is over 24 hours old.

        Records the time, then prunes the backup directory to the newest ``keep_backups``
        files.  Manual and safety backups count toward that limit (the Settings list shows
        them all), but the backup just made is always kept.  Returns the new backup, or
        ``None`` when nothing was due.
        """
        if self._settings is None:
            raise BackupError("Automatic backups need the settings service.")
        moment = (now or self._clock.now_utc()).astimezone(UTC)
        settings = self._settings.get_settings()
        last = settings.last_auto_backup_at_utc
        if not settings.auto_backup or (
            last is not None and last <= moment and moment - last <= _AUTO_BACKUP_INTERVAL
        ):
            return None
        with self._gate.operation():
            created = self._create_backup("AUTO", moment)
            self._settings.record_auto_backup(moment)
            self._prune(settings.keep_backups, keep_name=created.filename)
        return created

    def _prune(self, keep: int, *, keep_name: str) -> None:
        """Delete the oldest managed backups beyond the newest ``keep``."""
        try:
            entries = [
                (parsed[0], path)
                for path in self._backup_directory.iterdir()
                if (parsed := self._parse_managed(path)) is not None and path.name != keep_name
            ]
        except OSError as exc:
            raise BackupError("Could not list backup artifacts.") from exc
        entries.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        for _, path in entries[max(0, keep - 1) :]:
            try:
                path.unlink()
            except OSError:
                _LOGGER.warning("Could not delete old backup %s", path.name, exc_info=True)

    def list_backups(self, query: BackupPageQuery | None = None) -> tuple[BackupView, ...]:
        page = query or BackupPageQuery()
        with self._gate.operation():
            try:
                if not self._backup_directory.exists():
                    return ()
                self._ensure_managed_directory()
                entries: list[BackupView] = []
                for path in self._backup_directory.iterdir():
                    parsed = self._parse_managed(path)
                    if parsed is not None:
                        stamp, kind = parsed
                        entries.append(BackupView(filename=path.name, kind=cast(BackupKind, kind), created_at_utc=stamp, size_bytes=path.stat().st_size))
            except OSError as exc:
                raise BackupError("Could not list backup artifacts.") from exc
            entries.sort(key=lambda item: (item.created_at_utc, item.filename), reverse=True)
            return tuple(entries[page.offset : page.offset + page.limit])

    def validate_backup(self, command: ValidateBackup) -> BackupValidationView:
        with self._gate.operation():
            return self._as_view(self._validate_path(command.source_path))

    def delete_backup(self, command: DeleteBackup) -> None:
        with self._gate.operation():
            if _FILENAME.fullmatch(command.filename) is None or Path(command.filename).name != command.filename:
                raise ValidationError("Backup filename is not a managed backup artifact.")
            path = self._backup_directory / command.filename
            try:
                if self._parse_managed(path) is None:
                    if not path.exists():
                        raise NotFoundError("Backup was not found.")
                    raise ValidationError("Backup filename is not a safe managed artifact.")
                path.unlink()
            except FileNotFoundError as exc:
                raise NotFoundError("Backup was not found.") from exc
            except OSError as exc:
                raise BackupError("Could not delete backup.") from exc

    def _create_backup(self, kind: str, now: datetime | None = None) -> BackupValidationView:
        self._ensure_managed_directory(create=True)
        now = (now or self._clock.now_utc()).astimezone(UTC)
        for _ in range(8):
            final = self._backup_directory / self._filename(now, kind)
            temp = self._backup_directory / f".{final.name}.{self._ids.new()}.partial"
            try:
                self._copy_sqlite(self._database_path, temp)
                validated = self._validate_path(temp, filename=final.name, kind=kind, timestamp=now)
                self._fsync_file(temp)
                # ``link`` publishes atomically but, unlike ``replace``, never
                # overwrites an artifact another creator won concurrently.
                try:
                    os.link(temp, final)
                except FileExistsError:
                    continue
                temp.unlink()
                return self._as_view(
                    _ValidatedBackup(
                        final, final.name, kind, now, validated.size_bytes,
                        validated.sha256, self._identity(final),
                    )
                )
            except BackupError:
                raise
            except (OSError, sqlite3.Error) as exc:
                raise BackupError("Could not create SQLite backup.") from exc
            finally:
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass
        raise BackupError("Could not reserve a unique backup filename.")

    @staticmethod
    def _fsync_file(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDWR)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise BackupError("Could not flush backup artifact.") from exc

    def _copy_sqlite(self, source: Path, destination: Path) -> None:
        source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        destination_connection: sqlite3.Connection | None = None
        try:
            source_connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            if [row[0] for row in source_connection.execute("PRAGMA quick_check")] != ["ok"]:
                raise BackupValidationError("Source database failed quick_check.")
            destination_connection = sqlite3.connect(destination)
            source_connection.backup(destination_connection)
            mode = destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise BackupError("Backup artifact could not be made standalone.")
            destination_connection.commit()
        finally:
            if destination_connection is not None:
                destination_connection.close()
            source_connection.close()

    def stage_approved_restore_source(
        self, original: Path, approved: _ValidatedBackup, destination: Path
    ) -> _ValidatedBackup:
        """Copy exactly the approved source bytes before touching the live DB.

        The source is opened once, copied into the controlled staging location,
        and compared with the checksum captured during initial validation.  The
        remainder of restore works exclusively from ``destination``.
        """
        try:
            if original.resolve(strict=True) != approved.path:
                raise BackupValidationError("Backup source changed after validation.")
            if self._identity(approved.path) != approved.identity:
                raise BackupValidationError("Backup source identity changed after validation.")
            digest = hashlib.sha256()
            with original.open("rb") as source_handle, destination.open("xb") as destination_handle:
                source_stat = os.fstat(source_handle.fileno())
                source_identity = (
                    source_stat.st_dev,
                    source_stat.st_ino,
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                )
                if source_identity != approved.identity:
                    raise BackupValidationError("Backup source changed after validation.")
                while chunk := source_handle.read(1024 * 1024):
                    destination_handle.write(chunk)
                    digest.update(chunk)
            # Deletion/replacement while the source handle was open is still a
            # failed source operation, even where the OS permits the handle to
            # continue reading the old file.
            if original.resolve(strict=True) != approved.path:
                raise BackupValidationError("Backup source changed during staging.")
        except FileNotFoundError as exc:
            raise BackupValidationError("Backup source disappeared during staging.") from exc
        except BackupValidationError:
            raise
        except OSError as exc:
            raise BackupError("Could not stage backup source.") from exc
        if digest.hexdigest() != approved.sha256:
            raise BackupValidationError("Backup source changed during staging.")
        staged = self._validate_path(destination)
        if staged.sha256 != approved.sha256:
            raise BackupValidationError("Staged backup content differs from approved source.")
        return staged

    def _validate_path(self, source: Path, *, filename: str | None = None, kind: str | None = None, timestamp: datetime | None = None) -> _ValidatedBackup:
        try:
            source_mode = source.lstat().st_mode
            if stat.S_ISLNK(source_mode):
                raise BackupValidationError("Backup source must not be a symbolic link.")
            path = source.resolve(strict=True)
            if not self._is_regular_file(path):
                raise BackupValidationError("Backup source must be an existing regular file.")
        except FileNotFoundError as exc:
            raise BackupValidationError("Backup source must be an existing regular file.") from exc
        except BackupValidationError:
            raise
        except OSError as exc:
            raise BackupError("Could not access backup source.") from exc
        try:
            # Distinguish an inaccessible filesystem object from a readable
            # SQLite file whose contents fail validation below.
            with path.open("rb"):
                pass
        except OSError as exc:
            raise BackupError("Could not open backup source.") from exc
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise BackupValidationError("Backup is not a readable SQLite database.") from exc
        try:
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                raise BackupValidationError("Backup integrity_check did not return ok.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BackupValidationError("Backup foreign_key_check reported violations.")
            table_rows = connection.execute("PRAGMA table_list").fetchall()
            tables = {row[1] for row in table_rows if row[0] == "main" and row[2] == "table" and not str(row[1]).startswith("sqlite_")}
            virtual = {row[1] for row in table_rows if row[0] == "main" and row[2] == "virtual"}
            shadows = {row[1] for row in table_rows if row[0] == "main" and row[2] == "shadow"}
            if (
                tables != set(_EXPECTED_COLUMNS)
                or virtual != {"unified_search_fts"}
                or shadows != _EXPECTED_FTS_SHADOWS
            ):
                raise BackupValidationError("Backup has an unexpected application table set.")
            revision_rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            if len(revision_rows) != 1:
                raise BackupValidationError("Backup Alembic metadata is malformed.")
            revision = revision_rows[0][0]
            if revision != _REVISION:
                raise UnsupportedBackupVersionError("Backup schema revision is unsupported.")
            for table, expected in _EXPECTED_COLUMNS.items():
                actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if actual != expected:
                    raise BackupValidationError(f"Backup table {table} has unexpected columns.")
            fts_columns = {row[1] for row in connection.execute("PRAGMA table_info(unified_search_fts)")}
            if fts_columns != {"title", "body"}:
                raise BackupValidationError("Backup full-text index has unexpected columns.")
            fts_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='unified_search_fts'"
            ).fetchone()
            if fts_sql is None or self._normalize_sql(str(fts_sql[0])) != _EXPECTED_FTS_SQL:
                raise BackupValidationError("Backup full-text index definition is invalid.")
            triggers = {
                str(row[0]): normalize_fts_sql(str(row[1]))
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
                )
            }
            if triggers != EXPECTED_NORMALIZED_FTS_TRIGGER_SQL:
                raise BackupValidationError("Backup full-text synchronization triggers are invalid.")
            self._validate_focus_schema(connection)
            if connection.execute("SELECT id FROM app_settings WHERE id = 1").fetchone() is None:
                raise BackupValidationError("Backup lacks the settings singleton.")
        except BackupValidationError:
            raise
        except sqlite3.Error as exc:
            raise BackupValidationError("Backup validation query failed.") from exc
        finally:
            connection.close()
        try:
            parsed = self._parse_managed(path)
            created = timestamp or (
                parsed[0] if parsed else datetime.fromtimestamp(path.stat().st_mtime, UTC)
            )
            parsed_kind = kind or (parsed[1] if parsed else "MANUAL")
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            size = path.stat().st_size
            identity = self._identity(path)
        except OSError as exc:
            raise BackupError("Could not inspect backup source.") from exc
        return _ValidatedBackup(path, filename or path.name, parsed_kind, created, size, digest, identity)

    @staticmethod
    def _normalize_sql(value: str) -> str:
        return " ".join(value.lower().split())

    @staticmethod
    def _validate_focus_schema(connection: sqlite3.Connection) -> None:
        focus_sql = {
            row[0]: BackupService._normalize_sql(str(row[1]))
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name IN ('focus_sequences', 'focus_sessions')"
            )
        }
        sequence_fragments = (
            "constraint ck_focus_sequences_state check (state in ('active', 'completed', 'cancelled'))",
            "constraint ck_focus_sequences_phase_index check (current_phase_index between 0 and 7)",
            "constraint ck_focus_sequences_work_duration check (work_duration_ms between 1000 and 86400000)",
            "constraint ck_focus_sequences_short_duration check (short_break_duration_ms between 1000 and 86400000)",
            "constraint ck_focus_sequences_long_duration check (long_break_duration_ms between 1000 and 86400000)",
            "constraint ck_focus_sequences_version check (version >= 1)",
            "constraint ck_focus_sequences_terminal_timestamps check",
        )
        session_fragments = (
            "constraint uq_focus_sessions_sequence_phase unique (sequence_id, phase_index)",
            "constraint ck_focus_sessions_kind check (phase_kind in ('focus', 'short_break', 'long_break'))",
            "constraint ck_focus_sessions_sequence_phase check",
        )
        if any(value not in focus_sql.get("focus_sequences", "") for value in sequence_fragments):
            raise BackupValidationError("Backup focus sequence checks are invalid.")
        if any(value not in focus_sql.get("focus_sessions", "") for value in session_fragments):
            raise BackupValidationError("Backup focus session checks are invalid.")

        sequence_foreign_keys = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(focus_sequences)")
        }
        if sequence_foreign_keys != {("task_id", "tasks", "id", "SET NULL")}:
            raise BackupValidationError("Backup focus sequence foreign keys are invalid.")
        session_foreign_keys = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(focus_sessions)")
        }
        if session_foreign_keys != {
            ("sequence_id", "focus_sequences", "id", "CASCADE"),
            ("timer_id", "timers", "id", "CASCADE"),
        }:
            raise BackupValidationError("Backup focus session foreign keys are invalid.")

        sequence_indexes = connection.execute("PRAGMA index_list(focus_sequences)").fetchall()
        sequence_named = {row[1] for row in sequence_indexes if row[3] != "pk"}
        if sequence_named != {"ux_focus_sequences_one_active"}:
            raise BackupValidationError("Backup focus sequence indexes are invalid.")

        session_indexes = connection.execute("PRAGMA index_list(focus_sessions)").fetchall()
        session_named = {row[1] for row in session_indexes if row[3] == "c"}
        if session_named != {"ix_focus_sessions_sequence_phase"}:
            raise BackupValidationError("Backup focus session indexes are invalid.")
        session_specs = {
            (
                bool(row[2]),
                tuple(
                    str(column[2])
                    for column in connection.execute(f"PRAGMA index_xinfo({row[1]})")
                    if column[5]
                ),
            )
            for row in session_indexes
            if row[3] != "pk"
        }
        if session_specs != {
            (False, ("sequence_id", "phase_index")),
            (True, ("sequence_id", "phase_index")),
            (True, ("timer_id",)),
        }:
            raise BackupValidationError("Backup focus session indexes are invalid.")

        active_index = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='ux_focus_sequences_one_active'"
        ).fetchone()
        if active_index is None or BackupService._normalize_sql(str(active_index[0])) != (
            "create unique index ux_focus_sequences_one_active "
            "on focus_sequences (1) where state = 'active'"
        ):
            raise BackupValidationError("Backup active focus sequence index is invalid.")

    def _verify_unchanged_source(self, original: Path, validated: _ValidatedBackup) -> None:
        """Close the restore TOCTOU window with identity and content checks."""
        try:
            if stat.S_ISLNK(original.lstat().st_mode):
                raise BackupValidationError("Backup source must not be a symbolic link.")
            if original.resolve(strict=True) != validated.path:
                raise BackupValidationError("Backup source changed after validation.")
            if self._identity(validated.path) != validated.identity:
                raise BackupValidationError("Backup source identity changed after validation.")
            with validated.path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except FileNotFoundError as exc:
            raise BackupValidationError("Backup source disappeared during revalidation.") from exc
        except OSError as exc:
            raise BackupError("Could not revalidate backup source.") from exc
        if digest != validated.sha256:
            raise BackupValidationError("Backup source changed after validation.")

    @staticmethod
    def _identity(path: Path) -> tuple[int, int, int, int]:
        value = path.stat()
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    @staticmethod
    def _is_regular_file(path: Path) -> bool:
        value = path.lstat()
        mode = value.st_mode
        attributes = value.st_file_attributes if hasattr(value, "st_file_attributes") else 0
        return stat.S_ISREG(mode) and not stat.S_ISLNK(mode) and not (attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    def _ensure_managed_directory(self, *, create: bool = False) -> None:
        try:
            if create:
                self._backup_directory.mkdir(parents=True, exist_ok=True)
            if not self._backup_directory.exists():
                return
            value = self._backup_directory.lstat()
            attributes = value.st_file_attributes if hasattr(value, "st_file_attributes") else 0
            if (
                not stat.S_ISDIR(value.st_mode)
                or stat.S_ISLNK(value.st_mode)
                or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            ):
                raise BackupError("Managed backup directory is unsafe.")
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError("Could not access backup directory.") from exc

    def _parse_managed(self, path: Path) -> tuple[datetime, str] | None:
        if not self._is_regular_file(path):
            return None
        if path.parent.resolve() != self._backup_directory.resolve():
            return None
        match = _FILENAME.fullmatch(path.name)
        if match is None:
            return None
        try:
            timestamp = datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
        except ValueError:
            return None
        return timestamp, _KINDS[match.group("kind")]

    def _filename(self, now: datetime, kind: str) -> str:
        stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
        suffix = self._ids.new().replace("-", "")[:12]
        suffix = suffix.lower().ljust(12, "0")
        label = next(name for name, value in _KINDS.items() if value == kind)
        return f"productivity-backup-{stamp}-{suffix}-{label}.sqlite3"

    @staticmethod
    def _as_view(value: _ValidatedBackup) -> BackupValidationView:
        return BackupValidationView(filename=value.filename, kind=cast(BackupKind, value.kind), created_at_utc=value.created_at_utc, size_bytes=value.size_bytes, sha256=value.sha256, schema_revision=_REVISION)
