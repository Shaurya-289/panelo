"""Non-modal menus and transient in-panel inputs, never native overlay windows."""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget

from productivity_app.desktop.ui.session import error_text


class InputSheet(QFrame):
    def __init__(
        self, parent: QWidget, title: str, label: str, initial: str, callback: Callable[[str], Any]
    ) -> None:
        super().__init__(parent)
        self.setObjectName("inputSheet")
        self.callback = callback
        self.active = True
        self.submitting = False
        layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)
        self.input = QLineEdit(initial)
        self.input.setAccessibleName(label)
        self.input.setPlaceholderText(label)
        layout.addWidget(self.input)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for text, action in (("Apply", self.submit), ("Cancel", self.cancel)):
            button = QPushButton(text)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addStretch()
        self.input.returnPressed.connect(self.submit)
        self.input.installEventFilter(self)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.input.setFocus()

    def submit(self) -> None:
        if not self.active:
            return
        text = self.input.text()
        self.hide()
        self.submitting = True
        try:
            self.callback(text)
        except Exception as exc:
            self.status.setText(error_text(exc))
            self.show()
            return
        finally:
            self.submitting = False
        self.active = False

    def cancel(self) -> None:
        self.active = False
        self.hide()
        parent = self.parentWidget()
        if parent:
            parent.setFocus()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        parent = self.parentWidget()
        if parent and watched is parent and event.type() == QEvent.Type.Resize:
            self.setGeometry(parent.rect())
        if (
            self.isVisible()
            and self.active
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress:
                self.cancel()
                return True
        return super().eventFilter(watched, event)


def ask(
    parent: QWidget,
    title: str,
    label: str,
    initial: str,
    callback: Callable[[str], Any],
    *,
    integer: bool = False,
) -> None:
    # Integer parsing/validation belongs to the caller's existing public schema.
    previous = getattr(parent, "input_sheet", None)
    if previous is not None:
        previous.hide()
        previous.deleteLater()
    parent.input_sheet = InputSheet(parent, title, label, initial, callback)  # type: ignore[attr-defined]


def choose(
    parent: QWidget, title: str, label: str, choices: list[str], callback: Callable[[str], Any]
) -> None:
    previous = getattr(parent, "input_menu", None)
    if previous is not None:
        previous.hide()
        previous.deleteLater()
    menu = QMenu(title, parent)
    menu.setAccessibleName(label)
    for value in choices:
        action = menu.addAction(value)
        action.triggered.connect(lambda _checked=False, value=value: callback(value))
    parent.input_menu = menu  # type: ignore[attr-defined]
    menu.popup(parent.mapToGlobal(parent.rect().center()))


def clear(parent: QWidget) -> None:
    """Discard transient input actions carrying obsolete selection/version data."""
    for sheet in parent.findChildren(InputSheet):
        sheet.cancel()
    for widget in [parent, *parent.findChildren(QWidget)]:
        menu = getattr(widget, "input_menu", None)
        if menu is not None:
            menu.hide()
            menu.clear()


def pending(parent: QWidget) -> InputSheet | None:
    sheet = getattr(parent, "input_sheet", None)
    return sheet if isinstance(sheet, InputSheet) and sheet.submitting else None


def finish(sheet: InputSheet | None, error: Exception | None) -> None:
    """A failed async write restores its input, never a superseded input sheet."""
    if sheet is None:
        return
    try:
        parent = sheet.parentWidget()
        if parent is None or id(getattr(parent, "input_sheet", None)) != id(sheet):
            return
        if error:
            sheet.active = True
            sheet.status.setText(error_text(error))
            sheet.show()
            sheet.raise_()
            sheet.input.setFocus()
        else:
            sheet.cancel()
    except RuntimeError:
        # The owner may have deliberately replaced/deleted the transient sheet.
        return
