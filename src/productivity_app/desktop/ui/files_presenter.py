"""Inert placeholder for the old Files panel.

The panel used to browse the application's folders and tags, which the backend no longer has
(the new design's Files page is a real disk folder).  This stub only keeps the old UI
importable; the whole old UI is removed together with its tests in phase 3.
"""

from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from productivity_app.desktop.ui.session import DesktopUiSession


class FilesPresenter(QObject):
    def __init__(
        self, session: DesktopUiSession, panel: Any, presenters: dict[str, Any], workspace: Any
    ) -> None:
        super().__init__(panel)
        self.session, self.panel, self.presenters, self.workspace = (
            session,
            panel,
            presenters,
            workspace,
        )
        self.current: Any = None
        self.parent_id: str | None = None
        self.known_deleted: dict[str, Any] = {}
        self.controls = QWidget(panel.controls)

    def enter(self, identifier: str | None) -> None:
        self.panel.current_folder_id = identifier

    def refresh(self) -> None:
        return None
