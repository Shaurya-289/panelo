"""Minimal preferences and explicit manual backup/restore controls."""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from productivity_app.desktop.ui.actions import Action
from productivity_app.desktop.ui.session import error_text
from productivity_app.schemas.backups import DeleteBackup, RestoreBackup, ValidateBackup
from productivity_app.schemas.settings import SettingsPatch

_DURATIONS = (
    "default_timer_duration_ms",
    "pomodoro_work_duration_ms",
    "short_break_duration_ms",
    "long_break_duration_ms",
)


class PreferencesDialog(QDialog):
    def __init__(self, owner: Any) -> None:
        super().__init__(owner.window)
        self.owner, self.session = owner, owner.session
        self.settings: Any = None
        self.setWindowTitle("Preferences / manual backups")
        self.resize(620, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.durations: dict[str, QSpinBox] = {}
        for key in _DURATIONS:
            control = QSpinBox()
            control.setRange(1000, 86400000)
            control.setSuffix(" ms")
            self.durations[key] = control
            form.addRow(key.replace("_", " ").title(), control)
        self.minimize = QCheckBox("Minimize to tray")
        form.addRow(self.minimize)
        self.hotkey = QLineEdit()
        form.addRow("Global hotkey", self.hotkey)
        self.startup = QCheckBox("Start with Windows (verified launcher required)")
        form.addRow(self.startup)
        layout.addLayout(form)
        for label, callback in (
            ("Save durations / close preference", self.save),
            ("Apply hotkey", self.apply_hotkey),
            ("Apply Windows startup", self.apply_startup),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.backups = QListWidget()
        self.backups.setAccessibleName("Managed backups")
        layout.addWidget(self.backups)
        self.source = QLineEdit()
        self.source.setPlaceholderText("Explicit SQLite backup path (not a file explorer)")
        self.source.setAccessibleName("Backup source path")
        layout.addWidget(self.source)
        for label, callback in (
            ("Create backup", self.create_backup),
            ("Refresh backups", self.list_backups),
            ("Validate source", self.validate),
            ("Restore source", self.restore),
            ("Delete selected backup", self.delete),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.reload_settings()
        self.list_backups()

    def reload_settings(self) -> None:
        if not self.session.runner.accepting:
            return

        def loaded(value: Any, error: Exception | None) -> None:
            if error:
                self.status.setText(error_text(error))
                return
            self.settings = value
            for key, control in self.durations.items():
                control.setValue(getattr(value, key))
            self.minimize.setChecked(value.minimize_to_tray)
            self.hotkey.setText(value.global_hotkey)
            self.startup.setChecked(value.start_with_windows)

        self.request(Action("settings.get_settings"), "preferences:settings", loaded, read=True)

    def saved(self, value: Any, error: Exception | None) -> None:
        self.status.setText(error_text(error) if error else "Saved")
        if not error:
            self.reload_settings()

    def save(self) -> None:
        if self.settings is None:
            return
        patch = SettingsPatch.model_validate(
            {
                "expected_version": self.settings.version,
                "minimize_to_tray": self.minimize.isChecked(),
                **{key: control.value() for key, control in self.durations.items()},
            }
        )
        self.request(Action("settings.update_settings", (patch,)), "preferences:save", self.saved)

    def request(self, action: Action, key: str, callback: Any, *, read: bool = False) -> None:
        try:
            self.session.call(action, key, callback, read=read)
        except Exception as exc:
            self.status.setText(error_text(exc))

    def apply_hotkey(self) -> None:
        if self.settings:
            self.owner.change_hotkey(self.hotkey.text(), self.settings.version, self.saved)

    def apply_startup(self) -> None:
        if self.settings:
            try:
                self.owner.change_startup(
                    self.startup.isChecked(), self.settings.version, self.saved
                )
            except Exception as exc:
                self.status.setText(error_text(exc))

    def list_backups(self) -> None:
        if not self.session.runner.accepting:
            return

        def loaded(value: Any, error: Exception | None) -> None:
            if error:
                self.status.setText(error_text(error))
                return
            self.backups.clear()
            for row in value:
                self.backups.addItem(row.filename)

        self.request(Action("backups.list_backups"), "preferences:backups", loaded, read=True)

    def create_backup(self) -> None:
        self.request(
            Action("backups.create_backup"),
            "preferences:backup",
            lambda value, error: self.after_backup(value, error),
        )

    def after_backup(self, value: Any, error: Exception | None) -> None:
        self.status.setText(error_text(error) if error else f"Backup verified: {value.filename}")
        self.list_backups()

    def path(self) -> Path:
        if self.source.text().strip():
            return Path(self.source.text().strip())
        item = self.backups.currentItem()
        if item is None:
            raise ValueError("Select a managed backup or enter an explicit source path")
        return Path(self.owner.data_dir) / "backups" / item.text()

    def validate(self) -> None:
        try:
            self.session.call(
                Action("backups.validate_backup", (ValidateBackup(source_path=self.path()),)),
                "preferences:validate",
                self.after_backup,
                read=True,
            )
        except Exception as exc:
            self.status.setText(error_text(exc))

    def restore(self) -> None:
        try:
            if self.owner.workspace.editing:
                raise ValueError("Finish layout editing before restore")
            command = RestoreBackup(source_path=self.path())
            if (
                QMessageBox.question(
                    self,
                    "Restore backup",
                    "Replace current data with this backup? A safety backup is retained.",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            if not self.owner.resolve_drafts():
                self.status.setText("Restore cancelled or waiting for an explicit draft save")
                return
            application = self.session.application
            self.owner.set_transition(True)
            self.setEnabled(False)

            def finished(value: Any, error: Exception | None) -> None:
                # A recovered-original restore error also replaces the runtime.
                if not application.is_closed:
                    if (
                        error is None
                        or id(getattr(application, "search", None)) != self.session.runtime_token
                    ):
                        self.session.rebind()
                    self.setEnabled(True)
                    self.owner.set_transition(False)
                    self.owner.refresh()
                    self.reload_settings()
                    self.list_backups()
                else:
                    self.owner.close()
                    self.owner.set_transition(True)
                    self.status.setText(
                        "Application closed after recovery failure. Quit and recover from the retained safety backup."
                    )
                if error:
                    self.status.setText(error_text(error))
                else:
                    self.status.setText(f"Restored {value.source_filename}")

            self.session.exclusive(lambda: application.restore_backup(command), finished)
        except Exception as exc:
            self.setEnabled(True)
            self.owner.set_transition(False)
            self.status.setText(error_text(exc))

    def delete(self) -> None:
        item = self.backups.currentItem()
        if (
            item
            and QMessageBox.question(
                self, "Delete backup", "Permanently delete this managed backup file?"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.request(
                Action("backups.delete_backup", (DeleteBackup(filename=item.text()),)),
                "preferences:delete",
                lambda value, error: self.after_delete(error),
            )

    def after_delete(self, error: Exception | None) -> None:
        self.status.setText(error_text(error) if error else "Backup deleted")
        self.list_backups()
