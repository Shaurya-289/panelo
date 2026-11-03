"""An in-panel, local demo hierarchy; no filesystem or backend access."""

from dataclasses import dataclass
from functools import partial
from types import MappingProxyType

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from productivity_app.desktop.ui.panel import DashboardPanel


@dataclass(frozen=True, slots=True)
class DemoFolder:
    id: str
    name: str
    parent_id: str | None = None


DEMO_FOLDERS = (
    DemoFolder("study", "Study"),
    DemoFolder("coding", "Coding"),
    DemoFolder("personal", "Personal"),
    DemoFolder("semester1", "Semester 1", "study"),
    DemoFolder("reading", "Reading", "study"),
    DemoFolder("maths", "Maths", "semester1"),
    DemoFolder("physics", "Physics", "semester1"),
)


class NavigationButton(QPushButton):
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class ParentTabButton(NavigationButton):
    """A real keyboard-accessible button with a rotated, immediate-parent label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(34)
        self.setMinimumHeight(105)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.setPen(self.palette().buttonText().color())
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.height() - 16
        )
        painter.drawText(
            QRect(8, 0, self.height() - 16, self.width()), Qt.AlignmentFlag.AlignCenter, text
        )
        painter.end()


class FilesPanel(DashboardPanel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("files", "Files", parent)
        self.folders = MappingProxyType({folder.id: folder for folder in DEMO_FOLDERS})
        self.current_folder_id: str | None = None
        self.folder_buttons: dict[str, QPushButton] = {}
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self.parent_tab = ParentTabButton(container)
        self.parent_tab.clicked.connect(self.go_parent)
        layout.addWidget(self.parent_tab)
        self.folder_area = QWidget(container)
        self.folder_layout = QVBoxLayout(self.folder_area)
        self.folder_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.folder_area, 1)
        self.set_content(container)
        self._render()

    def bind_real(self, presenter: object) -> None:
        self.real_presenter = presenter
        self.current_folder_id = None
        while self.folder_layout.count():
            item = self.folder_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.deleteLater()
        self.folder_buttons.clear()
        self.parent_tab.clicked.disconnect()

    def render_real(
        self,
        current: object,
        parent: object,
        folders: tuple[object, ...],
        notes: tuple[object, ...],
        tasks: tuple[object, ...],
    ) -> None:
        from typing import Any, cast

        from PySide6.QtCore import Qt

        presenter = cast(Any, self.real_presenter)
        while self.folder_layout.count():
            item = self.folder_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.deleteLater()
        self.folder_buttons.clear()
        self.parent_tab.setVisible(current is not None)
        if current:
            c, p = cast(Any, current), cast(Any, parent)
            self.parent_tab.setText(p.name if parent else c.name)
            self.parent_tab.setAccessibleName(f"Back to {p.name if parent else 'root'}")
        for folder in folders:
            f = cast(Any, folder)
            button = NavigationButton(f.name, self.folder_area)
            button.clicked.connect(
                lambda _checked=False, identifier=f.id: presenter.enter(identifier)
            )
            self.folder_layout.addWidget(button)
            self.folder_buttons[f.id] = button
        if current is None:
            for domain, label in (("notes", "All notes"), ("tasks", "All tasks")):
                button = NavigationButton(label)
                button.clicked.connect(
                    lambda _checked=False, domain=domain: presenter.open_all(domain)
                )
                self.folder_layout.addWidget(button)
        for domain, rows in (("notes", notes), ("tasks", tasks)):
            for value in rows:
                row = cast(Any, value)
                button = NavigationButton(f"{domain[:-1]} · {row.title or 'Untitled'}")
                button.clicked.connect(
                    lambda _checked=False, domain=domain, identifier=row.id: presenter.open_entity(
                        domain, identifier
                    )
                )
                self.folder_layout.addWidget(button)
        self.folder_layout.addStretch()
        self.parent_tab.setToolTip(self.parent_tab.accessibleName())
        self.parent_tab.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def enter_folder(self, folder_id: str) -> None:
        if folder_id not in self.folder_buttons:
            raise ValueError("Only a child of the current demo folder may be entered.")
        self.current_folder_id = folder_id
        self._render()
        self.parent_tab.setFocus()

    def go_parent(self) -> None:
        if self.current_folder_id is None:
            return
        departed = self.current_folder_id
        self.current_folder_id = self.folders[departed].parent_id
        self._render()
        button = self.folder_buttons.get(departed)
        if button is not None:
            button.setFocus()

    def _render(self) -> None:
        while self.folder_layout.count():
            item = self.folder_layout.takeAt(0)
            assert item is not None
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.folder_buttons.clear()
        current = self.folders.get(self.current_folder_id or "")
        if current is None:
            self.parent_tab.hide()
        else:
            parent = self.folders.get(current.parent_id or "")
            # At the first level the branch label is its return-to-root handle.
            self.parent_tab.setText(parent.name if parent else current.name)
            target = parent.name if parent else "root"
            self.parent_tab.setAccessibleName(f"Back to {target}")
            self.parent_tab.setToolTip(f"Back to {target}")
            self.parent_tab.show()
        children = [folder for folder in DEMO_FOLDERS if folder.parent_id == self.current_folder_id]
        for folder in children:
            button = NavigationButton(folder.name, self.folder_area)
            button.setAccessibleName(f"Open {folder.name}")
            button.clicked.connect(partial(self.enter_folder, folder.id))
            self.folder_layout.addWidget(button)
            self.folder_buttons[folder.id] = button
        if not children:
            empty = QLabel("Nothing here yet", self.folder_area)
            empty.setProperty("role", "secondary")
            self.folder_layout.addWidget(empty)
        self.folder_layout.addStretch()
