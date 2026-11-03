"""Small preview-only panels. These records never enter application services."""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QVBoxLayout, QWidget

from productivity_app.desktop.ui.panel import DashboardPanel


@dataclass(frozen=True, slots=True)
class NotePreview:
    title: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class TaskPreview:
    title: str
    completed: bool = False


@dataclass(frozen=True, slots=True)
class ReminderPreview:
    title: str
    due_text: str


@dataclass(frozen=True, slots=True)
class TimerPreview:
    display: str = "25:00"
    state: str = "Ready"


def _label(text: str, *, secondary: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if secondary:
        label.setProperty("role", "secondary")
    return label


class NotesPanel(DashboardPanel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("notes", "Notes", parent)
        self.search = QLineEdit(self.content)
        self.search.setPlaceholderText("Search notes")
        self.search.setReadOnly(True)
        self.search.setAccessibleName("Notes search preview, read only")
        self.search.setToolTip("Local preview — search is not connected yet")
        self.content_layout.addWidget(self.search)
        self.rows_widget = QWidget(self.content)
        self.content_layout.addWidget(self.rows_widget, 1)
        self.set_rows(
            (
                NotePreview(
                    "A little clarity", "Space for thoughts, plans, and things worth keeping."
                ),
                NotePreview("This week", "Keep the important things close. Let the rest wait."),
                NotePreview("Project ideas", "A quiet place to collect the next small step."),
            )
        )

    def set_rows(self, rows: tuple[NotePreview, ...]) -> None:
        self.rows = rows
        old = self.rows_widget
        self.rows_widget = QWidget(self.content)
        self.content_layout.replaceWidget(old, self.rows_widget)
        old.hide()
        old.deleteLater()
        layout = QVBoxLayout(self.rows_widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)
        for row in rows:
            layout.addWidget(_label(row.title))
            layout.addWidget(_label(row.excerpt, secondary=True))
        layout.addStretch()


class TodoPanel(DashboardPanel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("todo", "To-Do", parent)
        self.set_rows(
            (
                TaskPreview("Review study notes"),
                TaskPreview("Plan the next small step"),
                TaskPreview("Clear the desk", True),
            )
        )

    def set_rows(self, rows: tuple[TaskPreview, ...]) -> None:
        self.rows = rows
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        for row in rows:
            checkbox = QCheckBox(row.title, container)
            checkbox.setChecked(row.completed)
            checkbox.setEnabled(False)
            checkbox.setAccessibleName(f"{row.title}, read-only preview")
            layout.addWidget(checkbox)
        layout.addStretch()
        self.set_content(container)


class TimersPanel(DashboardPanel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("timers", "Timers", parent)
        self.display = _label("")
        self.display.setProperty("role", "timerDisplay")
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label = _label("", secondary=True)
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addStretch()
        self.content_layout.addWidget(self.display)
        self.content_layout.addWidget(self.state_label)
        self.content_layout.addStretch()
        self.set_preview(TimerPreview())

    def set_preview(self, preview: TimerPreview) -> None:
        self.preview = preview
        self.display.setText(preview.display)
        self.state_label.setText(preview.state)
        self.setAccessibleDescription("Static timer preview; no countdown is active")
        self.setToolTip("Local preview — timer controls are not connected yet")


class RemindersPanel(DashboardPanel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("reminders", "Reminders", parent)
        self.set_rows(
            (
                ReminderPreview("Take a moment", "Today · 18:00"),
                ReminderPreview("Review the week", "Friday · 09:00"),
            )
        )

    def set_rows(self, rows: tuple[ReminderPreview, ...]) -> None:
        self.rows = rows
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        for row in rows:
            layout.addWidget(_label(row.title))
            layout.addWidget(_label(row.due_text, secondary=True))
        layout.addStretch()
        self.set_content(container)
