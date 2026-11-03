from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from productivity_app.core.exceptions import StaleVersionError
from productivity_app.schemas.settings import FilesViewMode, NotificationStyle, SettingsPatch, Theme


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


def test_new_design_settings_have_their_documented_defaults(application) -> None:
    settings = application.settings.get_settings()
    assert (
        settings.user_name,
        settings.tour_done,
        settings.notification_style,
        settings.notification_sound,
        settings.backdrop_blur,
        settings.backdrop_dim,
        settings.overlay_size_pct,
        settings.overlay_layout_json,
        settings.files_view_mode,
        settings.library_path,
        settings.auto_backup,
        settings.keep_backups,
        settings.last_auto_backup_at_utc,
    ) == ("", False, NotificationStyle.TOAST, "", 100, 45, 85, "", FilesViewMode.LIST, "", True, 7, None)
    # the existing settings the new UI keeps
    assert settings.minimize_to_tray is True and settings.notifications_enabled is True
    assert (settings.pomodoro_work_duration_ms, settings.short_break_duration_ms) == (1_500_000, 300_000)


def test_every_new_setting_can_be_updated_and_read_back(application) -> None:
    before = application.settings.get_settings()
    layout = '{"tiles": [1, 2, 3], "name": "caf\u00e9"}'
    after = application.settings.update_settings(
        SettingsPatch(
            expected_version=before.version,
            user_name="Ada",
            tour_done=True,
            notification_style="BOTH",
            notification_sound="C:/Sounds/ding.wav",
            backdrop_blur=0,
            backdrop_dim=100,
            overlay_size_pct=60,
            overlay_layout_json=layout,
            files_view_mode="GRID",
            library_path="D:/Notes/Files",
            auto_backup=False,
            keep_backups=60,
        )
    )
    assert after.version == before.version + 1
    assert application.settings.get_settings() == after
    assert (after.user_name, after.tour_done, after.notification_style) == ("Ada", True, NotificationStyle.BOTH)
    assert (after.backdrop_blur, after.backdrop_dim, after.overlay_size_pct) == (0, 100, 60)
    assert after.overlay_layout_json == layout and after.files_view_mode is FilesViewMode.GRID
    assert (after.library_path, after.auto_backup, after.keep_backups) == ("D:/Notes/Files", False, 60)
    # a text setting can be cleared again (an empty string is a value, not "unset")
    cleared = application.settings.update_settings(
        SettingsPatch(expected_version=after.version, user_name="", overlay_layout_json="")
    )
    assert cleared.user_name == "" and cleared.overlay_layout_json == ""


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_name", "x" * 61),
        ("notification_style", "LOUD"),
        ("notification_sound", "x" * 261),
        ("backdrop_blur", -1),
        ("backdrop_blur", 101),
        ("backdrop_dim", -1),
        ("backdrop_dim", 101),
        ("overlay_size_pct", 59),
        ("overlay_size_pct", 91),
        ("files_view_mode", "TREE"),
        ("library_path", "x" * 1025),
        ("keep_backups", 0),
        ("keep_backups", 61),
    ],
)
def test_new_settings_are_validated(field: str, value: object) -> None:
    with pytest.raises(PydanticValidationError):
        SettingsPatch(expected_version=1, **{field: value})  # type: ignore[arg-type]


def test_the_boundary_values_are_accepted() -> None:
    SettingsPatch(
        expected_version=1, user_name="x" * 60, notification_sound="x" * 260, library_path="x" * 1024,
        backdrop_blur=100, backdrop_dim=0, overlay_size_pct=90, keep_backups=1,
    )


def test_the_last_automatic_backup_time_is_not_user_editable(application, clock) -> None:
    with pytest.raises(PydanticValidationError):
        SettingsPatch(expected_version=1, last_auto_backup_at_utc=clock.now)  # type: ignore[call-arg]
    before = application.settings.get_settings()
    application.settings.record_auto_backup(clock.now)
    after = application.settings.get_settings()
    assert after.last_auto_backup_at_utc == clock.now
    # bookkeeping: it must not invalidate a version the user's editor is holding
    assert after.version == before.version and after.updated_at_utc == before.updated_at_utc


def test_reset_restores_the_defaults_but_keeps_identity_and_library_location(application) -> None:
    before = application.settings.get_settings()
    changed = application.settings.update_settings(
        SettingsPatch(
            expected_version=before.version, user_name="Ada", tour_done=True, library_path="D:/Files",
            notification_style="WINDOWS", backdrop_blur=10, keep_backups=3, auto_backup=False,
            files_view_mode="GRID", overlay_size_pct=70, minimize_to_tray=False,
        )
    )
    reset = application.settings.reset_settings(changed.version)
    assert (reset.user_name, reset.tour_done, reset.library_path) == ("Ada", True, "D:/Files")
    assert reset.notification_style is NotificationStyle.TOAST and reset.backdrop_blur == 100
    assert (reset.keep_backups, reset.auto_backup, reset.files_view_mode) == (7, True, FilesViewMode.LIST)
    assert reset.overlay_size_pct == 85 and reset.minimize_to_tray is True


def test_corrupt_new_setting_is_reported_as_database_corruption(application) -> None:
    from sqlalchemy import text

    from productivity_app.core.exceptions import DatabaseCorruptionError

    with application.engine.begin() as connection:
        connection.execute(text("PRAGMA ignore_check_constraints=ON"))
        connection.execute(text("UPDATE app_settings SET notification_style = 'LOUD' WHERE id = 1"))
        connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
    with pytest.raises(DatabaseCorruptionError):
        application.settings.get_settings()
