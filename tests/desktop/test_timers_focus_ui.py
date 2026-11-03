from datetime import timedelta

import pytest
from PySide6.QtWidgets import QInputDialog

from productivity_app.schemas.tasks import CreateTask
from productivity_app.schemas.timers import CreateTimer, CreateTimerPreset
from tests.desktop.test_final_ui_integration import settle

pytestmark = pytest.mark.usefixtures("scripted_inputs")


def test_cli_sequence_cancel_reports_terminal_state_without_inviting_start(final_ui):
    ui, app = final_ui, final_ui.session.application
    ui.execute("focus sequence new")
    settle(ui)
    sequence = app.focus.list_sequences()[0]
    ui.execute("focus cancel")
    settle(ui)
    assert app.focus.get_sequence(sequence.id).state == "CANCELLED"
    assert "Terminal sequence" in ui.presenters["timers"].view.status.text()
    assert "no timer was started" in ui.palette.outputs[-1]
    assert app.timers.get_active_timer() is None


def test_timer_lifecycle_and_ready_survives_failed_start(final_ui, clock):
    app = final_ui.session.application
    row = app.timers.create_timer(CreateTimer(name="Timer", planned_duration_ms=10000))
    settle(final_ui)
    presenter = final_ui.presenters["timers"]
    presenter.view.load(row)
    for action, state in (
        ("start_timer", "RUNNING"),
        ("pause_timer", "PAUSED"),
        ("resume_timer", "RUNNING"),
        ("complete_timer", "FINISHED"),
        ("reset_timer", "READY"),
        ("start_timer", "RUNNING"),
        ("cancel_timer", "CANCELLED"),
        ("reset_timer", "READY"),
        ("delete_timer", "READY"),
        ("restore_timer", "READY"),
    ):
        presenter.mutate(action)
        settle(final_ui)
        assert presenter.view.current.state == state
    presenter.mutate("start_timer")
    settle(final_ui)
    active_id = presenter.view.current.id
    presenter.view.load(values={"name": "Waiting", "planned_duration_ms": "3000"})
    presenter.save(start_after=True)
    settle(final_ui)
    assert presenter.view.current.state == "READY"
    assert "READY timer created" in presenter.view.status.text()
    assert app.timers.get_active_timer().id == active_id
    presenter.mutate("history")
    settle(final_ui)
    assert "No history" in presenter.view.status.text()


def test_focus_exact_eight_phases_explicit_and_task_independent(final_ui, clock):
    app = final_ui.session.application
    task = app.tasks.create_task(CreateTask(title="Focus task"))
    settle(final_ui)
    final_ui.execute(f"focus sequence new task {task.id}")
    settle(final_ui)
    presenter = final_ui.presenters["timers"]
    sequence_id = app.focus.list_sequences()[0].id
    expected = (
        "FOCUS",
        "SHORT_BREAK",
        "FOCUS",
        "SHORT_BREAK",
        "FOCUS",
        "SHORT_BREAK",
        "FOCUS",
        "LONG_BREAK",
    )
    for index, kind in enumerate(expected):
        sequence = app.focus.get_sequence(sequence_id)
        timer = sequence.current_session.timer
        assert sequence.current_phase_index == index
        assert sequence.current_session.phase_kind == kind and timer.state == "READY"
        assert timer.task_id == (task.id if kind == "FOCUS" else None)
        presenter.view.load(timer)
        presenter.mutate("start_timer")
        settle(final_ui)
        clock.now += timedelta(milliseconds=500)
        presenter.mutate("complete_timer")
        settle(final_ui)
        assert app.focus.get_sequence(sequence_id).current_phase_index == index
        final_ui.execute("focus next")
        settle(final_ui)
    assert app.focus.get_sequence(sequence_id).state == "COMPLETED"
    assert app.tasks.get_task(task.id).version == task.version
    assert app.tasks.get_task(task.id).status == "TODO"
    assert app.timers.get_active_timer() is None


def test_preset_create_dialog_and_duration_snapshot(final_ui, monkeypatch):
    presenter = final_ui.presenters["timers"]
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Create", True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("UI preset", True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *args, **kwargs: (3000, True))
    presenter.preset_dialog()
    settle(final_ui)
    preset = next(
        p for p in final_ui.session.application.timers.list_presets() if p.name == "UI preset"
    )
    presenter.view.load(values={"name": "From preset", "preset_id": preset.id})
    presenter.save()
    settle(final_ui)
    assert (
        presenter.view.current.planned_duration_ms == 3000
        and presenter.view.current.preset_id == preset.id
    )


def test_preset_edit_order_delete_restore_and_deleted_apply_rejection(final_ui, monkeypatch):
    app = final_ui.session.application
    preset = app.timers.create_preset(CreateTimerPreset(name="Manage", duration_ms=3000))
    settle(final_ui)
    p = final_ui.presenters["timers"]
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Renamed preset", True))
    for verb in ("Rename", "Duration", "Move", "Delete", "Apply", "Restore"):

        def choose(*args, verb=verb, **kwargs):
            if args[2] == "Choose":
                return next(value for value in args[3] if preset.id in value), True
            return verb, True

        monkeypatch.setattr(QInputDialog, "getItem", choose)
        monkeypatch.setattr(
            QInputDialog,
            "getInt",
            lambda *args, verb=verb, **kwargs: (4000 if verb == "Duration" else 0, True),
        )
        p.preset_dialog()
        settle(final_ui)
        if verb == "Apply":
            assert "Deleted presets" in p.view.status.text()
    current = app.timers.get_preset(preset.id)
    assert current.name == "Renamed preset" and current.duration_ms == 4000
    assert current.deleted_at_utc is None and current.sort_order == 0
