"""Self-test steps for the shell and the Files section: the overlay capture box, the Files
panel, click-through, the Full App's window state, and the library itself.  Ported from the
prototype's ``main.py``."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMimeData, QPoint, QRect, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QImage, QWindow
from PySide6.QtTest import QTest

from panelo.desktop.shell import FullWindowState
from panelo.files.library import FileLibrary
from panelo.ui.bridge.library import Library
from panelo.ui.selftest.driver import Step, js
from panelo.ui.selftest.harness import hard_delete
from panelo.ui.selftest.seed import seed_library

js_to_python = js

def library_checks(check: Any) -> None:
    """Exercise FileLibrary in a fresh temp folder (hard delete, never the Recycle Bin)."""
    clipboard = QGuiApplication.clipboard()
    saved = QMimeData()  # restore the user's clipboard afterwards
    current = clipboard.mimeData()
    if current is not None:
        for fmt in current.formats():
            saved.setData(fmt, current.data(fmt))
    tmp = Path(tempfile.mkdtemp(prefix="selftest-library-"))
    library = Library(FileLibrary(tmp / "lib", trash=hard_delete), stub_launch=True, sync_jobs=True)
    seed_library(library.root)
    try:

        def names(rel: str) -> list[str]:
            return [e["name"] for e in library.list(rel)]

        physics = names("Study/Physics")
        pdf = library.root / "Study/Physics/Chapter 3 - Kinematics.pdf"
        png = QImage(str(library.root / "Study/Physics/Lab graph.png"))
        check(
            "library seed: folders and files exist",
            names("") == ["Coding", "Personal", "Study"]
            and names("Study") == ["Chemistry", "Maths", "Physics"]
            and physics == ["Chapter 3 - Kinematics.pdf", "Lab graph.png"]
            and pdf.read_bytes().startswith(b"%PDF") and not png.isNull()
            and names("Coding") == ["ideas.txt"] and names("Personal") == ["Trip budget.csv"]
            and names("Study/Maths") == ["Formulas.md"],
        )
        entry = library.list("Study/Physics")[0]
        check(
            "library list: dict fields (kind pdf, size text, file url)",
            entry["kind"] == "pdf" and entry["ext"] == "pdf" and not entry["isDir"]
            and entry["size"].endswith(" KB") and entry["url"].startswith("file:///")
            and entry["rel"] == "Study/Physics/Chapter 3 - Kinematics.pdf",
        )

        source = tmp / "src"
        source.mkdir()
        (source / "notes.txt").write_text("hello", encoding="utf-8")
        error = library.addFiles([QUrl.fromLocalFile(str(source / "notes.txt")).toString()], "Coding")
        check(
            "addFiles copies a file in (original stays)",
            error == "" and (library.root / "Coding/notes.txt").read_text(encoding="utf-8") == "hello"
            and (source / "notes.txt").exists(),
        )
        error = library.addFiles([QUrl.fromLocalFile(str(source / "notes.txt")).toString()], "Coding")
        check("name clash gives ' (2)'", error == "" and (library.root / "Coding/notes (2).txt").exists())

        check("createFolder", library.createFolder("Coding", "Scratch") == ""
              and (library.root / "Coding/Scratch").is_dir())
        check(
            "rename works; empty / bad-character / clashing names give errors",
            library.rename("Coding/Scratch", "Projects") == ""
            and (library.root / "Coding/Projects").is_dir()
            and library.rename("Coding/Projects", "a/b") != ""
            and library.rename("Coding/Projects", "  ") != ""
            and library.rename("Coding/Projects", "ideas.txt") != "",
        )
        check(
            "move works; moving a folder into itself gives an error",
            library.move("Coding/notes.txt", "Personal") == ""
            and (library.root / "Personal/notes.txt").exists()
            and library.move("Study", "Study/Physics") != ""
            and (library.root / "Study/Physics").is_dir(),
        )
        check(
            "remove (hard delete under selftest)",
            library.remove("Personal/notes.txt") == "" and not (library.root / "Personal/notes.txt").exists()
            and library.remove("Coding/Projects") == "" and not (library.root / "Coding/Projects").exists(),
        )
        found = [e["name"] for e in library.search("KINEMAT")]
        check("search finds the seeded PDF (case-insensitive)", found == ["Chapter 3 - Kinematics.pdf"])
        check("folders() lists every folder rel", "Study/Physics" in library.folders() and "Coding" in library.folders())
        check(
            "paths that escape the root are rejected",
            library.list("..") == [] and library.list("../..") == [] and library.list("C:/Windows") == []
            and library.remove("../src/notes.txt") != "" and library.rename("..", "x") != ""
            and library.addFiles([str(source / "notes.txt")], "..") != ""
            and library.move("Coding/ideas.txt", "..") != "" and (source / "notes.txt").exists(),
        )

        error = library.copyToClipboard("Study/Physics/Chapter 3 - Kinematics.pdf")
        mime = clipboard.mimeData()
        urls = mime.urls() if mime is not None else []
        check(
            "copyToClipboard puts a file URL (and copy drop effect) on the clipboard",
            error == "" and len(urls) == 1 and urls[0].toLocalFile().endswith("Chapter 3 - Kinematics.pdf")
            and mime.hasFormat("Preferred DropEffect"),
        )
        error = library.pasteInto("Coding")
        check("pasteInto adds the clipboard file to another folder",
              error == "" and (library.root / "Coding/Chapter 3 - Kinematics.pdf").exists())
        clipboard.setText("not files")
        check("pasteInto with no files says so", library.pasteInto("Coding") == "Clipboard has no files")
    except Exception as exc:  # a crash is a failure, not a hang
        check(f"library checks crashed: {exc!r}", False)
    finally:
        library.close()
        shutil.rmtree(tmp, ignore_errors=True)
        clipboard.setMimeData(saved)


def ui_steps(env: Any) -> list[Step]:
    """Real mouse and key events (QTest) sent to the overlay and the Full App windows."""
    controller = env.controller
    overlay, full, library = controller.overlay, controller.full, controller.library
    left, right = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
    no_mod, ctrl = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier
    saved_clipboard = QMimeData()

    def point(window, name: str, index: int = 0) -> QPoint:
        value = js_to_python(window.probe(name, index))
        return QPoint(round(value["x"]), round(value["y"]))

    def click(window, name: str, index: int = 0, button=left) -> None:
        QTest.mouseClick(window, button, no_mod, point(window, name, index))

    def click_left_of(window, name: str, index: int, dx: int) -> None:
        """Click `dx` pixels to the left of a control's centre (outside an open menu)."""
        at = point(window, name, index)
        QTest.mouseClick(window, left, no_mod, QPoint(at.x() - dx, at.y()))

    def move_to(window, name: str, index: int = 0) -> None:
        QTest.mouseMove(window, point(window, name, index))

    def type_text(window, text: str) -> None:
        for char in text:
            QTest.keyClick(window, char)

    def press(window, key, modifier=no_mod) -> None:
        QTest.keyClick(window, key, modifier)

    def newest() -> dict:
        notes = js_to_python(controller.store.property("notes"))
        return notes[0] if notes else {}

    def names(rel: str) -> list[str]:
        return [e["name"] for e in library.list(rel)]

    def capture_closed() -> bool:
        return not overlay.property("captureOpen")

    def still_open() -> bool:
        return controller.overlay_state == "open"

    def stash_clipboard() -> None:
        current = QGuiApplication.clipboard().mimeData()
        if current is not None:
            for fmt in current.formats():
                saved_clipboard.setData(fmt, current.data(fmt))

    def restore_clipboard() -> None:
        QGuiApplication.clipboard().setMimeData(saved_clipboard)

    mode_before: list[str] = []

    def toggle_other_mode() -> None:
        mode_before.append(str(full.property("filesMode")))
        click(full, "gridToggle" if mode_before[0] == "list" else "listToggle")

    def toggle_back_mode() -> None:
        click(full, "gridToggle" if mode_before[0] == "grid" else "listToggle")

    def paste_in_full() -> None:
        """The toolbar's Paste button (Ctrl+V is no longer a shortcut)."""
        stash_clipboard()
        library.copyToClipboard("Study/Physics/Lab graph.png")
        full.setProperty("filesRel", "Personal")
        click(full, "pasteButton")

    def ctrl_v_does_nothing() -> None:
        library.copyToClipboard("Study/Physics/Chapter 3 - Kinematics.pdf")
        full.setProperty("filesRel", "Coding")
        press(full, Qt.Key.Key_V, ctrl)

    # -- Click-through: a click that closes a layer must not reach what is under it --
    ids: dict[str, int] = {}
    done_before: list[list[bool]] = []
    launched_before: list[int] = []

    def task_flags() -> list[bool]:
        return [bool(t["done"]) for t in js_to_python(controller.store.property("tasks"))]

    def hovered_notes() -> int:
        return int(js_to_python(full.hoveredNotes()))

    def nudge_over_note() -> None:
        at = point(full, "noteRightOfDrawer")
        QTest.mouseMove(full, QPoint(at.x() + 3, at.y() + 2))

    def remember_editor_note() -> None:
        ids["full"] = full.property("editorNoteId")

    def remember_overlay_note() -> None:
        ids["overlay"] = overlay.property("editorNoteId")

    def remember_tasks() -> None:
        done_before[:] = [task_flags()]

    def remember_launched() -> None:
        launched_before[:] = [len(library.launched)]

    def show_files_in_full() -> None:
        full.setProperty("filesRel", "Study/Physics")
        full.setProperty("filesMode", "list")
        full.setProperty("section", "files")

    def click_through_steps() -> list:
        tag = "click-through"
        pdf = "Study/Physics/Chapter 3 - Kinematics.pdf"
        return [
            # Full App: the drawer's scrim
            (0, lambda: full.setProperty("section", "notes"), None),
            (400, lambda: click(full, "menu"), None),
            (400, None, (f"{tag}: the hamburger opens the drawer", lambda: full.property("drawerOpen"))),
            # ...the drawer panel itself must also block what lies behind it
            (0, lambda: move_to(full, "noteUnderDrawerPanel"), None),
            (200, None, (f"{tag}: a note behind the drawer panel gets no hover highlight", lambda: hovered_notes() == 0)),
            (0, lambda: click(full, "noteUnderDrawerPanel"), None),
            (400, None, (f"{tag}: clicking on the drawer panel does not open the note behind it", lambda: not full.property("editorOpen"))),
            # clicking a nav row ("Files") that has a note card behind it (the user's report)
            (0, lambda: click(full, "navFiles"), None),
            (500, None, (f"{tag}: clicking 'Files' in the drawer switches section and opens no note behind it", lambda: full.property("section") == "files" and not full.property("editorOpen"))),
            (0, lambda: full.setProperty("section", "notes"), None),
            (300, lambda: click(full, "menu"), None),
            (400, None, None),
            (0, lambda: move_to(full, "noteRightOfDrawer"), None),
            (200, None, (f"{tag}: a note under the drawer scrim gets no hover highlight", lambda: hovered_notes() == 0)),
            (0, lambda: click(full, "noteRightOfDrawer"), None),
            (100, None, (f"{tag}: clicking the scrim closes the drawer and does not open the note under it", lambda: not full.property("drawerOpen") and not full.property("editorOpen"))),
            (500, None, (f"{tag}: ...and still no editor after the drawer has slid away", lambda: not full.property("editorOpen"))),
            (0, nudge_over_note, None),
            (200, None, (f"{tag}: hover works again once the drawer is closed (the hover check is not vacuous)", lambda: hovered_notes() == 1)),
            # Full App: the editor's scrim
            (0, lambda: click(full, "note", 0), None),
            (500, None, (f"{tag}: clicking a note opens the editor", lambda: full.property("editorOpen"))),
            (0, remember_editor_note, None),
            (0, lambda: click(full, "noteOutsideEditor"), None),
            (80, None, (f"{tag}: clicking the editor scrim over another note does not open that note", lambda: full.property("editorNoteId") == ids["full"])),
            (500, None, (f"{tag}: ...the editor closes and stays closed", lambda: not full.property("editorOpen"))),
            # Full App: right-click menu and the "Move to folder" picker
            (0, show_files_in_full, None),
            (300, remember_launched, None),
            (0, lambda: click(full, "row", 0, right), None),
            (200, None, (f"{tag}: right-click in Files opens the menu", lambda: full.property("filesMenuOpen"))),
            (0, lambda: click_left_of(full, "row", 0, 400), None),
            (200, None, (f"{tag}: clicking outside the menu closes it and does not open the file under it", lambda: not full.property("filesMenuOpen") and len(library.launched) == launched_before[0])),
            (0, lambda: full.demo("files-move"), None),
            (500, None, (f"{tag}: the 'Move to folder' picker opens", lambda: full.property("filesMenuOpen"))),
            (0, lambda: click_left_of(full, "row", 0, 400), None),
            (200, None, (f"{tag}: clicking outside the picker closes it and does not open the file under it", lambda: not full.property("filesMenuOpen") and len(library.launched) == launched_before[0])),
            (0, controller.hide_full, None),
            # Overlay: the editor's scrim
            (0, controller.open_overlay, None),
            (500, lambda: click(overlay, "noteRow", 0), None),
            (500, None, (f"{tag}: overlay: clicking a note row opens the editor", lambda: overlay.property("editorOpen"))),
            (0, remember_overlay_note, None),
            (0, lambda: click(overlay, "noteRow", 3), None),
            (80, None, (f"{tag}: overlay: the editor scrim over another note does not open that note", lambda: overlay.property("editorNoteId") == ids["overlay"])),
            (500, None, (f"{tag}: overlay: ...the editor closes and stays closed", lambda: not overlay.property("editorOpen") and still_open())),
            # Overlay: the capture box's click-away layer
            (0, lambda: click(overlay, "noteBar"), None),
            (300, lambda: type_text(overlay, "Away note"), None),
            (0, lambda: click(overlay, "noteRow", 1), None),
            (200, None, (f"{tag}: overlay: a click on a note row saves and collapses the capture box, and does not open that note", lambda: capture_closed() and newest().get("body") == "Away note" and not overlay.property("editorOpen"))),
            (0, remember_tasks, None),
            (0, lambda: click(overlay, "noteBar"), None),
            (300, None, (f"{tag}: overlay: the capture box expands again", lambda: overlay.property("captureOpen"))),
            (0, lambda: click(overlay, "todoCheck", 0), None),
            (200, None, (f"{tag}: overlay: a click on a to-do checkbox only collapses the box (the task is not toggled)", lambda: capture_closed() and task_flags() == done_before[0])),
            (0, lambda: click(overlay, "todoCheck", 0), None),
            (200, None, (f"{tag}: overlay: once the box is closed, the checkbox works again", lambda: task_flags() != done_before[0])),
            (0, lambda: click(overlay, "todoCheck", 0), None),
            # Overlay: the time picker
            (200, lambda: overlay.demo("timepicker"), None),
            (400, None, (f"{tag}: overlay: the time picker opens", lambda: overlay.property("pickerOpen"))),
            (0, remember_tasks, None),
            (0, lambda: click(overlay, "todoCheck", 0), None),
            (300, None, (f"{tag}: overlay: clicking outside the time picker closes it and does not toggle the checkbox under it", lambda: not overlay.property("pickerOpen") and task_flags() == done_before[0])),
            # Overlay: opening a file (or Explorer) closes the overlay
            (0, lambda: overlay.demo("files-overlay"), None),
            (300, remember_launched, None),
            (0, lambda: click(overlay, "row", 0), None),
            (500, None, ("overlay: opening a file from the Files panel opens it (stubbed) and closes the overlay", lambda: library.launched[launched_before[0]:] == [("open", pdf)] and controller.overlay_state == "hidden")),
            (0, controller.open_overlay, None),
            (500, lambda: overlay.demo("files-overlay"), None),
            (300, remember_launched, None),
            (0, lambda: click(overlay, "row", 0, right), None),
            (200, lambda: click(overlay, "menuItem", 3), None),
            (500, None, ("overlay: 'Show in Explorer' from the right-click menu (stubbed) closes the overlay", lambda: library.launched[launched_before[0]:] == [("explorer", pdf)] and controller.overlay_state == "hidden")),
        ]

    return [
        # overlay: capture box
        (0, controller.open_overlay, None),
        (500, lambda: click(overlay, "noteBar"), None),
        (150, None, ("ui: clicking 'Take a note…' expands the capture box", lambda: overlay.property("captureOpen"))),
        (0, lambda: type_text(overlay, "Click-away body"), None),
        (0, lambda: click(overlay, "todoHeader"), None),
        (150, None, ("ui: clicking elsewhere in the overlay saves and collapses", lambda: capture_closed() and newest().get("body") == "Click-away body" and still_open())),
        (0, lambda: click(overlay, "noteBar"), None),
        (150, lambda: type_text(overlay, "Esc body"), None),
        (0, lambda: press(overlay, Qt.Key.Key_Escape), None),
        (150, None, ("ui: Esc saves and collapses, and does not close the overlay", lambda: capture_closed() and newest().get("body") == "Esc body" and still_open())),
        (0, lambda: click(overlay, "noteBar"), None),
        (150, lambda: type_text(overlay, "Ctrl body"), None),
        (0, lambda: press(overlay, Qt.Key.Key_Return, ctrl), None),
        (150, None, ("ui: Ctrl+Enter saves and collapses", lambda: capture_closed() and newest().get("body") == "Ctrl body")),
        (0, lambda: click(overlay, "noteBar"), None),
        (150, lambda: click(overlay, "noteTitle"), None),
        (0, lambda: type_text(overlay, "T1"), None),
        (0, lambda: press(overlay, Qt.Key.Key_Return), None),
        (0, lambda: type_text(overlay, "B1"), None),
        (0, lambda: press(overlay, Qt.Key.Key_Escape), None),
        (150, None, ("ui: Enter in Title moves to the Note field (title T1, body B1 saved)", lambda: capture_closed() and newest().get("title") == "T1" and newest().get("body") == "B1")),
        # overlay: files panel
        (0, lambda: click(overlay, "row", 2), None),
        (150, None, ("ui: clicking a folder drills in (Files panel at Study)", lambda: overlay.property("filesRel") == "Study")),
        (0, lambda: click(overlay, "back"), None),
        (150, None, ("ui: the back arrow goes up a level", lambda: overlay.property("filesRel") == "")),
        (0, lambda: click(overlay, "row", 0, right), None),
        (150, None, ("ui: right-click opens the context menu", lambda: overlay.property("filesMenuOpen"))),
        (0, lambda: press(overlay, Qt.Key.Key_Escape), None),
        (150, None, ("ui: Esc closes the menu, not the overlay", lambda: not overlay.property("filesMenuOpen") and still_open())),
        (0, lambda: click(overlay, "newFolder"), None),
        (150, None, ("ui: New folder creates a folder and starts renaming it", lambda: "New folder" in names("") and overlay.property("renaming") == "New folder")),
        (0, lambda: press(overlay, Qt.Key.Key_Escape), None),
        (150, None, ("ui: Esc cancels the rename and keeps the folder", lambda: overlay.property("renaming") == "" and "New folder" in names("") and still_open())),
        (0, lambda: click(overlay, "newFolder"), None),
        (150, lambda: type_text(overlay, "Zeta"), None),
        (0, lambda: press(overlay, Qt.Key.Key_Return), None),
        (150, None, ("ui: typing a name and Enter renames the new folder", lambda: "Zeta" in names("") and "New folder (2)" not in names("") and overlay.property("renaming") == "")),
        (0, lambda: press(overlay, Qt.Key.Key_Escape), None),
        (400, None, ("ui: Esc with nothing open closes the overlay", lambda: controller.overlay_state == "hidden")),
        # overlay -> Full App on the Files section, at the same folder
        (0, controller.open_overlay, None),
        (500, lambda: overlay.demo("files-overlay"), None),
        (150, lambda: controller.morph_to_full("files"), None),
        (1000, None, ("morph from the overlay's Files panel opens the Full App on 'files' at Study/Physics", lambda: controller.full_visible() and full.property("section") == "files" and full.property("filesRel") == "Study/Physics")),
        # Full App: list/grid toggle and the Paste button
        (0, toggle_other_mode, None),
        (150, None, ("ui: the list/grid toggle switches the view", lambda: full.property("filesMode") != mode_before[0])),
        (0, toggle_back_mode, None),
        (150, None, ("ui: the toggle switches back (saved setting restored)", lambda: full.property("filesMode") == mode_before[0])),
        (0, paste_in_full, None),
        (300, None, ("ui: the Paste button in Files pastes the copied file into the current folder", lambda: "Lab graph.png" in names("Personal"))),
        (0, ctrl_v_does_nothing, None),
        (300, None, ("ui: Ctrl+V is no longer a shortcut (nothing is pasted)", lambda: "Chapter 3 - Kinematics.pdf" not in names("Coding"))),
        (0, restore_clipboard, None),
        *click_through_steps(),
        (0, controller.hide_full, None),
    ]


def window_state_steps(env: Any) -> list[Step]:
    """The Full App remembers its window state (maximized, geometry) across hide/show and restarts."""
    controller = env.controller
    full = controller.full
    visible_state = QWindow.Visibility
    memo: dict[str, QRect] = {}
    tag = "window state"

    def disk() -> FullWindowState:
        """A fresh read of the settings file, as a restarted app would see it."""
        return FullWindowState(QSettings(str(controller.settings_path), QSettings.Format.IniFormat))

    def available() -> QRect:
        return QRect(full.screen().availableGeometry())

    def geometry() -> QRect:
        return QRect(full.geometry())

    def move_window() -> None:
        area = available()
        memo["moved"] = QRect(area.x() + 90, area.y() + 70, 1010, 650)
        full.setGeometry(memo["moved"])

    def reload_from_disk() -> None:
        controller.state = disk()  # what a restart does: load the saved state, then show

    def target_checks() -> bool:
        screen = full.screen()
        controller.state.maximized = True
        maximized_target = controller._target_rect(screen) == screen.availableGeometry()
        controller.state.maximized = False
        normal_target = controller._target_rect(screen) == memo["moved"]
        return maximized_target and normal_target

    return [
        (0, lambda: controller.show_full("notes"), None),
        (600, lambda: memo.update(normal=geometry()), (f"{tag}: Full App shows normally first", lambda: controller.full_visible() and full.visibility() == visible_state.Windowed)),
        (0, full.showMaximized, None),
        (700, None, (f"{tag}: the frameless window maximizes to the available geometry (not over the taskbar)", lambda: full.visibility() == visible_state.Maximized and geometry() == available())),
        (0, controller.hide_full, None),
        (300, None, (f"{tag}: hiding while maximized saves maximized=true to the settings file", lambda: disk().maximized and disk().geometry == memo["normal"])),
        (0, controller.on_hotkey, None),
        (200, controller.on_hotkey, None),
        (1500, None, (f"{tag}: a double press after maximizing shows the Full App maximized (morph target = available area)", lambda: controller.full_visible() and not controller.overlay_visible() and full.visibility() == visible_state.Maximized and geometry() == available())),
        (0, full.showNormal, None),
        (700, None, (f"{tag}: restoring from maximized returns to the saved normal geometry", lambda: full.visibility() == visible_state.Windowed and geometry() == memo["normal"])),
        (0, move_window, None),
        (600, None, (f"{tag}: moving and resizing is saved (debounced) to the settings file", lambda: disk().geometry == memo["moved"] and not disk().maximized)),
        (0, controller.hide_full, None),
        (200, controller.open_full_from_tray, None),
        (700, None, (f"{tag}: un-maximized, moved, hidden, shown from the tray path: same geometry, not maximized", lambda: controller.full_visible() and full.visibility() == visible_state.Windowed and geometry() == memo["moved"])),
        (0, controller.hide_full, None),
        (100, None, (f"{tag}: the morph target is the available area when maximized, the saved geometry when not", target_checks)),
        # restart: a state loaded from disk (normal geometry kept, maximized flag on) drives the show
        (0, full.showMaximized, None),
        (0, controller.show_full, None),
        (700, None, (f"{tag}: maximizing keeps the normal geometry in the settings", lambda: disk().maximized and disk().geometry == memo["moved"])),
        (0, controller.hide_full, None),
        (0, reload_from_disk, None),
        (0, controller.open_full_from_tray, None),
        (800, None, (f"{tag}: restart: a state re-loaded from the settings file shows the Full App maximized", lambda: full.visibility() == visible_state.Maximized and geometry() == available())),
        (0, full.showNormal, None),
        (700, None, (f"{tag}: restart: restoring after a re-loaded maximized state still goes to the saved geometry", lambda: full.visibility() == visible_state.Windowed and geometry() == memo["moved"])),
        (0, controller.hide_full, None),
    ]

