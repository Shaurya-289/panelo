"""Final UI composition, retaining one shell and the backend application facade."""

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from productivity_app.desktop.ui import prompts
from productivity_app.desktop.ui.actions import Action, dispatch
from productivity_app.desktop.ui.command_palette import CommandPalette
from productivity_app.desktop.ui.command_parser import duration_ms, help_text, parse
from productivity_app.desktop.ui.files_presenter import FilesPresenter
from productivity_app.desktop.ui.layout_store import LayoutStore
from productivity_app.desktop.ui.preferences import PreferencesDialog
from productivity_app.desktop.ui.presenters import DomainPresenter
from productivity_app.desktop.ui.session import DesktopUiSession, error_text
from productivity_app.desktop.ui.timer_display import TimerDisplay
from productivity_app.schemas.notes import NoteQuery
from productivity_app.schemas.search import FullTextSearchQuery, UnifiedSearchQuery
from productivity_app.schemas.settings import SettingsPatch
from productivity_app.schemas.tasks import CreateTask, TaskQuery
from productivity_app.schemas.timers import CreateTimer, TimerPresetQuery


class ApplicationUi(QObject):
    def __init__(self, application: Any, controller: Any, data_dir: Path) -> None:
        super().__init__(controller.window)
        self.window, self.controller, self.data_dir = controller.window, controller, data_dir
        self.workspace = self.window.dashboard
        self.session = DesktopUiSession(application, self)
        self.transition = False
        self.session.can_mutate = lambda: not self.workspace.editing and not self.transition
        self.waiting_quit = False
        self.shell_settings: Any = None
        self.preferences: PreferencesDialog | None = None
        self.presenters = {
            domain: DomainPresenter(self.session, self.workspace.panels[key], domain)
            for domain, key in (
                ("notes", "notes"),
                ("tasks", "todo"),
                ("reminders", "reminders"),
                ("timers", "timers"),
            )
        }
        self.files = FilesPresenter(
            self.session, self.workspace.panels["files"], self.presenters, self.workspace
        )
        self.workspace.enable_freeform(LayoutStore(data_dir / "ui" / "workspace-v1.json"))
        for domain, presenter in self.presenters.items():
            key = "todo" if domain == "tasks" else domain
            presenter.on_detail = lambda key=key: self.reveal(key)
        self.palette = CommandPalette(self.window.surface)
        self.window.surface.layout().addWidget(self.palette)
        self.palette.execute.connect(self.execute)
        self.indicator = QPushButton()
        self.indicator.setAccessibleName("Reveal active timer")
        self.indicator.clicked.connect(lambda: self.reveal("timers"))
        self.window.surface.layout().addWidget(self.indicator)
        self.timer_label = QLabel()
        self.timer_label.setTextFormat(Qt.TextFormat.PlainText)
        cast(QVBoxLayout, self.presenters["timers"].view.layout()).addWidget(self.timer_label)
        self.display = TimerDisplay(self)
        self.display.painted.connect(self.paint_timer)
        self.layout_button = QToolButton(self.window.surface)
        self.layout_button.setText("⊞")
        self.layout_button.setAccessibleName("Edit layout")
        self.layout_button.clicked.connect(
            lambda: self.workspace.set_editing(not self.workspace.editing)
        )
        self.window.shell_controls.insertWidget(0, self.layout_button)
        self.reset_button = QToolButton(self.window.surface)
        self.reset_button.setText("Reset layout")
        self.reset_button.setAccessibleName("Reset five-panel layout")
        self.reset_button.clicked.connect(self.confirm_reset_layout)
        self.reset_button.hide()
        self.window.shell_controls.insertWidget(1, self.reset_button)
        self.preferences_button = QToolButton(self.window.surface)
        self.preferences_button.setText("⚙")
        self.preferences_button.setAccessibleName("Preferences")
        self.preferences_button.setToolTip("Preferences and backups")
        self.preferences_button.clicked.connect(self.open_preferences)
        self.window.shell_controls.insertWidget(0, self.preferences_button)
        self.shortcuts: list[QShortcut] = []
        for sequence, callback in (
            ("Ctrl+K", self.palette.open),
            ("Ctrl+S", self.save_focused),
            ("Ctrl+F", self.search_focused),
            ("Ctrl+Shift+L", lambda: self.workspace.set_editing(not self.workspace.editing)),
            ("Ctrl+Shift+M", self.maximize_focused),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self.window)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        self.window.escape_shortcut.activated.disconnect()
        self.window.escape_shortcut.activated.connect(self.escape)
        self.window.installEventFilter(self)
        self.workspace.presentation_changed.connect(self.presentation_changed)
        self.workspace.layout_changed.connect(self.layout_status)
        self.session.changed.connect(self.event_changed)
        self.session.restored.connect(self.clear_runtime_views)
        self.indicator.hide()
        self.presentation_changed()
        self.refresh()

    def reveal(self, panel: str) -> None:
        if self.workspace.maximized != panel:
            self.workspace.maximize(panel)
        self.workspace.scroll_area.ensureWidgetVisible(self.workspace.panels[panel])
        presenter = getattr(self.workspace.panels[panel], "presenter", None)
        if presenter:
            presenter.view.fields.get(
                "title", presenter.view.fields.get("name", presenter.view.records)
            ).setFocus()

    def focused_panel(self) -> str:
        focused = QApplication.focusWidget()
        for key, panel in self.workspace.panels.items():
            if focused and (panel is focused or panel.isAncestorOf(focused)):
                return str(key)
        return self.workspace.maximized or "notes"

    def save_focused(self) -> None:
        if self.workspace.editing or self.transition:
            return
        presenter = getattr(self.workspace.panels[self.focused_panel()], "presenter", None)
        if presenter:
            presenter.save()

    def search_focused(self) -> None:
        presenter = getattr(self.workspace.panels[self.focused_panel()], "presenter", None)
        if presenter:
            presenter.view.toggle_search()

    def maximize_focused(self) -> None:
        self.workspace.maximize(self.focused_panel())

    def escape(self) -> None:
        if not self.palette.isHidden():
            self.palette.dismiss()
        elif self.workspace.cancel_gesture():
            return
        elif self.workspace.editing:
            self.workspace.set_editing(False)
        elif self.workspace.maximized:
            self.workspace.maximize(None)
        else:
            self.window.hide_requested.emit()

    def layout_status(self) -> None:
        if self.workspace.store and self.workspace.store.diagnostic:
            self.layout_button.setToolTip(self.workspace.store.diagnostic)
            self.layout_button.setText(
                "Layout · not saved"
                if self.workspace.store.diagnostic == "Layout not saved."
                else "Layout · check saved file"
            )
        else:
            self.layout_button.setToolTip("Edit / Done · Ctrl+Shift+L")

    def presentation_changed(self) -> None:
        self.reset_button.setVisible(self.workspace.editing)
        self.layout_button.setText("Done" if self.workspace.editing else "⊞")
        self.files.controls.setEnabled(not self.workspace.editing)
        for domain, presenter in self.presenters.items():
            key = "todo" if domain == "tasks" else domain
            expanded = self.workspace.maximized == key
            presenter.view.set_expanded(expanded)
            presenter.view.button_area.setEnabled(
                not self.workspace.editing and not presenter.pending
            )
            if domain == "timers":
                presenter.focus_status.setVisible(expanded and bool(presenter.focus_status.text()))
        panel = self.workspace.panels["timers"]
        viewport = self.workspace.scroll_area.viewport()
        rectangle = panel.rect().translated(panel.mapTo(viewport, QPoint(0, 0)))
        visible = panel.isVisible() and rectangle.intersects(viewport.rect())
        active = self.display.view is not None if hasattr(self, "display") else False
        self.indicator.setVisible(self.window.isVisible() and active and not visible) if hasattr(
            self, "indicator"
        ) else None
        if hasattr(self, "display"):
            self.display.set_visible(
                self.window.isVisible() and active and (visible or self.indicator.isVisible())
            )
        self.layout_status()

    def paint_timer(self, text: str) -> None:
        self.indicator.setText(text)
        self.timer_label.setText(text)

    def read(self, operation: Any, key: str, callback: Any) -> None:
        try:
            self.session.run(operation, key, callback, read=True)
        except RuntimeError as exc:
            callback(None, exc)

    def refresh_active(self) -> None:
        def loaded(value: Any, error: Exception | None) -> None:
            if not error:
                self.display.update(value)
                self.presentation_changed()
            else:
                self.timer_label.setText(error_text(error))

        self.read(
            lambda: dispatch(self.session.application, Action("timers.get_active_timer")),
            "timers:active",
            loaded,
        )

    def refresh_references(self) -> None:
        application = self.session.application

        def operation() -> tuple[Any, ...]:
            return (
                dispatch(application, Action("folders.get_tree")),
                dispatch(
                    application, Action("tags.list_tags", kwargs=(("include_deleted", True),))
                ),
                dispatch(application, Action("timers.list_presets", (TimerPresetQuery(),))),
                dispatch(application, Action("tasks.list_tasks", (TaskQuery(limit=200),))),
                dispatch(application, Action("notes.list_notes", (NoteQuery(limit=200),))),
            )

        def loaded(value: Any, error: Exception | None) -> None:
            if error:
                self.presenters["notes"].view.status.setText(error_text(error))
                return
            folders: list[Any] = []

            def flatten(rows: Any) -> None:
                for row in rows:
                    folders.append(row)
                    flatten(row.children)

            flatten(value[0])
            for domain in ("notes", "tasks"):
                self.presenters[domain].view.set_reference("folder_id", tuple(folders))
                self.presenters[domain].view.set_tags(value[1])
            self.presenters["timers"].view.set_reference("preset_id", value[2])
            for domain in ("timers", "reminders"):
                self.presenters[domain].view.set_reference("task_id", value[3])
            self.presenters["reminders"].view.set_reference("note_id", value[4])

        self.read(operation, "ui:references", loaded)

    def refresh(self) -> None:
        if not self.session.runner.accepting or self.transition:
            return
        for presenter in self.presenters.values():
            presenter.refresh()
        self.files.refresh()
        self.refresh_active()
        self.refresh_references()
        self.refresh_shell_settings()

    def refresh_shell_settings(self) -> None:
        def loaded(value: Any, error: Exception | None) -> None:
            if not error:
                self.shell_settings = value
            else:
                self.preferences_button.setToolTip(error_text(error))

        self.read(
            lambda: dispatch(self.session.application, Action("settings.get_settings")),
            "shell:settings",
            loaded,
        )

    def toggle_startup(self) -> None:
        coordinator = self.controller._startup_coordinator
        if coordinator is None:
            return
        application = self.session.application

        def operation() -> Any:
            status = coordinator.inspect()
            if status.state.value in {"MISMATCHED", "UNAVAILABLE"}:
                raise ValueError("Windows startup registration requires manual repair")
            settings = dispatch(application, Action("settings.get_settings"))
            return coordinator.set_enabled(status.state.value != "ENABLED", settings.version)

        def finished(value: Any, error: Exception | None) -> None:
            self.controller.status_text = (
                error_text(error) if error else f"Start with Windows: {value.state.value.lower()}"
            )
            if not error:
                self.controller._apply_startup_status(value.state)

        try:
            self.session.run(operation, "shell:startup", finished)
        except Exception as exc:
            finished(None, exc)

    def event_changed(self, event: Any) -> None:
        if not self.session.runner.accepting or self.transition:
            return
        kind = str(event.event_type)
        if "SETTINGS" in kind:
            self.refresh_shell_settings()
        affected = (
            "notes"
            if "NOTE" in kind
            else "tasks"
            if "TASK" in kind
            else "reminders"
            if "REMINDER" in kind
            else "timers"
            if "TIMER" in kind or "FOCUS" in kind
            else None
        )
        if affected:
            presenter = self.presenters[affected]
            if presenter.view.current and presenter.view.current.id == event.entity_id:
                if presenter.view.dirty:
                    presenter.view.status.setText(
                        "Changed elsewhere · unsaved input kept. Reload explicitly before reapplying."
                    )
                else:
                    singular = "reminder" if affected == "reminders" else affected[:-1]
                    presenter.request(
                        Action(
                            f"{affected}.get_{singular}",
                            (event.entity_id,),
                            (("include_deleted", True),),
                        ),
                        "selection",
                        presenter._loaded,
                        read=True,
                    )
            presenter.refresh()
        if any(value in kind for value in ("FOLDER", "TAG", "NOTE", "TASK")):
            self.files.refresh()
        if kind == "FOLDER_DELETED":
            for domain in ("notes", "tasks"):
                presenter = self.presenters[domain]
                if presenter.folder_filter == event.entity_id:
                    presenter.folder_filter = None
                row = presenter.view.current
                if row and row.folder_id == event.entity_id:
                    if presenter.view.dirty:
                        presenter.view.status.setText(
                            "Folder changed elsewhere · draft kept. Review the latest version before saving."
                        )
                    else:
                        presenter.request(
                            Action(
                                f"{domain}.get_{domain[:-1]}",
                                (row.id,),
                                (("include_deleted", True),),
                            ),
                            "selection",
                            presenter._loaded,
                            read=True,
                        )
        if any(name in kind for name in ("FOLDER", "TAG", "PRESET", "TASK", "NOTE")):
            self.refresh_references()
            for domain in ("notes", "tasks"):
                self.presenters[domain].refresh()
        if "TIMER" in kind:
            self.refresh_active()

    def clear_runtime_views(self) -> None:
        prompts.clear(self.window)
        for presenter in self.presenters.values():
            dialog = getattr(presenter, "filter_dialog", None)
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
                presenter.filter_dialog = None
            presenter.rows = ()
            presenter.view.load()
            presenter.set_pending(False)
            presenter.view.records.clear()
            presenter.offset = 0
            presenter.folder_filter = None
            presenter.tag_filter = None
            presenter.status_filter = None
            presenter.priority_filter = None
        self.files.current = None
        self.files.known_deleted.clear()
        self.files.panel.current_folder_id = None
        self.files.parent_id = None
        self.display.update(None)

    def resolve_drafts(self) -> bool:
        return all(p.view.permit_discard() for p in self.presenters.values())

    def set_transition(self, enabled: bool) -> None:
        self.transition = enabled
        self.workspace.setEnabled(not enabled)
        self.palette.setEnabled(not enabled)
        self.preferences_button.setEnabled(not enabled)
        self.layout_button.setEnabled(not enabled)
        self.reset_button.setEnabled(not enabled)

    def confirm_reset_layout(self) -> None:
        if (
            QMessageBox.question(
                self.window, "Reset layout", "Restore the default five-panel arrangement?"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.workspace.reset_layout()

    def prepare_quit(self) -> bool:
        if not self.resolve_drafts():
            return False
        if self.session.runner.busy:
            if not self.waiting_quit:
                self.waiting_quit = True
                self.session.runner.quiesce()
                self.set_transition(True)
                self.session.runner.idle.connect(self.drained_quit)
            return False
        self.session.runner.quiesce()
        return True

    def drained_quit(self) -> None:
        self.session.runner.idle.disconnect(self.drained_quit)
        self.waiting_quit = False
        self.controller.request_quit()

    def resume_after_rejected_close(self) -> None:
        self.session.runner.resume()
        self.set_transition(False)

    def close(self) -> None:
        prompts.clear(self.window)
        self.display.set_visible(False)
        self.session.close()

    def drain(self) -> None:
        """Wait for accepted one-shot requests without running services on GUI."""
        if self.session.runner.busy:
            loop = QEventLoop(self)
            self.session.runner.idle.connect(loop.quit)
            loop.exec()
            self.session.runner.idle.disconnect(loop.quit)

    def abort_startup(self) -> None:
        """Failed shell construction drains its worker before backend cleanup."""
        self.session.runner.quiesce()
        self.session.callbacks.clear()
        self.drain()
        self.close()

    def open_preferences(self) -> None:
        if self.preferences is None:
            self.preferences = PreferencesDialog(self)
        self.preferences.show()
        self.preferences.raise_()

    def change_hotkey(self, value: str, version: int, callback: Any) -> None:
        from productivity_app.desktop.hotkey import parse_hotkey

        try:
            manager = self.controller._hotkey_manager
            if manager is None:
                raise ValueError("Hotkey integration unavailable")

            def persist(canonical: str, done: Any) -> None:
                patch = SettingsPatch(expected_version=version, global_hotkey=canonical)
                self.session.call(
                    Action("settings.update_settings", (patch,)),
                    "preferences:hotkey",
                    lambda result, error: done(error),
                )

            manager.replace_async(parse_hotkey(value), persist, lambda error: callback(None, error))
        except Exception as exc:
            callback(None, exc)

    def change_startup(self, enabled: bool, version: int, callback: Any) -> None:
        coordinator = self.controller._startup_coordinator
        if coordinator is None:
            callback(None, ValueError("Windows startup integration unavailable"))
            return
        self.session.run(
            lambda: coordinator.set_enabled(enabled, version), "preferences:startup", callback
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Hide:
            self.workspace.cancel_gesture()
            self.display.set_visible(False)
        elif event.type() == QEvent.Type.Show:
            self.refresh()
            self.presentation_changed()
        elif event.type() == QEvent.Type.ActivationChange:
            if not self.window.isActiveWindow():
                self.workspace.cancel_gesture()
            else:
                self.refresh()
        return super().eventFilter(watched, event)

    def execute(self, text: str) -> None:
        try:
            command = parse(text)
            pattern, arguments = command.pattern, command.arguments
            if (
                self.transition
                or self.workspace.editing
                and command.family not in {"layout", "help", "panel"}
            ):
                raise ValueError(
                    "Finish layout editing or lifecycle transition before domain commands"
                )
            if command.family == "help":
                self.palette.report(help_text(arguments[0] if arguments else None))
            elif command.family == "layout":
                if pattern == "layout edit":
                    self.workspace.set_editing(True)
                elif pattern == "layout done":
                    self.workspace.set_editing(False)
                elif pattern == "layout swap <panel> <panel>":
                    if not self.workspace.editing:
                        raise ValueError("Enter layout edit first")
                    before = self.workspace.logical.copy()
                    self.workspace.swap(*arguments)
                    if before == self.workspace.logical:
                        raise ValueError("Swap rejected: panel minima or geometry conflict")
                elif (
                    QMessageBox.question(
                        self.window, "Reset layout", "Reset the five-panel arrangement?"
                    )
                    == QMessageBox.StandardButton.Yes
                ):
                    self.workspace.reset_layout()
                else:
                    raise ValueError("Layout reset cancelled")
                self.palette.report("Layout editing" if self.workspace.editing else "Layout ready")
            elif command.family == "panel":
                if arguments:
                    self.reveal(arguments[0])
                else:
                    self.workspace.maximize(None)
                self.palette.report("Panel presentation updated")
            elif pattern == "refresh":
                self.refresh()
                self.palette.report("Refresh requested")
            elif command.family == "open":
                target = pattern.split()[1]
                if target == "folder":
                    self.reveal("files")
                    self.files.enter(arguments[0])
                else:
                    domain = {"note": "notes", "task": "tasks", "timer": "timers"}[target]
                    if self.presenters[domain].pending:
                        raise ValueError("Wait for the pending mutation before changing selection")
                    self.reveal("todo" if domain == "tasks" else domain)
                    self.presenters[domain].select(arguments[0])
                self.palette.report("Open requested")
            elif pattern.startswith("new note"):
                presenter = self.presenters["notes"]
                if presenter.pending:
                    raise ValueError("Wait for the pending mutation before opening a draft")
                if not presenter.view.permit_discard():
                    raise ValueError("Draft change cancelled or waiting for save")
                self.session.state.next_generation("notes:selection")
                self.session.state.selections["notes"] = None
                presenter.view.load(values={"title": arguments[0] if arguments else ""})
                presenter.view.dirty = bool(arguments)
                self.reveal("notes")
                self.palette.report("Note draft opened · Save explicitly")
            elif pattern == "new task <text>":
                self.command_action(
                    Action("tasks.create_task", (CreateTask(title=arguments[0]),)), "tasks"
                )
            elif command.family == "search":
                query = (
                    UnifiedSearchQuery(query=arguments[0])
                    if pattern == "search like <text>"
                    else FullTextSearchQuery(text=arguments[0], mode="PREFIX")
                )
                self.session.call(
                    Action(
                        "search.search"
                        if pattern == "search like <text>"
                        else "search.search_full_text",
                        (query,),
                    ),
                    "cli:search",
                    lambda value, error: self.palette.report(
                        error_text(error)
                        if error
                        else "\n".join(
                            f"{row.entity_type} · {row.title} · {row.entity_id}" for row in value
                        )
                        or "No results",
                        not error,
                    ),
                    read=True,
                )
            elif command.family == "reminders":
                presenter = self.presenters["reminders"]
                presenter.view.mode.setCurrentText(pattern.split()[1].title())
                presenter.refresh()
                self.reveal("reminders")
                self.palette.report("Reminder view opened")
            elif command.family == "focus":
                presenter = self.presenters["timers"]
                action = (
                    "sequence"
                    if "sequence" in pattern
                    else "short_break"
                    if pattern.endswith("short")
                    else "long_break"
                    if pattern.endswith("long")
                    else "advance"
                    if pattern == "focus next"
                    else "cancel_sequence"
                    if pattern == "focus cancel"
                    else "focus"
                )
                presenter.focus_action(
                    action,
                    arguments[0] if arguments else None,
                    use_selected_task=False,
                    on_result=lambda value, error: self.palette.report(
                        error_text(error)
                        if error
                        else "Focus command committed · no timer was started automatically",
                        not error,
                    ),
                )
                self.reveal("timers")
            elif pattern == "timer new <duration>":
                self.command_action(
                    Action(
                        "timers.create_timer",
                        (CreateTimer(name="Timer", planned_duration_ms=duration_ms(arguments[0])),),
                    ),
                    "timers",
                )
            elif pattern == "timer reconcile":
                self.command_action(Action("timers.reconcile_expired_timer"), "timers")
            elif pattern == "timer start <uuid>":
                application, identifier = self.session.application, arguments[0]

                def start() -> Any:
                    row = dispatch(application, Action("timers.get_timer", (identifier,)))
                    return dispatch(
                        application, Action("timers.start_timer", (row.id, row.version))
                    )

                self.session.run(
                    start,
                    "cli:timer",
                    lambda value, error: self.command_result(value, error, "timers"),
                )
            else:
                method = {
                    "timer pause": "pause_timer",
                    "timer resume": "resume_timer",
                    "timer finish": "complete_timer",
                    "timer cancel": "cancel_timer",
                    "timer reset": "reset_timer",
                }[pattern]
                application = self.session.application

                def mutation() -> Any:
                    row = dispatch(application, Action("timers.get_active_timer"))
                    if row is None:
                        raise ValueError("No running or paused timer")
                    return dispatch(application, Action(f"timers.{method}", (row.id, row.version)))

                self.session.run(
                    mutation,
                    "cli:timer",
                    lambda value, error: self.command_result(value, error, "timers"),
                )
        except Exception as exc:
            self.palette.report(error_text(exc), False)

    def command_action(self, action: Action, domain: str) -> None:
        self.session.call(
            action, "cli:mutation", lambda value, error: self.command_result(value, error, domain)
        )

    def command_result(self, value: Any, error: Exception | None, domain: str) -> None:
        if error:
            self.palette.report(error_text(error), False)
        else:
            presenter = self.presenters[domain]
            if presenter.view.dirty:
                presenter.refresh()
                presenter.view.status.setText("Command committed · unsaved editor preserved")
            else:
                presenter._saved(value, None)
            self.palette.report(
                f"Committed · {value.id}" if value else "No expired timer to record"
            )
