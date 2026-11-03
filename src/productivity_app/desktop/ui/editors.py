"""Plain-text forms and explicit timezone conversion; no domain persistence."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def local_instant(text: str, zone: str, fold: str = "") -> datetime | None:
    if not text.strip():
        return None
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    tz = ZoneInfo(zone)
    candidates = [parsed.replace(tzinfo=tz, fold=f) for f in (0, 1)]
    valid = [
        v for v in candidates if v.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == parsed
    ]
    if not valid:
        raise ValueError(
            "This local time does not exist. Choose another time or enter an explicit offset."
        )
    if len({v.utcoffset() for v in valid}) > 1 and fold not in {"0", "1"}:
        raise ValueError("Ambiguous local time: select occurrence 0/1 or enter an explicit offset.")
    return parsed.replace(tzinfo=tz, fold=int(fold or "0")).astimezone(UTC)


class InlineStatus(QLabel):
    """Keep an empty status out of the layout; errors remain immediately visible."""

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setVisible(bool(text))


class RecordView(QWidget):
    intent = Signal(str)
    selected = Signal(str)
    search_requested = Signal()
    completion_requested = Signal(str, bool)

    def __init__(
        self, fields: tuple[str, ...], modes: tuple[str, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.dirty = False
        self.after_save: Callable[[], None] | None = None
        self.loading = False
        self.current: Any = None
        self.expanded = False
        self.fields: dict[str, QLineEdit | QPlainTextEdit | QComboBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.search_bar = QWidget()
        top = QHBoxLayout(self.search_bar)
        top.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search records")
        self.search.returnPressed.connect(self.search_requested.emit)
        self.mode = QComboBox(self)
        self.mode.addItems(modes)
        self.mode.currentTextChanged.connect(lambda _value: self.search_requested.emit())
        self.mode.hide()
        top.addWidget(self.search)
        layout.addWidget(self.search_bar)
        self.search_bar.hide()
        self.records = QListWidget()
        self.records.itemChanged.connect(self._completion)
        self.task_rows: dict[str, Any] = {}
        self.records.itemClicked.connect(
            lambda item: self.selected.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.records.itemActivated.connect(
            lambda item: self.selected.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        )
        layout.addWidget(self.records, 1)
        self.editor_area = QScrollArea()
        self.editor_area.setWidgetResizable(True)
        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        self.details_area = QWidget()
        details = QFormLayout(self.details_area)
        details.setContentsMargins(0, 8, 0, 0)
        primary = {"title", "name", "content", "description", "message", "planned_duration_ms"}
        if "scheduled_timezone" in fields:
            primary.add("due_at_utc")
        labels = {
            "folder_id": "Folder",
            "task_id": "Task",
            "note_id": "Note",
            "preset_id": "Preset",
            "start_at_utc": "Starts",
            "due_at_utc": "Due",
            "planned_duration_ms": "Duration (ms)",
            "scheduled_timezone": "Timezone",
            "fold": "Time occurrence (0 / 1)",
        }
        for name in fields:
            control: QLineEdit | QPlainTextEdit | QComboBox
            if name in {"content", "description", "message"}:
                control = QPlainTextEdit()
                control.setMinimumHeight(100)
                control.textChanged.connect(self._edited)
            elif name in {"folder_id", "priority", "preset_id", "task_id", "note_id"}:
                control = QComboBox()
                if name == "priority":
                    for value in ("LOW", "NORMAL", "HIGH", "URGENT"):
                        control.addItem(value, value)
                else:
                    control.addItem("None", None)
                control.currentIndexChanged.connect(self._edited)
            else:
                control = QLineEdit()
                control.textEdited.connect(self._edited)
            control.setAccessibleName(name.replace("_", " "))
            self.fields[name] = control
            if name in {"title", "name"}:
                assert isinstance(control, QLineEdit)
                control.setPlaceholderText(name.title())
                control.setObjectName("recordTitle")
                form.addRow(control)
            elif name in {"content", "description", "message"}:
                assert isinstance(control, QPlainTextEdit)
                control.setPlaceholderText("Write a note…" if name == "content" else "Details…")
                control.setObjectName("recordBody")
                form.addRow(control)
            else:
                (form if name in primary else details).addRow(
                    labels.get(name, name.replace("_", " ").title()), control
                )
        self.tags = QListWidget()
        self.tags.setMaximumHeight(85)
        self.tags.itemChanged.connect(self._edited)
        if "folder_id" in fields:
            details.addRow("Tags", self.tags)
        form.addRow(self.details_area)
        self.details_area.hide()
        self.editor_area.setWidget(container)
        layout.addWidget(self.editor_area, 2)
        self.editor_area.hide()
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setAccessibleName("Selected record history")
        self.history.setMaximumHeight(180)
        self.history.hide()
        layout.addWidget(self.history)
        self.buttons: dict[str, QPushButton] = {}
        self.button_area = QWidget()
        self.button_layout = QHBoxLayout(self.button_area)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(4)
        self.menu_button = QToolButton(self.button_area)
        self.menu_button.setText("⋯")
        self.menu_button.setFixedSize(28, 28)
        self.menu_button.setAccessibleName("Panel options")
        self.menu_button.setToolTip("Options")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.menu_button)
        self.menu_button.setMenu(self.menu)
        self.search_action = self.menu.addAction("Search…")
        self.search_action.setCheckable(True)
        self.search_action.triggered.connect(self.toggle_search)
        self.views_menu = self.menu.addMenu("Show")
        for mode in modes:
            action = self.views_menu.addAction(mode)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, mode=mode: self.mode.setCurrentText(mode)
            )
        self.details_action = self.menu.addAction("Details / relationships")
        self.details_action.setCheckable(True)
        self.details_action.toggled.connect(self.details_area.setVisible)
        self.menu.addSeparator()
        self.menu_actions: dict[str, QAction] = {}
        self.menu.aboutToShow.connect(self.sync_menu)
        self.button_layout.addWidget(self.menu_button)
        layout.insertWidget(0, self.button_area)
        self.status = InlineStatus("Loading…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def add_button(self, name: str, label: str) -> None:
        button = QPushButton("+" if name == "new" else label, self.button_area)
        button.clicked.connect(lambda: self.intent.emit(name))
        self.buttons[name] = button
        if name == "new":
            button.setFixedSize(28, 28)
            button.setAccessibleName("Create new record")
            button.setToolTip("Create new")
            self.button_layout.insertWidget(0, button)
        elif name == "save":
            self.button_layout.insertWidget(1, button)
            button.hide()
        else:
            button.hide()
            action = self.menu.addAction(
                {"previous": "Previous page", "next": "Next page"}.get(name, label)
            )
            action.triggered.connect(button.click)
            self.menu_actions[name] = action

    def sync_menu(self) -> None:
        self.search_action.setChecked(not self.search_bar.isHidden())
        self.details_action.setVisible(self.expanded)
        for action in self.views_menu.actions():
            action.setChecked(action.text() == self.mode.currentText())
        for name, action in self.menu_actions.items():
            action.setEnabled(self.buttons[name].isEnabled())
            if name in {"previous", "next"}:
                action.setVisible(action.isEnabled())

    def toggle_search(self, enabled: bool = True) -> None:
        self.search_bar.setVisible(enabled)
        if enabled:
            self.search.setFocus()
        elif self.search.text():
            self.search.clear()
            self.search_requested.emit()

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.editor_area.setVisible(expanded)
        if "save" in self.buttons:
            self.buttons["save"].setVisible(expanded and (self.dirty or self.current is None))
        self.history.setVisible(expanded and bool(self.history.toPlainText()))

    def _completion(self, item: QListWidgetItem) -> None:
        identifier = str(item.data(Qt.ItemDataRole.UserRole))
        row = self.task_rows.get(identifier)
        if row is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        previous = row.status == "COMPLETED"
        if checked != previous:
            self.records.blockSignals(True)
            item.setCheckState(Qt.CheckState.Checked if previous else Qt.CheckState.Unchecked)
            self.records.blockSignals(False)
            self.completion_requested.emit(identifier, checked)

    def _edited(self, *_: Any) -> None:
        if not self.loading:
            self.dirty = True
            self.set_expanded(self.expanded)

    def text(self, name: str) -> str:
        control = self.fields[name]
        if isinstance(control, QPlainTextEdit):
            return control.toPlainText()
        if isinstance(control, QComboBox):
            return str(control.currentData() or "")
        return control.text()

    def values(self) -> dict[str, Any]:
        return {name: self.text(name) for name in self.fields}

    def tag_ids(self) -> tuple[str, ...]:
        return tuple(
            str(self.tags.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.tags.count())
            if self.tags.item(i).checkState() == Qt.CheckState.Checked
        )

    def set_reference(self, name: str, rows: tuple[Any, ...]) -> None:
        control = self.fields.get(name)
        if not isinstance(control, QComboBox):
            return
        selected = control.currentData()
        control.blockSignals(True)
        control.clear()
        control.addItem("None", None)
        for row in rows:
            control.addItem(getattr(row, "name", getattr(row, "title", "Untitled")), row.id)
        if selected and control.findData(selected) < 0:
            control.addItem(f"Historical link · {selected}", selected)
        control.setCurrentIndex(max(0, control.findData(selected)))
        control.blockSignals(False)

    def set_tags(self, rows: tuple[Any, ...]) -> None:
        checked = (
            set(self.current.tag_ids)
            if self.current is not None and not self.dirty and hasattr(self.current, "tag_ids")
            else set(self.tag_ids())
        )
        self.tags.blockSignals(True)
        self.tags.clear()
        for row in rows:
            item = QListWidgetItem(row.name, self.tags)
            item.setData(Qt.ItemDataRole.UserRole, row.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if row.id in checked else Qt.CheckState.Unchecked
            )
            if getattr(row, "deleted_at_utc", None) is not None and row.id not in checked:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.tags.blockSignals(False)

    def load(self, row: Any = None, values: dict[str, Any] | None = None) -> None:
        if row is None or self.current is None or self.current.id != row.id:
            self.history.clear()
            self.history.hide()
            self.details_action.setChecked(False)
        self.loading = True
        self.current = row
        data = values if values is not None else (row.model_dump() if row is not None else {})
        for name, control in self.fields.items():
            value = data.get(name)
            if isinstance(value, datetime):
                value = value.isoformat()
            if isinstance(control, QPlainTextEdit):
                control.setPlainText(str(value or ""))
            elif isinstance(control, QComboBox):
                index = control.findData(value)
                if index < 0 and value:
                    control.addItem(f"Historical link · {value}", value)
                    index = control.count() - 1
                control.setCurrentIndex(max(0, index))
            else:
                control.setText(str(value if value is not None else ""))
        for i in range(self.tags.count()):
            item = self.tags.item(i)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in data.get("tag_ids", ())
                else Qt.CheckState.Unchecked
            )
        self.loading = False
        self.dirty = False
        self.set_expanded(self.expanded)
        self.editor_area.setEnabled(row is None or getattr(row, "deleted_at_utc", None) is None)

    def permit_discard(self, after_save: Callable[[], None] | None = None) -> bool:
        if not self.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes before leaving?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Discard:
            self.dirty = False
            return True
        if choice == QMessageBox.StandardButton.Save:
            self.after_save = after_save
            self.intent.emit("save")
        return False

    def render_rows(self, rows: tuple[Any, ...]) -> None:
        selected = self.current.id if self.current is not None else None
        self.records.blockSignals(True)
        self.records.clear()
        self.task_rows.clear()
        for row in rows:
            title = getattr(row, "title", getattr(row, "name", row.id)) or "Untitled"
            state = getattr(row, "timing_state", None) or getattr(
                row, "effective_state", getattr(row, "status", "")
            )
            due = getattr(row, "due_at_utc", None)
            text = f"{title} · {state}" if state else title
            if due:
                text += f" · {due.astimezone().isoformat(timespec='minutes')}"
            item = QListWidgetItem(text, self.records)
            item.setData(Qt.ItemDataRole.UserRole, row.id)
            if (
                "priority" in self.fields
                and getattr(row, "status", None) in {"TODO", "IN_PROGRESS", "COMPLETED"}
                and getattr(row, "deleted_at_utc", None) is None
            ):
                self.task_rows[row.id] = row
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if row.status == "COMPLETED" else Qt.CheckState.Unchecked
                )
            if row.id == selected:
                self.records.setCurrentItem(item)
        self.status.setText("Nothing here yet · use + to add one" if not rows else "")
        self.records.blockSignals(False)
