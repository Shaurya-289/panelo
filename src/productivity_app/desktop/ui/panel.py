"""Independent panel chrome; placement belongs to the workspace."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from productivity_app.desktop.ui.theme import PANEL_PADDING


class DashboardPanel(QFrame):
    expand_requested = Signal(str)
    options_requested = Signal(str)

    def __init__(self, panel_id: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.panel_id = panel_id
        self.is_compact = False
        self.swap_control: QToolButton | None = None
        self.setObjectName("dashboardPanel")
        self.setAccessibleName(title)
        self.setMinimumHeight(160)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PANEL_PADDING, PANEL_PADDING, PANEL_PADDING, PANEL_PADDING)
        layout.setSpacing(12)
        self.header = QWidget(self)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title.upper(), self.header)
        self.title_label.setProperty("role", "panelTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        self.controls = QWidget(self.header)
        self.control_layout = QHBoxLayout(self.controls)
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self.controls)
        layout.addWidget(self.header)
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.content, 1)

    def set_content(self, widget: QWidget) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            assert item is not None
            previous = item.widget()
            if previous is not None:
                previous.setParent(None)
                previous.deleteLater()
        self.content_layout.addWidget(widget)

    def set_compact(self, enabled: bool) -> None:
        self.is_compact = enabled
