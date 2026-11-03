"""Small domain presenters: public schemas in, immutable DTOs out."""

from typing import Any, cast

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
)

from productivity_app.desktop.ui import prompts
from productivity_app.desktop.ui.actions import Action, PartialSave, dispatch, save_relationships
from productivity_app.desktop.ui.editors import InlineStatus, RecordView, local_instant
from productivity_app.desktop.ui.session import DesktopUiSession, error_text
from productivity_app.schemas.focus import (
    AdvanceFocusSequence,
    CancelFocusSequence,
    CreateFocusSequence,
    CreateFocusSession,
    FocusSequenceQuery,
    FocusStatsQuery,
)
from productivity_app.schemas.notes import CreateNote, NoteQuery, NoteSearchQuery, UpdateNote
from productivity_app.schemas.reminders import (
    CreateReminder,
    ReminderPageQuery,
    ReminderQuery,
    SnoozeReminder,
    UpdateReminder,
)
from productivity_app.schemas.search import FullTextSearchQuery
from productivity_app.schemas.tasks import (
    CreateTask,
    TaskCalendarQuery,
    TaskPageQuery,
    TaskQuery,
    TaskSearchQuery,
    UpdateTask,
)
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    MoveTimerPreset,
    TimerPresetQuery,
    TimerQuery,
    UpdateTimer,
    UpdateTimerPreset,
)

SPECS = {
    "notes": (
        ("title", "content", "folder_id"),
        ("All", "Pinned", "Archived", "Deleted"),
        CreateNote,
        UpdateNote,
    ),
    "tasks": (
        (
            "title",
            "description",
            "priority",
            "start_at_utc",
            "due_at_utc",
            "timezone",
            "fold",
            "folder_id",
        ),
        (
            "Active",
            "All",
            "Today",
            "Next Seven Days",
            "Upcoming",
            "Overdue",
            "Important",
            "Completed",
            "Deleted",
        ),
        CreateTask,
        UpdateTask,
    ),
    "reminders": (
        ("title", "message", "due_at_utc", "scheduled_timezone", "fold", "task_id", "note_id"),
        ("Upcoming", "Due", "Overdue", "History", "All", "Deleted"),
        CreateReminder,
        UpdateReminder,
    ),
    "timers": (
        ("name", "planned_duration_ms", "preset_id", "task_id"),
        ("All", "Active", "Finished", "Deleted"),
        CreateTimer,
        UpdateTimer,
    ),
}
COMMANDS = {
    "notes": (
        "pin_note",
        "unpin_note",
        "archive_note",
        "restore_from_archive",
        "delete_note",
        "restore_note",
    ),
    "tasks": (
        "start_task",
        "return_task_to_todo",
        "complete_task",
        "cancel_task",
        "reopen_task",
        "delete_task",
        "restore_task",
    ),
    "reminders": (
        "complete_reminder",
        "dismiss_reminder",
        "cancel_reminder",
        "reopen_reminder",
        "delete_reminder",
        "restore_reminder",
    ),
    "timers": (
        "start_timer",
        "pause_timer",
        "resume_timer",
        "complete_timer",
        "cancel_timer",
        "reset_timer",
        "delete_timer",
        "restore_timer",
    ),
}


class DomainPresenter(QObject):
    def __init__(self, session: DesktopUiSession, panel: Any, domain: str) -> None:
        super().__init__(panel)
        self.session, self.panel, self.domain = session, panel, domain
        fields, modes, _, _ = SPECS[domain]
        self.view = RecordView(fields, modes)
        panel.set_content(self.view)
        panel.search = self.view.search
        panel.presenter = self
        self.view.add_button("new", "New")
        self.view.add_button("save", "Save")
        self.view.add_button("refresh", "Refresh")
        self.view.add_button("more", "Actions…")
        self.view.intent.connect(self.intent)
        self.view.selected.connect(self.select)
        self.view.search_requested.connect(self.query_changed)
        self.view.completion_requested.connect(self.complete_row)
        self.on_detail: Any = lambda: None
        self.rows: tuple[Any, ...] = ()
        self.filter_dialog: QDialog | None = None
        self.offset = 0
        self.folder_filter: str | None = None
        self.tag_filter: str | None = None
        self.priority_filter: str | None = None
        self.status_filter: str | None = None
        self.notice: str | None = None
        self.pending = False
        if domain == "timers":
            self.view.add_button("create_start", "Create & start")
            self.focus_status = InlineStatus()
            self.focus_status.hide()
            self.focus_status.setTextFormat(Qt.TextFormat.PlainText)
            self.focus_status.setWordWrap(True)
            layout = self.view.layout()
            assert layout is not None
            layout.addWidget(self.focus_status)
        if domain in {"notes", "tasks"}:
            self.view.add_button("filters", "Filters…")
        self.view.add_button("previous", "‹")
        self.view.add_button("next", "›")
        panel.control_layout.insertWidget(0, self.view.button_area)
        self.view.buttons["new"].setAccessibleName(f"Create {domain[:-1]}")
        self.view.buttons["new"].setToolTip(f"New {domain[:-1]}")
        self.view.menu_button.setAccessibleName(f"{domain.title()} options")
        self.view.mode.currentTextChanged.connect(self.update_heading)
        if domain == "notes":
            self.view.menu_actions["more"].setVisible(False)
            self.note_menu = self.view.menu.addMenu("This note")
            self.view.menu.aboutToShow.connect(self.populate_note_menu)
        self.view.load(
            values={
                "timezone": "Asia/Calcutta",
                "scheduled_timezone": "Asia/Calcutta",
                "priority": "NORMAL",
            }
        )

    def update_heading(self, mode: str) -> None:
        default = SPECS[self.domain][1][0]
        title = "To-Do" if self.domain == "tasks" else self.domain.title()
        self.panel.title_label.setText(title.upper() if mode == default else f"{title} · {mode}")

    def populate_note_menu(self) -> None:
        menu: QMenu = self.note_menu
        menu.clear()
        row = self.view.current
        menu.menuAction().setVisible(row is not None)
        if row is None:
            return
        if row.deleted_at_utc is not None:
            commands = [("restore_note", "Restore")]
        else:
            commands = [
                ("unpin_note", "Unpin") if row.is_pinned else ("pin_note", "Pin"),
                ("restore_from_archive", "Unarchive")
                if row.is_archived
                else ("archive_note", "Archive"),
                ("delete_note", "Delete"),
            ]
        commands += [("reload", "Reload"), ("review_latest", "Review external changes…")]
        for name, label in commands:
            action = menu.addAction(label)
            action.setEnabled(not self.pending)
            action.triggered.connect(lambda _checked=False, name=name: self.mutate(name))

    def request(self, action: Action, key: str, callback: Any, *, read: bool = False) -> None:
        try:
            self.session.call(action, f"{self.domain}:{key}", callback, read=read)
        except Exception as exc:
            self.view.status.setText(error_text(exc))
            if not read:
                self.set_pending(False)

    def refresh(self) -> None:
        try:
            self._refresh()
            if self.domain == "timers":
                self.refresh_sequence()
        except Exception as exc:
            self.view.status.setText(error_text(exc))

    def query_changed(self) -> None:
        self.offset = 0
        self.refresh()

    def _refresh(self) -> None:
        mode, term = self.view.mode.currentText(), self.view.search.text().strip()
        deleted = mode == "Deleted"
        page: dict[str, Any] = {"limit": 50, "offset": self.offset}
        if term and deleted and self.domain == "tasks":
            self.view.status.setText(
                "Deleted-task text search is not supported by the Task API. Clear search to list deleted tasks."
            )
            return
        if term and (
            self.domain in {"reminders", "timers"} or (deleted and self.domain == "notes")
        ):
            application, domain = self.session.application, self.domain
            fts_query = FullTextSearchQuery.model_validate(
                {
                    "text": term,
                    "entity_types": (
                        {"reminders": "REMINDER", "timers": "TIMER", "notes": "NOTE"}[domain],
                    ),
                    "include_deleted": deleted,
                    **page,
                }
            )
            search_action = Action("search.search_full_text", (fts_query,))

            def operation() -> tuple[Any, ...]:
                matches = dispatch(application, search_action)
                return tuple(
                    dispatch(
                        application,
                        Action(
                            f"{domain}.get_{domain[:-1]}",
                            (match.entity_id,),
                            (("include_deleted", deleted),),
                        ),
                    )
                    for match in matches
                )

            self.session.run(operation, f"{domain}:list", self._listed, read=True)
            return
        if self.domain == "notes":
            if term and not deleted:
                action = Action(
                    "notes.search_notes",
                    (NoteSearchQuery(query=term, include_archived=mode == "Archived", limit=100),),
                )
            else:
                action = Action(
                    "notes.list_notes",
                    (
                        NoteQuery(
                            include_deleted=deleted,
                            is_pinned=True if mode == "Pinned" else None,
                            is_archived=True if mode == "Archived" else None,
                            **page,
                        ),
                    ),
                )
        elif self.domain == "tasks":
            if term and not deleted:
                action = Action(
                    "tasks.search_tasks",
                    (TaskSearchQuery(query=term, include_terminal=True, limit=100),),
                )
            elif mode in {"Today", "Next Seven Days", "Upcoming"}:
                method = {
                    "Today": "list_today",
                    "Next Seven Days": "list_next_seven_days",
                    "Upcoming": "list_upcoming",
                }[mode]
                try:
                    query = TaskCalendarQuery(
                        timezone=self.view.text("timezone") or "Asia/Calcutta", **page
                    )
                except Exception as exc:
                    self.view.status.setText(error_text(exc))
                    return
                action = Action(f"tasks.{method}", (query,))
            elif mode in {"Overdue", "Important", "Completed"}:
                action = Action(f"tasks.list_{mode.lower()}", (TaskPageQuery(**page),))
            else:
                action = Action(
                    "tasks.list_tasks",
                    (
                        TaskQuery(
                            priorities=cast(
                                Any, (self.priority_filter,) if self.priority_filter else ()
                            ),
                            include_deleted=deleted,
                            statuses=cast(
                                Any,
                                (self.status_filter,)
                                if self.status_filter
                                else (("TODO", "IN_PROGRESS") if mode == "Active" else ()),
                            ),
                            **page,
                        ),
                    ),
                )
        elif self.domain == "reminders":
            action = (
                Action(
                    "reminders.list_reminders",
                    (ReminderQuery(sort="due_soonest", **page),),
                )
                if mode in {"All", "Deleted"}
                else Action(f"reminders.list_{mode.lower()}", (ReminderPageQuery(**page),))
            )
        else:
            action = Action(
                "timers.list_timers",
                (
                    TimerQuery(
                        include_deleted=deleted,
                        states=("RUNNING", "PAUSED")
                        if mode == "Active"
                        else (("FINISHED",) if mode == "Finished" else ()),
                        **page,
                    ),
                ),
            )
        self.request(action, "list", self._listed, read=True)

    def _listed(self, value: Any, error: Exception | None) -> None:
        if error:
            self.view.status.setText(error_text(error))
            return
        source_count = len(value)
        self.rows = tuple(
            row
            for row in value
            if self.view.mode.currentText() != "Deleted" or row.deleted_at_utc is not None
        )
        bounded_search = (
            bool(self.view.search.text().strip())
            and self.domain in {"notes", "tasks"}
            and self.view.mode.currentText() != "Deleted"
        )
        count = len(self.rows)
        if bounded_search:
            self.rows = self.rows[self.offset : self.offset + 50]
        self.view.render_rows(self.rows)
        self.view.buttons["next"].setEnabled(
            not self.pending
            and (self.offset + 50 < count if bounded_search else source_count == 50)
        )
        self.view.buttons["previous"].setEnabled(not self.pending and self.offset > 0)
        if self.view.mode.currentText() == "Deleted" and not self.rows:
            self.view.status.setText(
                "No deleted records on this source page · use Next / Previous to check other pages"
            )
        if self.notice:
            self.view.status.setText(self.notice)
        elif self.view.dirty:
            self.view.status.setText("List refreshed; unsaved draft preserved")
        elif bounded_search:
            self.view.status.setText(
                "Domain text search · first 100 matches · list filters/calendar modes are separate"
            )

    def select(self, entity_id: str) -> None:
        if self.pending:
            self.view.status.setText("Wait for the pending mutation before changing selection")
            return
        if not self.view.permit_discard(after_save=lambda: self.select(entity_id)):
            return
        self.session.state.selections[self.domain] = entity_id
        self.on_detail()
        self.request(
            Action(
                f"{self.domain}.get_{self.domain[:-1] if self.domain != 'reminders' else 'reminder'}",
                (entity_id,),
                (("include_deleted", True),),
            ),
            "selection",
            self._loaded,
            read=True,
        )

    def _loaded(self, value: Any, error: Exception | None) -> None:
        if error:
            if getattr(error, "code", "") == "not_found" and not self.view.dirty:
                self.view.load()
            self.view.status.setText(error_text(error))
        elif not self.view.dirty:
            self.view.load(value)
            self.editability()
            self.view.status.setText("")

    def editability(self) -> None:
        row = self.view.current
        allowed = row is None or row.deleted_at_utc is None
        if self.domain == "reminders" and row is not None:
            allowed = allowed and row.status in {"PENDING", "TRIGGERED", "OVERDUE"}
        self.view.editor_area.setEnabled(allowed and not self.pending)
        self.view.buttons["save"].setEnabled(allowed and not self.pending)
        if self.domain == "timers":
            for key, control in self.view.fields.items():
                control.setEnabled(
                    allowed
                    and not self.pending
                    and (key == "name" or row is None or row.state == "READY")
                )

    def set_pending(self, pending: bool) -> None:
        self.pending = pending
        self.view.button_area.setEnabled(not pending and self.panel.content.isEnabled())
        self.view.records.setEnabled(not pending)
        self.editability()

    def complete_row(self, identifier: str, checked: bool) -> None:
        if self.domain != "tasks":
            return
        row = next((row for row in self.rows if row.id == identifier), None)
        if row is None:
            return
        if self.pending:
            return
        if not self.view.permit_discard(after_save=lambda: self.complete_row(identifier, checked)):
            return
        if self.view.current is not None and self.view.current.id == identifier:
            row = self.view.current
        self.set_pending(True)
        self.request(
            Action(
                "tasks.complete_task" if checked else "tasks.reopen_task", (row.id, row.version)
            ),
            "mutation",
            self._saved,
        )

    def save(self, start_after: bool = False) -> None:
        try:
            if self.pending:
                raise ValueError("Wait for this record's pending mutation")
            if self.view.current is not None and self.view.current.deleted_at_utc is not None:
                raise ValueError("Deleted records are read-only. Restore explicitly first.")
            data = self.view.values()
            folder = data.pop("folder_id", "") or None
            tags = self.view.tag_ids()
            if self.domain == "tasks":
                zone, fold = data.pop("timezone") or "Asia/Calcutta", data.pop("fold")
                for key in ("start_at_utc", "due_at_utc"):
                    data[key] = local_instant(data[key], zone, fold)
            if self.domain == "reminders":
                fold = data.pop("fold")
                data["due_at_utc"] = local_instant(
                    data["due_at_utc"], data["scheduled_timezone"], fold
                )
            for key in ("task_id", "note_id", "preset_id"):
                if key in data:
                    data[key] = data[key] or None
            if "planned_duration_ms" in data:
                data["planned_duration_ms"] = (
                    int(data["planned_duration_ms"]) if data["planned_duration_ms"] else None
                )
            current = self.view.current
            create, update = cast(Any, SPECS[self.domain][2]), cast(Any, SPECS[self.domain][3])
            action: Action | None
            singular = self.domain[:-1]
            if current is None:
                if self.domain in {"notes", "tasks"}:
                    data.update(folder_id=folder, tag_ids=tags)
                action = Action(f"{self.domain}.create_{singular}", (create.model_validate(data),))
            else:
                original = current.model_dump()
                changed = {k: v for k, v in data.items() if original.get(k) != v}
                action = (
                    Action(
                        f"{self.domain}.update_{singular}",
                        (
                            current.id,
                            update.model_validate({"expected_version": current.version, **changed}),
                        ),
                    )
                    if changed
                    else None
                )
            application, domain = self.session.application, self.domain

            def operation() -> Any:
                row = dispatch(application, action) if action else current
                if start_after:
                    try:
                        return dispatch(
                            application, Action("timers.start_timer", (row.id, row.version))
                        )
                    except Exception as exc:
                        return PartialSave(row, ("READY timer created",), exc)
                return (
                    save_relationships(
                        application, domain, row, folder, tags, text_saved=action is not None
                    )
                    if domain in {"notes", "tasks"}
                    else row
                )

            self.set_pending(True)
            self.session.run(operation, f"{domain}:save", self._saved)
        except Exception as exc:
            self.view.status.setText(error_text(exc))
            self.set_pending(False)

    def _saved(self, value: Any, error: Exception | None) -> None:
        self.set_pending(False)
        if error:
            self.view.after_save = None
            self.notice = error_text(error)
            self.view.status.setText(self.notice)
            return
        if isinstance(value, PartialSave):
            self.view.current = value.view
            if value.error:
                self.view.after_save = None
                if self.domain == "timers":
                    self.view.load(value.view)
                progress = (
                    f"Saved {', '.join(value.completed)}"
                    if value.completed
                    else "No changes committed"
                )
                self.notice = f"{progress}; remaining changes kept. {error_text(value.error)}"
                self.view.status.setText(self.notice)
                return
            value = value.view
        if value is not None:
            self.view.load(value)
            self.notice = None
            self.editability()
        else:
            self.view.status.setText("No expired active timer to record")
        self.refresh()
        continuation, self.view.after_save = self.view.after_save, None
        if continuation:
            continuation()

    def intent(self, name: str) -> None:
        if name == "filters":
            self.filters()
        elif name == "create_start":
            if self.view.current is not None:
                self.view.status.setText("Open New first; Create & start is two explicit commands")
                return
            self.save(start_after=True)
        elif name == "save":
            self.save()
        elif name == "new":
            if self.pending:
                self.view.status.setText("Wait for the pending mutation before opening a draft")
                return
            if self.view.permit_discard(after_save=lambda: self.intent("new")):
                self.session.state.next_generation(f"{self.domain}:selection")
                self.session.state.selections[self.domain] = None
                self.on_detail()
                self.view.load(
                    values={
                        "timezone": "Asia/Calcutta",
                        "scheduled_timezone": "Asia/Calcutta",
                        "priority": "NORMAL",
                    }
                )
        elif name in {"refresh", "next", "previous"}:
            if name == "next":
                self.offset += 50
            elif name == "previous":
                self.offset = max(0, self.offset - 50)
            self.refresh()
        elif name == "more":
            self.more()
        else:
            self.mutate(name)

    def more(self) -> None:
        names = list(COMMANDS[self.domain])
        names += ["reload", "review_latest"]
        if self.domain == "reminders":
            names += ["snooze", "history"]
        if self.domain == "timers":
            names += [
                "history",
                "presets",
                "focus",
                "short_break",
                "long_break",
                "sequence",
                "advance",
                "cancel_sequence",
                "stats",
                "record",
            ]
        labels = {
            name: {
                "complete_timer": "Finish now",
                "cancel_timer": "Cancel timer",
                "record": "Record completion",
                "advance": "Next focus phase (explicit)",
                "sequence": "Create eight-phase sequence (READY)",
                "focus": "Create focus (READY)",
                "short_break": "Create short break (READY)",
                "long_break": "Create long break (READY)",
                "cancel_sequence": "Cancel sequence",
                "presets": "Timer presets",
            }.get(name, name.replace("_", " ").title())
            for name in names
        }
        prompts.choose(
            self.view,
            "Actions",
            "Action",
            list(labels.values()),
            lambda choice: self.mutate(
                next(name for name, label in labels.items() if label == choice)
            ),
        )

    def mutate(self, name: str) -> None:
        row = self.view.current
        try:
            if self.pending:
                raise ValueError("Wait for this record's pending mutation")
            if name == "review_latest":
                self.review_latest()
                return
            if name == "reload":
                if row and self.view.permit_discard():
                    self.select(row.id)
                return
            if self.domain == "timers" and name in {
                "focus",
                "short_break",
                "long_break",
                "sequence",
                "advance",
                "cancel_sequence",
                "stats",
                "record",
                "presets",
            }:
                self.focus_action(name)
                return
            if row is None:
                raise ValueError("Select a record first")
            if name in COMMANDS[self.domain] or name == "snooze":
                if not self.view.permit_discard(after_save=lambda: self.mutate(name)):
                    return
                row = self.view.current
            if name == "history":
                action = (
                    Action("timers.list_timer_runs", (row.id,), (("include_deleted_timer", True),))
                    if self.domain == "timers"
                    else Action("reminders.list_reminder_events", (row.id,))
                )
                self.request(
                    action,
                    "history",
                    lambda value, error: self.show_history(value, error),
                    read=True,
                )
                return
            if name == "snooze":
                self.on_detail()

                def submitted(text: str) -> None:
                    sheet = prompts.pending(self.view)
                    action = Action(
                        "reminders.snooze_reminder",
                        (
                            row.id,
                            SnoozeReminder.model_validate(
                                {"expected_version": row.version, "due_at_utc": text}
                            ),
                        ),
                    )
                    self.set_pending(True)

                    def saved(value: Any, error: Exception | None) -> None:
                        prompts.finish(sheet, error)
                        self._saved(value, error)

                    try:
                        self.session.call(action, f"{self.domain}:mutation", saved)
                    except Exception:
                        self.set_pending(False)
                        raise

                prompts.ask(self.view, "Snooze", "Future ISO time with offset", "", submitted)
                return
            elif name in COMMANDS[self.domain]:
                action = Action(f"{self.domain}.{name}", (row.id, row.version))
            else:
                raise ValueError("Unsupported action")
            self.set_pending(True)
            self.request(action, "mutation", self._saved)
        except Exception as exc:
            self.view.status.setText(error_text(exc))
            self.set_pending(False)

    def show_history(self, value: Any, error: Exception | None) -> None:
        if error:
            self.view.status.setText(error_text(error))
        else:
            lines = []
            for row in value:
                if self.domain == "timers":
                    lines.append(
                        f"{row.started_at_utc.astimezone().isoformat(timespec='seconds')} · {row.outcome} · {row.actual_duration_ms} ms · task {row.task_id or 'none'}"
                    )
                else:
                    lines.append(
                        f"{row.occurred_at_utc.astimezone().isoformat(timespec='seconds')} · {row.event_type} · {row.previous_status or 'none'} → {row.new_status or 'none'}"
                    )
            self.view.history.setPlainText("\n".join(lines))
            self.on_detail()
            self.view.history.setVisible(bool(lines))
            self.view.status.setText(f"{len(lines)} history records" if lines else "No history")

    def filters(self) -> None:
        """List filters use domain query contracts, never calendar calculations."""
        previous = getattr(self, "filter_dialog", None)
        if previous is not None:
            previous.close()
            previous.deleteLater()
        dialog = QDialog(self.view)
        dialog.setWindowTitle("List filters (search and calendar modes are separate)")
        form = QFormLayout(dialog)
        folder, tag = QComboBox(), QComboBox()
        source = cast(QComboBox, self.view.fields["folder_id"])
        for index in range(source.count()):
            folder.addItem(
                "All folders" if index == 0 else source.itemText(index), source.itemData(index)
            )
        tag.addItem("All tags", None)
        for index in range(self.view.tags.count()):
            item = self.view.tags.item(index)
            tag.addItem(item.text(), item.data(Qt.ItemDataRole.UserRole))
        folder.setCurrentIndex(max(0, folder.findData(self.folder_filter)))
        tag.setCurrentIndex(max(0, tag.findData(self.tag_filter)))
        form.addRow("Folder", folder)
        form.addRow("Tag", tag)
        priority, status = QComboBox(), QComboBox()
        priority.addItems(["Any", "LOW", "NORMAL", "HIGH", "URGENT"])
        status.addItems(["Any", "TODO", "IN_PROGRESS", "COMPLETED", "CANCELLED"])
        priority.setCurrentText(self.priority_filter or "Any")
        status.setCurrentText(self.status_filter or "Any")
        if self.domain == "tasks":
            form.addRow("Priority", priority)
            form.addRow("Status", status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def accepted() -> None:
            self.folder_filter, self.tag_filter = folder.currentData(), tag.currentData()
            self.priority_filter = (
                None if priority.currentText() == "Any" else priority.currentText()
            )
            self.status_filter = None if status.currentText() == "Any" else status.currentText()
            self.offset = 0
            self.view.search.clear()
            self.view.mode.setCurrentText("All")
            self.refresh()

        dialog.accepted.connect(accepted)
        self.filter_dialog = dialog
        dialog.setModal(False)
        dialog.show()

    def review_latest(self) -> None:
        row = self.view.current
        if row is None:
            self.view.status.setText("Select an existing record to review")
            return

        def reviewed(latest: Any, error: Exception | None) -> None:
            if error:
                self.view.status.setText(error_text(error))
                return
            choice = QMessageBox.question(
                self.view,
                "Review changed record",
                f"Latest version: {latest.version}. Yes reloads and discards the draft. No keeps your input against this reviewed version; inspect it, then explicitly Save. Cancel keeps the old draft unchanged.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self.view.load(latest)
            elif choice == QMessageBox.StandardButton.No:
                if latest.deleted_at_utc:
                    self.view.status.setText(
                        "Latest record is deleted. Restore before editing; draft retained."
                    )
                    return
                self.view.current = latest
                self.view.dirty = True
                self.view.status.setText(
                    "Latest version reviewed. Draft kept; inspect and explicitly Save to reapply."
                )
            self.editability()

        self.request(
            Action(
                f"{self.domain}.get_{self.domain[:-1]}", (row.id,), (("include_deleted", True),)
            ),
            "selection",
            reviewed,
            read=True,
        )

    def refresh_sequence(self) -> None:
        application = self.session.application

        def operation() -> Any:
            rows = dispatch(
                application,
                Action("focus.list_sequences", (FocusSequenceQuery(states=("ACTIVE",)),)),
            )
            return (
                dispatch(application, Action("focus.get_sequence", (rows[0].id,))) if rows else None
            )

        def loaded(value: Any, error: Exception | None) -> None:
            if error:
                self.focus_status.setText(error_text(error))
            elif value:
                phase = value.current_session
                self.focus_status.setText(
                    f"Focus {value.current_phase_index + 1}/8 · {phase.phase_kind.replace('_', ' ').title()} · {phase.timer.effective_state.title()}"
                )
            else:
                self.focus_status.setText("")
            self.focus_status.setVisible(self.view.expanded and bool(self.focus_status.text()))

        self.session.run(operation, "focus:presentation", loaded, read=True)

    def focus_action(
        self,
        name: str,
        task_id: str | None = None,
        *,
        use_selected_task: bool = True,
        on_result: Any = None,
    ) -> None:
        if name not in {"stats", "record"} and not self.view.permit_discard():
            return
        if self.pending:
            raise ValueError("Wait for the pending timer/focus mutation")
        if name not in {"stats", "presets"}:
            self.set_pending(True)
        task = (self.view.text("task_id") or None) if use_selected_task else task_id

        def finished(value: Any, error: Exception | None) -> None:
            self._focus_saved(value, error)
            if on_result:
                on_result(value, error or (value.error if isinstance(value, PartialSave) else None))

        if name in {"focus", "short_break", "long_break"}:
            action = Action(
                "focus.create_session",
                (
                    CreateFocusSession.model_validate(
                        {
                            "phase_kind": "FOCUS" if name == "focus" else name.upper(),
                            "task_id": task if name == "focus" else None,
                        }
                    ),
                ),
            )
            self.request(
                action,
                "focus",
                finished,
            )
        elif name == "sequence":
            self.request(
                Action("focus.create_sequence", (CreateFocusSequence(task_id=task),)),
                "focus",
                finished,
            )
        elif name == "stats":
            self.request(
                Action("focus.get_stats", (FocusStatsQuery(task_id=task),)),
                "stats",
                lambda value, error: self.view.status.setText(
                    error_text(error)
                    if error
                    else f"Completed focus sessions: {value.completed_focus_sessions} · Focus time: {value.focused_duration_ms} ms"
                ),
                read=True,
            )
        elif name == "record":
            self.request(Action("timers.reconcile_expired_timer"), "record", self._saved)
        elif name in {"advance", "cancel_sequence"}:
            application = self.session.application

            def operation() -> Any:
                recorded = False
                sequences = dispatch(
                    application,
                    Action("focus.list_sequences", (FocusSequenceQuery(states=("ACTIVE",)),)),
                )
                if not sequences:
                    raise ValueError("No active sequence")
                sequence = dispatch(application, Action("focus.get_sequence", (sequences[0].id,)))
                if name == "advance" and sequence.current_session.timer.needs_reconciliation:
                    dispatch(application, Action("timers.reconcile_expired_timer"))
                    recorded = True
                    sequence = dispatch(application, Action("focus.get_sequence", (sequence.id,)))
                command = (
                    AdvanceFocusSequence(expected_version=sequence.version)
                    if name == "advance"
                    else CancelFocusSequence(expected_version=sequence.version)
                )
                try:
                    return dispatch(
                        application,
                        Action(
                            "focus.advance_sequence"
                            if name == "advance"
                            else "focus.cancel_sequence",
                            (sequence.id, command),
                        ),
                    )
                except Exception as exc:
                    if recorded:
                        return PartialSave(
                            sequence.current_session.timer, ("completion recorded",), exc
                        )
                    raise

            try:
                self.session.run(operation, "focus:sequence", finished)
            except Exception:
                self.set_pending(False)
                raise
        elif name == "presets":
            self.preset_dialog()

    def _focus_saved(self, value: Any, error: Exception | None) -> None:
        if isinstance(value, PartialSave):
            self._saved(value, error)
            return
        timer = (
            (value.current_session.timer if hasattr(value, "current_session") else value.timer)
            if value
            else None
        )
        self._saved(timer, error)
        if value and not error:
            sequence_hint = (
                "READY phases require explicit Start"
                if getattr(value, "state", None) == "ACTIVE"
                else "Terminal sequence · no phase may be started"
            )
            self.notice = (
                f"Sequence {value.state} · phase {value.current_phase_index + 1}/8 · sequence version {value.version} · {sequence_hint}"
                if hasattr(value, "current_session")
                else f"{value.phase_kind} · READY · Start explicitly"
            )
            self.view.status.setText(self.notice)

    def preset_dialog(self) -> None:
        def listed(value: Any, error: Exception | None) -> None:
            if error:
                self.view.status.setText(error_text(error))
                return
            choices = ["Create"] + [f"{p.name} · {p.duration_ms} ms · {p.id}" for p in value]

            def selected(choice: str) -> None:
                self.on_detail()
                if choice == "Create":

                    def named(text: str) -> None:
                        prompts.ask(
                            self.view,
                            "Preset",
                            "Milliseconds",
                            "1500000",
                            lambda number: submit(
                                Action(
                                    "timers.create_preset",
                                    (CreateTimerPreset(name=text, duration_ms=int(number)),),
                                )
                            ),
                            integer=True,
                        )

                    prompts.ask(self.view, "Preset", "Name", "", named)
                    return
                preset = value[choices.index(choice) - 1]

                def verb_selected(verb: str) -> None:
                    try:
                        if verb == "Apply":
                            if preset.deleted_at_utc:
                                raise ValueError("Deleted presets cannot be newly applied")
                            if self.view.current is not None and self.view.current.state != "READY":
                                raise ValueError("Presets can be applied only to READY timers")
                            control = cast(QComboBox, self.view.fields["preset_id"])
                            control.setCurrentIndex(control.findData(preset.id))
                            cast(QLineEdit, self.view.fields["planned_duration_ms"]).clear()
                            self.view.dirty = True
                        elif verb in {"Delete", "Restore"}:
                            submit(
                                Action(f"timers.{verb.lower()}_preset", (preset.id, preset.version))
                            )
                        elif verb == "Rename":
                            prompts.ask(
                                self.view,
                                "Rename",
                                "Name",
                                preset.name,
                                lambda text: submit(
                                    Action(
                                        "timers.update_preset",
                                        (
                                            preset.id,
                                            UpdateTimerPreset(
                                                expected_version=preset.version, name=text
                                            ),
                                        ),
                                    )
                                ),
                            )
                        elif verb == "Duration":
                            prompts.ask(
                                self.view,
                                "Duration",
                                "Value",
                                str(preset.duration_ms),
                                lambda number: submit(
                                    Action(
                                        "timers.update_preset",
                                        (
                                            preset.id,
                                            UpdateTimerPreset(
                                                expected_version=preset.version,
                                                duration_ms=int(number),
                                            ),
                                        ),
                                    )
                                ),
                                integer=True,
                            )
                        else:
                            prompts.ask(
                                self.view,
                                "Move",
                                "Value",
                                str(preset.sort_order),
                                lambda number: submit(
                                    Action(
                                        "timers.move_preset",
                                        (
                                            preset.id,
                                            MoveTimerPreset(
                                                expected_version=preset.version,
                                                position=int(number),
                                            ),
                                        ),
                                    )
                                ),
                                integer=True,
                            )
                    except Exception as exc:
                        self.view.status.setText(error_text(exc))

                prompts.choose(
                    self.view,
                    "Preset",
                    "Action",
                    ["Apply", "Rename", "Duration", "Move", "Delete", "Restore"],
                    verb_selected,
                )

            def submit(action: Action) -> None:
                sheet = prompts.pending(self.view)

                def saved(result: Any, error: Exception | None) -> None:
                    prompts.finish(sheet, error)
                    self.view.status.setText(error_text(error) if error else "Preset saved")

                self.session.call(action, "presets:mutation", saved)

            prompts.choose(self.view, "Presets", "Choose", choices, selected)

        self.request(
            Action("timers.list_presets", (TimerPresetQuery(include_deleted=True),)),
            "presets",
            listed,
            read=True,
        )
