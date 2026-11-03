import threading
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest

from productivity_app.desktop.ui.actions import Action
from productivity_app.desktop.ui.application_ui import ApplicationUi
from productivity_app.desktop.window import ShellWindow
from productivity_app.schemas.tasks import CreateTask


def settle(ui):
    for _ in range(3000):
        QTest.qWait(10)
        if not ui.session.runner.busy:
            QTest.qWait(10)
            if not ui.session.runner.busy:
                return
    raise AssertionError("UI queue did not drain")


def make_ui(application, qapplication, tmp_path):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapplication.processEvents()
    window = ShellWindow(lambda: True, lambda: None)
    controller = SimpleNamespace(window=window, _hotkey_manager=None, _startup_coordinator=None)
    ui = ApplicationUi(application, controller, application._config.paths.data_dir)
    settle(ui)
    return ui


def test_real_note_save_and_task_command(application, qapplication, tmp_path):
    ui = make_ui(application, qapplication, tmp_path)
    try:
        note = ui.presenters["notes"]
        note.view.fields["title"].setText("UI saved note")
        note.view.fields["content"].setPlainText("Plain content")
        note.save()
        settle(ui)
        assert note.view.current.title == "UI saved note"
        assert application.notes.get_note(note.view.current.id).content == "Plain content"
        ui.execute('new task "UI task"')
        settle(ui)
        assert application.tasks.list_tasks()[0].title == "UI task"
        assert ui.palette.outputs[-1].startswith("Committed")
    finally:
        settle(ui)
        ui.close()
        ui.window.hide()


def test_cli_timer_is_ready_focus_manual_and_search(application, qapplication, tmp_path):
    ui = make_ui(application, qapplication, tmp_path)
    try:
        ui.execute("timer new 25m")
        settle(ui)
        timer = application.timers.list_timers()[0]
        assert timer.state == "READY" and timer.planned_duration_ms == 1500000
        ui.execute("focus sequence new")
        settle(ui)
        sequence = application.focus.list_sequences()[0]
        detail = application.focus.get_sequence(sequence.id)
        assert detail.current_phase_index == 0 and detail.current_session.timer.state == "READY"
        ui.execute('search "Focus"')
        settle(ui)
        assert "TIMER" in ui.palette.outputs[-1]
        ui.execute('search like "Focus"')
        settle(ui)
        assert "TIMER" in ui.palette.outputs[-1]
    finally:
        settle(ui)
        ui.close()
        ui.window.hide()


def test_shutdown_drains_executing_mutation_rejects_new_work(final_ui):
    ui = final_ui
    entered, release = threading.Event(), threading.Event()
    callbacks = []

    def mutation():
        entered.set()
        assert release.wait(5)
        return ui.session.application.tasks.create_task(CreateTask(title="Durable before quit"))

    ui.controller.request_quit = lambda: callbacks.append(ui.prepare_quit())
    ui.session.run(mutation, "quit:mutation", lambda value, error: None)
    assert entered.wait(5)
    assert not ui.prepare_quit()
    assert not ui.session.runner.accepting and ui.transition
    ui.execute('new task "Must be rejected"')
    release.set()
    settle(ui)
    assert callbacks == [True]
    assert [row.title for row in ui.session.application.tasks.list_tasks()] == [
        "Durable before quit"
    ]
    ui.resume_after_rejected_close()
    assert ui.session.runner.accepting and not ui.transition


def test_all_dispatched_service_work_runs_off_gui_thread(final_ui, monkeypatch):
    ui = final_ui
    gui_thread = threading.get_ident()
    original = ui.session.application.tasks.create_task
    threads = []

    def recorded(command):
        threads.append(threading.get_ident())
        return original(command)

    monkeypatch.setattr(ui.session.application.tasks, "create_task", recorded)
    ui.execute('new task "Off GUI"')
    settle(ui)
    assert threads and all(value != gui_thread for value in threads)
    assert ui.session.runner.pool.maxThreadCount() == 1


def test_quit_dirty_cancel_keeps_draft_and_hide_is_not_quit(final_ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    p = final_ui.presenters["notes"]
    p.view.fields["content"].setPlainText("Uncommitted draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    assert not final_ui.prepare_quit()
    assert final_ui.session.runner.accepting
    final_ui.window.hide()
    assert p.view.text("content") == "Uncommitted draft" and p.view.dirty
    assert not final_ui.session.application.is_closed


def test_domain_actions_rejected_after_quiescing(final_ui):
    final_ui.session.runner.quiesce()
    results = []
    import pytest

    with pytest.raises(RuntimeError, match="closing or restoring"):
        final_ui.session.call(
            Action("tasks.create_task", (CreateTask(title="Blocked"),)),
            "closed",
            lambda value, error: results.append(value),
        )
    assert final_ui.session.application.tasks.list_tasks() == () and results == []
    final_ui.session.runner.resume()
