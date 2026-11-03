import ast
from pathlib import Path

from productivity_app.desktop.ui.command_parser import GRAMMAR, help_text


def test_ui_has_no_backend_bypass_or_execution_delivery_infrastructure():
    root = Path(__file__).resolve().parents[2] / "src/productivity_app/desktop/ui"
    banned = (
        "sqlalchemy",
        "sqlite3",
        "productivity_app.db",
        "productivity_app.repositories",
        "productivity_app.services",
        "subprocess",
    )
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(banned) for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(banned), path
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in {"eval", "exec", "compile"}, path
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {
                        "system",
                        "showMessage",
                        "execute_sql",
                        "create_all",
                        "drop_all",
                    }, path
        source = path.read_text()
        assert "while True" not in source
        assert "rebuild_full_text_index" not in source
    assert len(help_text().splitlines()) == len(GRAMMAR)


def test_layout_edit_rejects_domain_mutations_even_through_cli(final_ui):
    ui = final_ui
    ui.workspace.is_compact = False
    ui.workspace.set_editing(True)
    before = ui.session.application.tasks.list_tasks()
    ui.execute('new task "Blocked"')
    assert ui.session.application.tasks.list_tasks() == before
    assert "Finish layout editing" in ui.palette.outputs[-1]


def test_input_sheet_is_nonmodal_preserves_invalid_input_and_escape(qapplication):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget

    from productivity_app.desktop.ui.prompts import ask, choose

    parent = QWidget()
    parent.resize(400, 250)
    parent.show()
    values = []

    def accept(value):
        number = int(value)
        values.append(number)

    ask(parent, "Position", "Integer", "invalid", accept, integer=True)
    sheet = parent.input_sheet
    assert not sheet.isWindow() and parent.isEnabled()
    sheet.submit()
    assert sheet.isVisible() and sheet.input.text() == "invalid"
    assert sheet.status.text() and not values
    QTest.keyClick(sheet.input, Qt.Key.Key_Escape)
    assert sheet.isHidden() and parent.isVisible()
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    assert not sheet.eventFilter(
        parent,
        QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    ask(parent, "Position", "Integer", "3", accept)
    parent.input_sheet.submit()
    assert values == [3] and parent.input_sheet.isHidden()
    choose(parent, "Action", "Choose", ["One", "Two"], values.append)
    parent.input_menu.actions()[1].trigger()
    assert values == [3, "Two"]
    parent.hide()
    parent.deleteLater()
    qapplication.processEvents()


def test_nested_input_sheet_survives_first_sheet_submission(qapplication):
    from PySide6.QtWidgets import QWidget

    from productivity_app.desktop.ui.prompts import ask

    parent = QWidget()
    parent.show()
    values = []
    ask(
        parent,
        "Name",
        "Name",
        "Preset",
        lambda name: ask(
            parent,
            "Duration",
            "Milliseconds",
            "1000",
            lambda duration: values.append((name, duration)),
        ),
    )
    first = parent.input_sheet
    first.submit()
    assert parent.input_sheet is not first and parent.input_sheet.isVisible()
    parent.input_sheet.submit()
    assert values == [("Preset", "1000")]
    parent.hide()
    parent.deleteLater()
    qapplication.processEvents()


def test_runtime_rebind_rejects_obsolete_input_sheet_action(final_ui):
    from productivity_app.desktop.ui.prompts import ask

    values = []
    parent = final_ui.presenters["notes"].view
    ask(parent, "Old runtime", "Name", "Obsolete", values.append)
    sheet = parent.input_sheet
    final_ui.session.rebind()
    assert sheet.isHidden() and not sheet.active
    sheet.submit()
    assert values == []


def test_runtime_rebind_dismisses_nonmodal_filter_snapshot(final_ui):
    p = final_ui.presenters["notes"]
    p.filters()
    dialog = p.filter_dialog
    assert dialog.isVisible() and not dialog.isModal()
    final_ui.session.rebind()
    assert dialog.isHidden() and p.filter_dialog is None


def test_rejected_refresh_when_queue_is_full_is_inline_not_unhandled(final_ui):
    runner = final_ui.session.runner
    capacity = runner.capacity
    runner.capacity = 0
    try:
        final_ui.refresh()
        assert "Too many pending" in final_ui.timer_label.text()
        assert "Too many pending" in final_ui.files.status.text()
        assert "Too many pending" in final_ui.preferences_button.toolTip()
        assert not runner.busy
    finally:
        runner.capacity = capacity


def test_rejected_read_does_not_unlock_a_pending_record_mutation(final_ui):
    from productivity_app.desktop.ui.actions import Action

    p = final_ui.presenters["notes"]
    runner = final_ui.session.runner
    capacity = runner.capacity
    p.set_pending(True)
    runner.capacity = 0
    try:
        p.request(Action("notes.list_notes"), "list", p._listed, read=True)
        assert p.pending and not p.view.button_area.isEnabled()
    finally:
        runner.capacity = capacity
        p.set_pending(False)


def test_secondary_input_kept_on_async_failure_and_retry_is_explicit(final_ui):
    from productivity_app.desktop.ui.actions import Action
    from productivity_app.desktop.ui.prompts import ask
    from productivity_app.schemas.folders import CreateFolder
    from tests.desktop.test_final_ui_integration import settle

    ui, app = final_ui, final_ui.session.application
    app.folders.create_folder(CreateFolder(name="Already exists"))
    settle(ui)
    ui.workspace.maximize("files")
    ui.window.show()
    settle(ui)
    parent = ui.files.panel.content
    ask(
        parent,
        "Folder",
        "Name",
        "Already exists",
        lambda name: ui.files.submit(Action("folders.create_folder", (CreateFolder(name=name),))),
    )
    sheet = parent.input_sheet
    sheet.submit()
    settle(ui)
    assert sheet.isVisible() and sheet.active
    assert sheet.input.text() == "Already exists" and sheet.status.text()
    assert len(app.folders.list_children()) == 1
    sheet.input.setText("Explicit retry")
    sheet.submit()
    settle(ui)
    assert sheet.isHidden() and not sheet.active
    assert {row.name for row in app.folders.list_children()} == {"Already exists", "Explicit retry"}
