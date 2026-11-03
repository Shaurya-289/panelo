"""Application hierarchy navigation, never a filesystem browser."""

from typing import Any

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from productivity_app.desktop.ui import prompts
from productivity_app.desktop.ui.actions import Action, dispatch
from productivity_app.desktop.ui.editors import InlineStatus
from productivity_app.desktop.ui.session import DesktopUiSession, error_text
from productivity_app.schemas.folders import CreateFolder, MoveFolder, RenameFolder
from productivity_app.schemas.notes import NoteQuery
from productivity_app.schemas.tags import CreateTag, RenameTag
from productivity_app.schemas.tasks import TaskQuery


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
        panel.bind_real(self)
        panel.parent_tab.clicked.connect(
            lambda: self.enter(self.current.parent_id if self.current else None)
        )
        self.status = InlineStatus()
        self.status.hide()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        panel.content_layout.addWidget(self.status)
        self.controls = QWidget(panel.controls)
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        self.add_button = QToolButton(self.controls)
        self.add_button.setText("+")
        self.add_button.setAccessibleName("Create folder")
        self.add_button.setToolTip("New folder")
        self.add_button.clicked.connect(lambda: self.actions("New folder"))
        self.options_button = QToolButton(self.controls)
        self.options_button.setText("⋯")
        self.options_button.setAccessibleName("Files options")
        self.options_button.setToolTip("Folder and tag options")
        self.options_button.clicked.connect(lambda: self.actions())
        for button in (self.add_button, self.options_button):
            button.setFixedSize(28, 28)
            controls.addWidget(button)
        panel.control_layout.insertWidget(0, self.controls)

    def enter(self, identifier: str | None) -> None:
        self.panel.current_folder_id = identifier
        self.refresh()

    def refresh(self) -> None:
        if not self.session.runner.accepting:
            return
        identifier, application = self.panel.current_folder_id, self.session.application

        def operation() -> tuple[Any, ...]:
            current = (
                dispatch(application, Action("folders.get_folder", (identifier,)))
                if identifier
                else None
            )
            parent = (
                dispatch(application, Action("folders.get_folder", (current.parent_id,)))
                if current and current.parent_id
                else None
            )
            children = dispatch(application, Action("folders.list_children", (identifier,)))
            # At root these queries mean ALL, not root-only. They are presented
            # by explicit All notes/tasks navigation, not invented root rows.
            notes = (
                dispatch(
                    application,
                    Action("notes.list_notes", (NoteQuery(folder_id=identifier, limit=200),)),
                )
                if identifier
                else ()
            )
            tasks = (
                dispatch(
                    application,
                    Action("tasks.list_tasks", (TaskQuery(folder_id=identifier, limit=200),)),
                )
                if identifier
                else ()
            )
            return current, parent, children, notes, tasks

        def loaded(value: Any, error: Exception | None) -> None:
            if error:
                self.status.setText(error_text(error))
                if identifier:
                    self.panel.current_folder_id = self.parent_id
                    self.parent_id = None
                    self.refresh()
                return
            self.current = value[0]
            self.parent_id = self.current.parent_id if self.current else None
            self.panel.render_real(*value)
            self.panel.title_label.setText(self.current.name if self.current else "FILES")
            self.status.setText("")

        try:
            self.session.run(operation, "files:list", loaded, read=True)
        except RuntimeError as exc:
            self.status.setText(error_text(exc))

    def open_all(self, domain: str) -> None:
        presenter = self.presenters[domain]
        presenter.folder_filter = None
        presenter.offset = 0
        presenter.view.mode.setCurrentText("All")
        presenter.refresh()
        self.workspace.maximize("notes" if domain == "notes" else "todo")

    def open_entity(self, domain: str, identifier: str) -> None:
        self.workspace.maximize("notes" if domain == "notes" else "todo")
        self.presenters[domain].select(identifier)

    def submit(self, action: Action) -> None:
        sheet = prompts.pending(self.panel.content)

        def saved(value: Any, error: Exception | None) -> None:
            prompts.finish(sheet, error)
            if error:
                self.status.setText(error_text(error))
                return
            if value.deleted_at_utc:
                self.known_deleted[value.id] = value
                self.panel.current_folder_id = value.parent_id
            else:
                self.known_deleted.pop(value.id, None)
            self.refresh()

        try:
            self.session.call(action, "files:mutation", saved)
        except Exception as exc:
            prompts.finish(sheet, exc)
            self.status.setText(error_text(exc))

    def actions(self, initial: str | None = None) -> None:
        names = ["New folder", "New note here", "New task here", "Tags"]
        if self.current:
            names += ["Notes here", "Tasks here", "Rename", "Move", "Reorder", "Delete"]
        if self.known_deleted:
            names += ["Restore known folder"]

        def selected(name: str) -> None:
            try:
                if name in {"Notes here", "Tasks here"}:
                    domain = "notes" if name == "Notes here" else "tasks"
                    presenter = self.presenters[domain]
                    presenter.folder_filter = self.panel.current_folder_id
                    presenter.offset = 0
                    presenter.view.search.clear()
                    presenter.view.mode.setCurrentText("All")
                    presenter.refresh()
                    self.workspace.maximize("notes" if domain == "notes" else "todo")
                    return
                if name in {"New note here", "New task here"}:
                    domain = "notes" if name == "New note here" else "tasks"
                    presenter = self.presenters[domain]
                    if presenter.pending:
                        raise ValueError("Wait for the pending mutation before opening a draft")
                    if not presenter.view.permit_discard():
                        return
                    presenter.intent("new")
                    control = presenter.view.fields["folder_id"]
                    control.setCurrentIndex(control.findData(self.panel.current_folder_id))
                    self.workspace.maximize("notes" if domain == "notes" else "todo")
                    return
                if name == "Tags":
                    self.tags()
                    return
                if name == "Restore known folder":

                    def restore(identifier: str) -> None:
                        row = self.known_deleted[identifier]
                        self.submit(Action("folders.restore_folder", (row.id, row.version)))

                    prompts.choose(
                        self.panel,
                        "Restore",
                        "Known deleted folder",
                        list(self.known_deleted),
                        restore,
                    )
                    return
                self.workspace.maximize("files")
                if name in {"New folder", "Rename"}:
                    row = self.current
                    folder_id = self.panel.current_folder_id

                    def named(text: str) -> None:
                        action = (
                            Action(
                                "folders.create_folder",
                                (CreateFolder(name=text, parent_id=folder_id),),
                            )
                            if name == "New folder"
                            else Action(
                                "folders.rename_folder",
                                (
                                    row.id,
                                    RenameFolder(name=text, expected_version=row.version),
                                ),
                            )
                        )
                        self.submit(action)

                    prompts.ask(
                        self.panel.content,
                        name,
                        "Name",
                        self.current.name if name == "Rename" else "",
                        named,
                    )
                elif name == "Delete":
                    from PySide6.QtWidgets import QMessageBox

                    if (
                        QMessageBox.question(
                            self.panel,
                            "Delete folder",
                            "Soft-delete this folder? Contents follow backend promotion rules.",
                        )
                        == QMessageBox.StandardButton.Yes
                    ):
                        self.submit(
                            Action("folders.delete_folder", (self.current.id, self.current.version))
                        )
                else:
                    row = self.current

                    def positioned(parent: str | None) -> None:
                        def number(text: str) -> None:
                            action = (
                                Action(
                                    "folders.move_folder",
                                    (
                                        row.id,
                                        MoveFolder(
                                            parent_id=parent or None,
                                            position=int(text),
                                            expected_version=row.version,
                                        ),
                                    ),
                                )
                                if name == "Move"
                                else Action(
                                    "folders.reorder_folder", (row.id, int(text), row.version)
                                )
                            )
                            self.submit(action)

                        prompts.ask(
                            self.panel.content,
                            name,
                            "Sibling position",
                            str(row.sort_order),
                            number,
                            integer=True,
                        )

                    if name == "Move":
                        prompts.ask(
                            self.panel.content,
                            "Move",
                            "Parent folder UUID; blank for root",
                            "",
                            positioned,
                        )
                    else:
                        positioned(row.parent_id)
            except Exception as exc:
                self.status.setText(error_text(exc))

        if initial is not None:
            selected(initial)
        else:
            prompts.choose(self.panel, "Application hierarchy", "Action", names, selected)

    def tags(self) -> None:
        def loaded(value: Any, error: Exception | None) -> None:
            if error:
                self.status.setText(error_text(error))
                return
            choices = ["Create"] + [f"{row.name} · {row.id}" for row in value]

            def submit(action: Action) -> None:
                sheet = prompts.pending(self.panel.content)

                def saved(result: Any, error: Exception | None) -> None:
                    prompts.finish(sheet, error)
                    self.status.setText(error_text(error) if error else "Tag saved")

                try:
                    self.session.call(action, "tags:mutation", saved)
                except Exception as exc:
                    prompts.finish(sheet, exc)
                    self.status.setText(error_text(exc))

            def selected(choice: str) -> None:
                self.workspace.maximize("files")
                if choice == "Create":
                    prompts.ask(
                        self.panel.content,
                        "Create tag",
                        "Name",
                        "",
                        lambda text: submit(Action("tags.create_tag", (CreateTag(name=text),))),
                    )
                    return
                row = value[choices.index(choice) - 1]

                def verb_selected(verb: str) -> None:
                    if verb == "Rename":
                        prompts.ask(
                            self.panel.content,
                            "Rename tag",
                            "Name",
                            row.name,
                            lambda text: submit(
                                Action(
                                    "tags.rename_tag",
                                    (row.id, RenameTag(name=text, expected_version=row.version)),
                                )
                            ),
                        )
                    else:
                        submit(Action(f"tags.{verb.lower()}_tag", (row.id, row.version)))

                prompts.choose(
                    self.panel, "Tag", "Action", ["Rename", "Delete", "Restore"], verb_selected
                )

            prompts.choose(self.panel, "Tags", "Tag", choices, selected)

        try:
            self.session.call(
                Action("tags.list_tags", kwargs=(("include_deleted", True),)),
                "tags:list",
                loaded,
                read=True,
            )
        except RuntimeError as exc:
            self.status.setText(error_text(exc))
