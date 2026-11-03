from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from productivity_app.core.exceptions import StaleVersionError
from productivity_app.schemas.settings import SettingsPatch, Theme


def test_settings_defaults_are_created_and_survive_reopen(application, app_config, clock) -> None:
    settings = application.settings.get_settings()
    assert settings.theme is Theme.SYSTEM
    assert settings.global_hotkey == "Ctrl+Alt+Space"
    assert settings.default_timer_duration_ms == 1_500_000
    application.close()

    from productivity_app.bootstrap import create_application

    restarted = create_application(app_config, clock)
    try:
        assert restarted.settings.get_settings() == settings
    finally:
        restarted.close()


def test_settings_update_increments_version_and_publishes_after_commit(application) -> None:
    before = application.settings.get_settings()
    after = application.settings.update_settings(
        SettingsPatch(
            expected_version=before.version, theme=Theme.DARK, notifications_enabled=False
        )
    )
    assert after.theme is Theme.DARK
    assert not after.notifications_enabled
    assert after.version == before.version + 1


def test_settings_reject_stale_version(application) -> None:
    before = application.settings.get_settings()
    application.settings.update_settings(
        SettingsPatch(expected_version=before.version, theme=Theme.LIGHT)
    )
    with pytest.raises(StaleVersionError):
        application.settings.update_settings(
            SettingsPatch(expected_version=before.version, theme=Theme.DARK)
        )


def test_settings_validation_rejects_invalid_duration_and_hotkey() -> None:
    with pytest.raises(PydanticValidationError):
        SettingsPatch(expected_version=1, default_timer_duration_ms=999)
    with pytest.raises(PydanticValidationError):
        SettingsPatch(expected_version=1, global_hotkey="Ctrl+Alt")
