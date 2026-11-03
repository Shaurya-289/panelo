"""In-window plain text command palette with bounded in-memory history."""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from productivity_app.desktop.ui.command_parser import KEYWORDS


class CommandPalette(QWidget):
    execute = Signal(str)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history: list[str] = []
        self.position = 0
        self.outputs: list[str] = []
        self.previous_focus: QWidget | None = None
        layout = QVBoxLayout(self)
        self.input = QLineEdit()
        self.input.setAccessibleName("Application command")
        self.input.setPlaceholderText('help · search "text" · timer new 25m')
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(140)
        self.output.setAccessibleName("Command results")
        layout.addWidget(self.input)
        layout.addWidget(self.output)
        self.input.returnPressed.connect(self.submit)
        self.input.installEventFilter(self)
        self.hide()

    def submit(self) -> None:
        text = self.input.text()
        if not text.strip():
            return
        self.history = (self.history + [text])[-100:]
        self.position = len(self.history)
        self.execute.emit(text)

    def report(self, text: str, success: bool = True) -> None:
        self.outputs = (self.outputs + [text])[-200:]
        self.output.setPlainText("\n".join(self.outputs))
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        if success:
            self.input.clear()

    def open(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.previous_focus = QApplication.focusWidget()
        self.show()
        self.input.setFocus()

    def dismiss(self) -> None:
        self.hide()
        try:
            if self.previous_focus and self.previous_focus.isVisible():
                self.previous_focus.setFocus()
        except RuntimeError:
            self.previous_focus = None
        self.closed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.dismiss()
                return True
            if event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
                self.position = max(
                    0,
                    min(
                        len(self.history),
                        self.position + (-1 if event.key() == Qt.Key.Key_Up else 1),
                    ),
                )
                self.input.setText(
                    self.history[self.position] if self.position < len(self.history) else ""
                )
                return True
            if event.key() == Qt.Key.Key_Tab:
                text = self.input.text()
                prefix = text.rsplit(" ", 1)[-1]
                choices = [word for word in KEYWORDS if word.startswith(prefix)]
                # Completion is fixed vocabulary only, never IDs, content or code.
                if len(choices) == 1 and '"' not in text:
                    self.input.setText(
                        text[: -len(prefix)] + choices[0] if prefix else text + choices[0]
                    )
                return True
        return super().eventFilter(watched, event)
