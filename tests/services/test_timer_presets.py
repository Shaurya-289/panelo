from __future__ import annotations

import pytest

from productivity_app.core.exceptions import DuplicateNameError
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    MoveTimerPreset,
    UpdateTimerPreset,
)


def test_preset_duration_is_snapshotted(application) -> None:
    preset = application.timers.create_preset(
        CreateTimerPreset(name="Focus Duration", duration_ms=25_000)
    )
    timer = application.timers.create_timer(CreateTimer(name="Work", preset_id=preset.id))
    changed = application.timers.update_preset(
        preset.id, UpdateTimerPreset(expected_version=preset.version, duration_ms=30_000)
    )
    assert timer.planned_duration_ms == 25_000
    assert changed.duration_ms == 30_000


def test_preset_normalized_name_is_unique(application) -> None:
    application.timers.create_preset(CreateTimerPreset(name="Focus Time", duration_ms=1_000))
    with pytest.raises(DuplicateNameError):
        application.timers.create_preset(
            CreateTimerPreset(name=" focus   time ", duration_ms=2_000)
        )


def test_preset_reordering_is_dense_and_collision_safe(application) -> None:
    first = application.timers.create_preset(CreateTimerPreset(name="First", duration_ms=1_000))
    second = application.timers.create_preset(CreateTimerPreset(name="Second", duration_ms=2_000))
    moved = application.timers.move_preset(
        second.id, MoveTimerPreset(expected_version=second.version, position=0)
    )
    assert moved.sort_order == 0
    assert [preset.id for preset in application.timers.list_presets()] == [second.id, first.id]
