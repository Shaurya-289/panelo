from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

from productivity_app.desktop.ui.panel import DashboardPanel
from productivity_app.desktop.ui.panels import NotesPanel, RemindersPanel, TimersPanel, TodoPanel
from productivity_app.desktop.ui.theme import apply_dashboard_theme


def test_panel_owns_named_header_content_and_empty_control_slot(qapplication: QApplication) -> None:
    panel = DashboardPanel("notes", "Notes")
    apply_dashboard_theme(panel)
    content = QLabel("Local preview")
    panel.set_content(content)
    assert panel.panel_id == "notes"
    assert panel.accessibleName() == "Notes"
    assert panel.title_label.text() == "NOTES"
    assert panel.control_layout.count() == 0
    assert panel.content_layout.itemAt(0).widget() is content
    assert content.parentWidget() is panel.content
    panel.set_compact(True)
    assert panel.is_compact
    panel.set_compact(False)
    assert not panel.is_compact
    panel.set_content(QLabel("Replacement"))
    assert panel.content_layout.count() == 1
    panel.deleteLater()


def test_preview_panels_are_static_read_only_and_share_base(qapplication: QApplication) -> None:
    panels = (NotesPanel(), TodoPanel(), TimersPanel(), RemindersPanel())
    assert tuple(panel.panel_id for panel in panels) == ("notes", "todo", "timers", "reminders")
    assert all(isinstance(panel, DashboardPanel) for panel in panels)
    notes, todo, timer, reminders = panels
    assert isinstance(notes, NotesPanel) and notes.search.isReadOnly()
    assert len(notes.rows) == 3
    assert all(not item.isEnabled() for item in todo.findChildren(QCheckBox))
    assert isinstance(timer, TimersPanel)
    assert timer.display.text() == "25:00" and timer.state_label.text() == "Ready"
    assert isinstance(reminders, RemindersPanel) and len(reminders.rows) == 2
    for panel in panels:
        assert panel.control_layout.count() == 0
        panel.deleteLater()
