"""Stable application-level errors exposed by services."""

from __future__ import annotations


class AppError(Exception):
    code = "app_error"


class ValidationError(AppError):
    code = "validation_error"


class NotFoundError(AppError):
    code = "not_found"


class ConflictError(AppError):
    code = "conflict"


class StaleVersionError(ConflictError):
    code = "stale_version"


class DuplicateNameError(ConflictError):
    code = "duplicate_name"


class InvalidStateTransitionError(AppError):
    code = "invalid_state_transition"


class DatabaseError(AppError):
    code = "database_error"


class DatabaseBusyError(DatabaseError):
    code = "database_busy"


class MigrationError(DatabaseError):
    code = "migration_error"


class DatabaseCorruptionError(DatabaseError):
    code = "database_corruption"


class IntegrationError(AppError):
    code = "integration_error"


class NotificationDeliveryError(IntegrationError):
    code = "notification_delivery_error"


class HotkeyRegistrationError(IntegrationError):
    code = "hotkey_registration_error"


class BackupError(AppError):
    code = "backup_error"


class BackupValidationError(BackupError):
    code = "backup_validation_error"


class UnsupportedBackupVersionError(BackupValidationError):
    code = "unsupported_backup_version"


class BackupRestoreError(BackupError):
    code = "backup_restore_error"


class BackupRecoveryError(BackupRestoreError):
    code = "backup_recovery_error"


class ApplicationLifecycleError(ConflictError):
    code = "application_lifecycle_error"
