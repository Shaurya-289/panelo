"""Business-level settings operations."""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from productivity_app.core.clock import Clock
from productivity_app.core.events import DomainEvent
from productivity_app.core.exceptions import (
    DatabaseCorruptionError,
    StaleVersionError,
    ValidationError,
)
from productivity_app.db.models.initial import DEFAULT_SETTINGS_VALUES, AppSettings
from productivity_app.db.unit_of_work import UnitOfWorkFactory
from productivity_app.schemas.settings import SettingsPatch, SettingsView, Theme


def _to_view(settings: AppSettings) -> SettingsView:
    try:
        return SettingsView(
            theme=Theme(settings.theme),
            global_hotkey=settings.global_hotkey,
            start_with_windows=settings.start_with_windows,
            minimize_to_tray=settings.minimize_to_tray,
            notifications_enabled=settings.notifications_enabled,
            default_timer_duration_ms=settings.default_timer_duration_ms,
            pomodoro_work_duration_ms=settings.pomodoro_work_duration_ms,
            short_break_duration_ms=settings.short_break_duration_ms,
            long_break_duration_ms=settings.long_break_duration_ms,
            updated_at_utc=settings.updated_at_utc,
            version=settings.version,
        )
    except (ValueError, PydanticValidationError) as exc:
        raise DatabaseCorruptionError("Persisted settings are invalid.") from exc


class SettingsService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def get_settings(self) -> SettingsView:
        with self._unit_of_work_factory() as unit_of_work:
            assert unit_of_work.settings is not None
            settings = unit_of_work.settings.get()
            if settings is None:
                settings = unit_of_work.settings.create_defaults(self._clock.now_utc())
                unit_of_work.commit()
            return _to_view(settings)

    def update_settings(self, patch: SettingsPatch) -> SettingsView:
        values = patch.model_dump(exclude={"expected_version"}, exclude_none=True)
        if not values:
            raise ValidationError("Settings update must include at least one mutable field.")
        with self._unit_of_work_factory() as unit_of_work:
            assert unit_of_work.settings is not None
            settings = unit_of_work.settings.update_if_version(
                patch.expected_version, values, self._clock.now_utc()
            )
            if settings is None:
                raise StaleVersionError(
                    "Settings were changed by another operation. Refresh and retry."
                )
            unit_of_work.collect_event(
                DomainEvent("SETTINGS_CHANGED", "1", self._clock.now_utc(), "settings:1")
            )
            unit_of_work.commit()
            return _to_view(settings)

    def reset_settings(self, expected_version: int) -> SettingsView:
        return self.update_settings(
            SettingsPatch.model_validate(
                {"expected_version": expected_version, **DEFAULT_SETTINGS_VALUES}
            )
        )
