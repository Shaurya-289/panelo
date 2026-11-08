"""The pure mappings between backend views and the dictionaries the QML pages read."""

from __future__ import annotations

from datetime import UTC, datetime

from panelo.schemas.reminders import ReminderRepeat
from panelo.ui.bridge import convert


def test_milliseconds_round_trip() -> None:
    moment = datetime(2026, 11, 3, 10, 30, 15, 250000, tzinfo=UTC)
    assert convert.to_ms(moment) == 1793701815250
    assert convert.from_ms(1793701815250) == moment
    assert convert.to_ms(None) is None


def test_rule_mapping_covers_every_kind() -> None:
    rules = [
        {"kind": "daily"},
        {"kind": "weekdays"},
        {"kind": "yearly"},
        {"kind": "weekly", "days": [1, 3, 5]},
        {"kind": "monthly", "day": 31},
        {"kind": "every", "n": 2, "unit": "hours"},
        {"kind": "every", "n": 3, "unit": "weeks"},
    ]
    for rule in rules:
        schema = convert.rule_from_qml(rule)
        assert isinstance(schema, ReminderRepeat)
        assert convert.rule_to_qml(schema) == rule


def test_bad_rules_mean_never() -> None:
    assert convert.rule_from_qml(None) is None
    assert convert.rule_from_qml({"kind": "fortnightly"}) is None
    assert convert.rule_from_qml({"kind": "weekly", "days": []}) is None
    assert convert.rule_from_qml({"kind": "monthly", "day": 0}) is None
    assert convert.rule_from_qml({"kind": "every", "n": 2, "unit": "years"}) is None
    assert convert.rule_to_qml(None) is None


def test_settings_keys_convert_both_ways() -> None:
    assert convert.setting_field("backdropBlur", 0.45) == ("backdrop_blur", 45)
    assert convert.setting_field("overlaySize", 0.95) == ("overlay_size_pct", 90)
    assert convert.setting_field("overlaySize", 0.3) == ("overlay_size_pct", 60)
    assert convert.setting_field("notificationStyle", "both") == ("notification_style", "BOTH")
    assert convert.setting_field("closeHidesToTray", False) == ("minimize_to_tray", False)
    assert convert.setting_field("reminderAlerts", True) == ("notifications_enabled", True)
    assert convert.setting_field("focusMs", 25 * 60000) == ("pomodoro_work_duration_ms", 1500000)
    assert convert.setting_field("filesViewMode", "grid") == ("files_view_mode", "GRID")


def test_preset_names() -> None:
    assert convert.preset_timer_name(5 * 60000) == "5 min timer"
    assert convert.preset_timer_name(60 * 60000) == "1 h timer"
    assert convert.preset_label(60 * 60000) == "1h"
    assert convert.preset_label(15 * 60000) == "15m"


def test_local_timezone_is_a_real_zone() -> None:
    from zoneinfo import ZoneInfo

    assert ZoneInfo(convert.local_timezone_name())
