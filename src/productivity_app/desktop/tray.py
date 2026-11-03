"""Tray menu construction with no notification-message capability used."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon


class ShellTray:
    """Owns only shell navigation actions, never domain actions or notifications."""

    def __init__(
        self,
        on_open: Callable[[], None],
        on_hide: Callable[[], None],
        on_quit: Callable[[], None],
        on_startup_toggle: Callable[[], None] | None = None,
    ) -> None:
        self._menu = QMenu()
        self.open_action = self._menu.addAction("Open", on_open)
        self.hide_action = self._menu.addAction("Hide", on_hide)
        self.startup_action = self._menu.addAction("Start with Windows")
        self.startup_action.setCheckable(True)
        self.startup_action.setEnabled(False)
        if on_startup_toggle is not None:
            self.startup_action.triggered.connect(on_startup_toggle)
        self._menu.addSeparator()
        self.quit_action = self._menu.addAction("Quit", on_quit)
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._icon = QSystemTrayIcon(icon)
        self._icon.setToolTip("Productivity App")
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._activated)
        self._on_open = on_open
        self._closed = False

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def available(self) -> bool:
        return self.is_available()

    def show(self) -> None:
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.hide()
        self._menu.deleteLater()
        self._icon.deleteLater()

    def set_startup_state(self, *, enabled: bool, checked: bool) -> None:
        """Reflect inspected OS state; this never mutates the registry itself."""
        self.startup_action.setEnabled(enabled)
        self.startup_action.setChecked(checked)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._on_open()
