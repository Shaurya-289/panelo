import pytest
from PySide6.QtWidgets import QMessageBox

from productivity_app.desktop.ui.preferences import PreferencesDialog
from productivity_app.schemas.tasks import CreateTask
from tests.desktop.test_final_ui_integration import settle


def test_preferences_save_backup_and_restore_runtime_rebinding(final_ui, monkeypatch):
    ui, app = final_ui, final_ui.session.application
    window_id = int(ui.window.winId())
    identities = {key: id(panel) for key, panel in ui.workspace.panels.items()}
    preferences = PreferencesDialog(ui)
    settle(ui)
    preferences.durations["default_timer_duration_ms"].setValue(30000)
    preferences.save()
    settle(ui)
    assert app.settings.get_settings().default_timer_duration_ms == 30000
    task = app.tasks.create_task(CreateTask(title="Backed up"))
    settle(ui)
    preferences.create_backup()
    settle(ui)
    assert preferences.backups.count() == 1
    preferences.backups.setCurrentRow(0)
    app.tasks.create_task(CreateTask(title="Not backed up"))
    settle(ui)
    old_service = app.tasks
    generation = ui.session.state.runtime_generation
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    preferences.restore()
    settle(ui)
    assert not ui.transition and ui.session.runner.accepting
    assert app.tasks is not old_service
    assert ui.session.state.runtime_generation > generation
    assert [row.id for row in app.tasks.list_tasks()] == [task.id]
    assert int(ui.window.winId()) == window_id
    assert {key: id(panel) for key, panel in ui.workspace.panels.items()} == identities
    assert len(ui.session.subscriptions) > 0
    preferences.hide()


def test_failed_restore_keeps_current_selection_and_domain_usable(final_ui, monkeypatch):
    preferences = PreferencesDialog(final_ui)
    settle(final_ui)
    row = final_ui.session.application.tasks.create_task(CreateTask(title="Keep"))
    settle(final_ui)
    final_ui.presenters["tasks"].view.load(row)
    preferences.source.setText(str(final_ui.data_dir / "not-a-backup.sqlite3"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    preferences.restore()
    settle(final_ui)
    assert final_ui.presenters["tasks"].view.current.id == row.id
    assert not final_ui.transition and final_ui.session.runner.accepting
    assert (
        "not" in preferences.status.text().lower() or "exist" in preferences.status.text().lower()
    )


@pytest.mark.parametrize("fatal", [False, True])
def test_recovered_or_fatal_restore_respects_runtime_and_ui_state(final_ui, monkeypatch, fatal):
    ui, app = final_ui, final_ui.session.application
    preferences = PreferencesDialog(ui)
    settle(ui)
    preferences.create_backup()
    settle(ui)
    preferences.backups.setCurrentRow(0)
    task = app.tasks.create_task(CreateTask(title="Original before failed replacement"))
    settle(ui)
    old_service = app.tasks
    old_generation = ui.session.state.runtime_generation
    original = type(app)._install
    attempts = []

    def fail_install(self, other):
        attempts.append(other)
        if fatal or len(attempts) == 1:
            other.engine.dispose()
            raise RuntimeError("Injected runtime install failure")
        original(self, other)

    monkeypatch.setattr(type(app), "_install", fail_install)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    preferences.restore()
    settle(ui)
    assert len(attempts) == 2
    if fatal:
        assert app.is_closed and ui.session.closed and ui.transition
        assert not ui.workspace.isEnabled() and not ui.session.runner.accepting
    else:
        assert not app.is_closed and app.tasks is not old_service
        assert ui.session.state.runtime_generation > old_generation
        assert app.tasks.get_task(task.id).title == task.title
        assert ui.session.runner.accepting and not ui.transition
        assert "recovered" in preferences.status.text().lower()


def test_restore_preflight_failure_does_not_clear_still_valid_selection(final_ui, monkeypatch):
    ui, app = final_ui, final_ui.session.application
    preferences = PreferencesDialog(ui)
    settle(ui)
    preferences.create_backup()
    settle(ui)
    preferences.backups.setCurrentRow(0)
    row = app.tasks.create_task(CreateTask(title="Still authoritative"))
    settle(ui)
    ui.presenters["tasks"].view.load(row)
    old_service, old_generation = app.tasks, ui.session.state.runtime_generation

    def fail_safety(*args):
        raise RuntimeError("Injected safety-copy preflight failure")

    monkeypatch.setattr(app.backups, "_create_backup", fail_safety)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    preferences.restore()
    settle(ui)
    assert app.tasks is old_service and ui.session.state.runtime_generation == old_generation
    assert ui.presenters["tasks"].view.current.id == row.id
    assert "preflight" in preferences.status.text().lower()
    assert ui.session.runner.accepting and not ui.transition


def test_async_hotkey_settings_success_and_stale_failure_keep_one_binding(final_ui, qapplication):
    from productivity_app.core.exceptions import StaleVersionError
    from productivity_app.desktop.hotkey import HotkeyManager
    from tests.desktop.test_desktop_pass2 import _HotkeyBackend

    ui, app = final_ui, final_ui.session.application
    backend = _HotkeyBackend()
    manager = HotkeyManager(qapplication, ui.window, backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    ui.controller._hotkey_manager = manager
    results = []
    try:
        version = app.settings.get_settings().version
        hwnd = int(ui.window.winId())
        ui.change_hotkey("Ctrl+Shift+F1", version, lambda value, error: results.append(error))
        settle(ui)
        assert results == [None]
        assert app.settings.get_settings().global_hotkey == "Ctrl+Shift+F1"
        assert manager.active_hotkey.canonical == "Ctrl+Shift+F1"
        assert len(backend.registered) == 1 and int(ui.window.winId()) == hwnd
        ui.change_hotkey("Ctrl+Alt+F2", version, lambda value, error: results.append(error))
        settle(ui)
        assert isinstance(results[-1], StaleVersionError)
        assert app.settings.get_settings().global_hotkey == "Ctrl+Shift+F1"
        assert manager.active_hotkey.canonical == "Ctrl+Shift+F1"
        assert len(backend.registered) == 1 and int(ui.window.winId()) == hwnd
    finally:
        manager.close()


def test_ui_restore_preserves_one_real_shell_resource_set(final_ui, qapplication, monkeypatch):
    from productivity_app.desktop.controller import ShellController
    from productivity_app.desktop.hotkey import HotkeyManager
    from tests.desktop.test_desktop_pass2 import _HotkeyBackend
    from tests.desktop.test_desktop_pass3_integration import _Mutex, _Tray

    ui, app = final_ui, final_ui.session.application
    tray, lease, backend = _Tray(), _Mutex(), _HotkeyBackend()
    controller = ShellController(app, qapplication, lease, window=ui.window, tray=tray)
    manager = HotkeyManager(qapplication, ui.window, backend, controller.toggle_window)
    controller._hotkey_manager = manager
    controller.ui, ui.controller = ui, controller
    preferences = PreferencesDialog(ui)
    settle(ui)
    try:
        controller.start(start_hidden=False)
        settle(ui)
        resources = (
            controller.window,
            controller.tray,
            controller._hotkey_manager,
            controller._mutex,
        )
        hwnd = int(ui.window.winId())
        registrations = backend.register_calls.copy()
        preferences.create_backup()
        settle(ui)
        preferences.backups.setCurrentRow(0)
        app.tasks.create_task(CreateTask(title="Discarded by restore"))
        settle(ui)
        old = app.tasks
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        preferences.restore()
        settle(ui)
        assert app.tasks is not old and app.tasks.list_tasks() == ()
        assert resources == (
            controller.window,
            controller.tray,
            controller._hotkey_manager,
            controller._mutex,
        )
        assert backend.register_calls == registrations and len(backend.registered) == 1
        assert int(ui.window.winId()) == hwnd and tray.close_count == lease.close_count == 0
        for _ in range(3):
            controller.toggle_window()
            controller.activate_window()
            controller.show_window()
            settle(ui)
            assert int(ui.window.winId()) == hwnd
        assert tray.show_count == 1 and backend.register_calls == registrations
    finally:
        preferences.hide()
        controller._close_shell_resources()
        controller.deleteLater()
