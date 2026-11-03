"""Visual prototype of the new Qt Quick UI, using fake in-memory data.

Runs in the tray. Press the hotkey (default Ctrl+Alt+Space) for the Quick Overlay.

Run:  .venv\\Scripts\\python.exe prototype\\main.py
      .venv\\Scripts\\python.exe prototype\\main.py --show overlay --fake-backdrop
      .venv\\Scripts\\python.exe prototype\\main.py --state overlay --fake-backdrop --screenshot out.png
      .venv\\Scripts\\python.exe prototype\\main.py --selftest
      .venv\\Scripts\\python.exe prototype\\main.py --fast-focus --library D:\\some\\folder
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))
sys.path.insert(0, str(ROOT))

import layout_tests  # noqa: E402
import round5_settings  # noqa: E402
import round5_tests  # noqa: E402
import round5_views  # noqa: E402
import round6_tests  # noqa: E402
import round7_tests  # noqa: E402
from app import (  # noqa: E402
    Controller,
    FullWindowState,
    HotkeyBridge,
    SnapshotProvider,
    SoundPlayer,
    StubHotkeyManager,
    clock_is_12h,
    style_full_window,
)
from files import FileLibrary  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QLocale,
    QMimeData,
    QPoint,
    QRect,
    QSettings,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QFontDatabase, QGuiApplication, QImage, QWindow  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from productivity_app.desktop.hotkey import HotkeyManager  # noqa: E402
from productivity_app.windows.hotkeys import WindowsHotkeyBackend  # noqa: E402

OVERLAY_STATES = [
    "overlay", "overlay-search", "overlay-editor", "overlay-morph", "overlay-carousel", "timepicker",
    "overlay-compose", "files-overlay", "files-search", "focus-break",
    "layout-picker", "layout-drag-swap", "layout-drag-split", "layout-drag-invalid", "layout-topbar",
    "layout-big", "layout-rows", "layout-columns", "overlay-greeting", "overlay-greeting-12h",
]
FULL_STATES = [
    "full", "editor", "drawer", "compose",
    "files-full-grid", "files-full-list", "files-context", "files-drop", "files-move", "files-rename",
    "tasks", "tasks-quickadd", "tasks-drag", "task-editor", "task-datepicker",
    "reminders", "reminder-editor", "focus", "focus-lengths", "focus-newtimer",
    "settings-top", "settings-bottom", "settings-hotkey-recording", "trash", "trash-empty",
    "settings-sound-picker", "settings-appearance", "focus-fullscreen", "focus-fullscreen-calm",
    "reminders-repeat", "reminder-repeat-popup", "search-dropdown", "takenote-noicon",
    "tour-1", "tour-2", "tour-3", "settings-name",
]
STATES = [*OVERLAY_STATES, *FULL_STATES, "toast", "toast-focus"]


def default_library_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ProductivityAppPrototype" / "Files"


def start_focus_demo(store) -> None:  # noqa: ANN001
    """Fast mode, phase 0 with 1 s left and running: it rolls into the short break."""
    store.setProperty("fastFocus", True)
    store.focusSetPhase(0)
    store.setProperty("focusRemaining", 1)
    store.setProperty("focusRunning", True)


def run_screenshot(controller: Controller, store, state: str, path: Path, delay: int) -> None:  # noqa: ANN001
    controller.on_shot = lambda _path: QTimer.singleShot(0, controller.quit)
    if state in OVERLAY_STATES:
        controller.open_overlay()
        if state == "focus-break":
            QTimer.singleShot(500, lambda: start_focus_demo(store))
            delay = max(delay, 2400)  # the 1 s ticker must fire and the panel must repaint
        else:
            QTimer.singleShot(500, lambda: controller.overlay.demo(state))
        QTimer.singleShot(delay, lambda: controller.overlay.saveShot(str(path)))
    elif state in ("toast", "toast-focus"):
        if state == "toast":
            QTimer.singleShot(300, controller.fire_demo_reminder)
        else:
            QTimer.singleShot(300, lambda: controller.on_focus_completed("Focus session done — time for a 5 min break"))
        QTimer.singleShot(delay, lambda: controller.toast.saveShot(str(path)))
    elif state in ("focus-fullscreen", "focus-fullscreen-calm"):
        # a real fullscreen window; the controls stay (a long calm delay) or fade out (a short one)
        controller.full.setProperty("zenCalmDelay", 400 if state.endswith("calm") else 600000)
        controller.show_full("focus")
        QTimer.singleShot(500, controller.enter_fullscreen)
        QTimer.singleShot(max(delay, 2200), lambda: controller.full.saveShot(str(path)))
    else:
        if state.startswith("tour-"):
            store.setProperty("tourDone", False)   # screenshots start with the tour done
        controller.show_full("notes")
        QTimer.singleShot(400, lambda: controller.full.demo(state))
        QTimer.singleShot(delay, lambda: controller.full.saveShot(str(path)))


def run_morph_stats(controller: Controller, target: str) -> None:
    """Measure the overlay's frame intervals during the morph into the Full App.

    Records QQuickWindow.frameSwapped timestamps of the overlay from the moment the morph
    starts until the overlay is hidden again. "during morph" counts only frames up to the end of
    the animation; "incl. handoff" also counts the stall of showing the Full App.
    """
    overlay = controller.overlay
    stamps: list[float] = []
    state = {"recording": False, "morph_end": 0.0}

    def on_frame() -> None:
        if state["recording"]:
            stamps.append(time.perf_counter())

    def on_morph_end(_section: str) -> None:
        state["morph_end"] = time.perf_counter()

    def summary(times: list[float]) -> str:
        gaps = [(b - a) * 1000 for a, b in zip(times, times[1:], strict=False)]
        if not gaps:
            return "no frames"
        worst = sorted(gaps)[-3:][::-1]
        return (f"{len(times)} frames, avg {sum(gaps) / len(gaps):.1f} ms, max {max(gaps):.1f} ms, "
                f"worst 3: {', '.join(f'{g:.1f}' for g in worst)}")

    def begin() -> None:
        controller.prepare_morph_stats(target)
        state["recording"] = True
        controller.morph_to_full("notes")
        poll()

    def poll() -> None:
        if controller.overlay_state == "hidden":
            QTimer.singleShot(300, report)
        else:
            QTimer.singleShot(40, poll)

    def report() -> None:
        state["recording"] = False
        during = [t for t in stamps if t <= state["morph_end"]]
        print(f"morph-stats [{target}] during morph : {summary(during)}")
        print(f"morph-stats [{target}] incl. handoff: {summary(stamps)}")
        controller.quit()

    overlay.frameSwapped.connect(on_frame)
    overlay.morphFinished.connect(on_morph_end)
    controller.open_overlay()
    QTimer.singleShot(900, begin)


# ── Self-test ───────────────────────────────────────────────────────


def library_checks(check) -> None:  # noqa: ANN001
    """Exercise FileLibrary in a fresh temp folder (hard delete, never the Recycle Bin)."""
    clipboard = QGuiApplication.clipboard()
    saved = QMimeData()  # restore the user's clipboard afterwards
    current = clipboard.mimeData()
    if current is not None:
        for fmt in current.formats():
            saved.setData(fmt, current.data(fmt))
    tmp = Path(tempfile.mkdtemp(prefix="proto-selftest-"))
    library = FileLibrary(tmp / "lib", hard_delete=True)
    try:
        names = lambda rel: [e["name"] for e in library.list(rel)]  # noqa: E731
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


def js_to_python(value):  # noqa: ANN001, ANN201
    return value.toVariant() if hasattr(value, "toVariant") else value


def ui_steps(controller: Controller) -> list:
    """Real mouse and key events (QTest) sent to the overlay and the Full App windows."""
    overlay, full, library = controller.overlay, controller.full, controller.library
    left, right = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
    no_mod, ctrl = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier
    saved_clipboard = QMimeData()

    def point(window, name: str, index: int = 0) -> QPoint:  # noqa: ANN001
        value = js_to_python(window.probe(name, index))
        return QPoint(round(value["x"]), round(value["y"]))

    def click(window, name: str, index: int = 0, button=left) -> None:  # noqa: ANN001
        QTest.mouseClick(window, button, no_mod, point(window, name, index))

    def click_left_of(window, name: str, index: int, dx: int) -> None:  # noqa: ANN001
        """Click `dx` pixels to the left of a control's centre (outside an open menu)."""
        at = point(window, name, index)
        QTest.mouseClick(window, left, no_mod, QPoint(at.x() - dx, at.y()))

    def move_to(window, name: str, index: int = 0) -> None:  # noqa: ANN001
        QTest.mouseMove(window, point(window, name, index))

    def type_text(window, text: str) -> None:  # noqa: ANN001
        for char in text:
            QTest.keyClick(window, char)

    def press(window, key, modifier=no_mod) -> None:  # noqa: ANN001
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
        ids["full"] = int(full.property("editorNoteId"))

    def remember_overlay_note() -> None:
        ids["overlay"] = int(overlay.property("editorNoteId"))

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


def window_state_steps(controller: Controller) -> list:
    """The Full App remembers its window state (maximized, geometry) across hide/show and restarts."""
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
        maximized_target = controller._target_rect(screen) == screen.availableGeometry()  # noqa: SLF001
        controller.state.maximized = False
        normal_target = controller._target_rect(screen) == memo["moved"]  # noqa: SLF001
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


def run_selftest(controller: Controller) -> None:
    """Drive on_hotkey() in timed sequences and assert the state after each step."""
    results: list[bool] = []
    store = controller.store

    def check(name: str, ok: bool) -> None:
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}: {name}")

    def state() -> str:
        return (
            f"overlay={controller.overlay_state}/{'visible' if controller.overlay_visible() else 'hidden'}"
            f" full={'visible' if controller.full_visible() else 'hidden'}"
        )

    def start_pomodoro() -> None:
        store.setProperty("fastFocus", True)
        store.focusSetPhase(0)
        store.setProperty("focusRemaining", 1)
        store.setProperty("focusRunning", True)

    def pomodoro_advanced() -> bool:
        return (
            store.property("focusPhase") == 1 and store.property("focusLabel") == "Short break"
            and store.property("focusRunning") is False
            and store.property("focusRemaining") == store.focusDuration(1) == 8
            and controller.focus_toasts == 1
            and controller.last_focus_message.startswith("Focus session done")
        )

    def skip_from_6() -> None:
        store.focusSetPhase(6)
        store.focusSkip()

    def skip_from_7() -> None:
        store.focusSkip()

    def new_note_ok() -> bool:
        notes = js_to_python(store.property("notes"))
        first = notes[0] if notes else {}
        return first.get("title") == "Selftest title" and first.get("body") == "Selftest body"

    steps = [
        (0, controller.on_hotkey, None),
        (300, None, ("press -> overlay open", lambda: controller.overlay_visible() and controller.overlay_state == "open")),
        (500, controller.on_hotkey, None),
        (500, None, ("press after 500 ms -> overlay closed", lambda: not controller.overlay_visible() and controller.overlay_state == "hidden")),
        (200, controller.on_hotkey, None),
        (200, controller.on_hotkey, None),
        (900, None, ("double-press (200 ms) -> full app visible, overlay hidden", lambda: controller.full_visible() and not controller.overlay_visible() and controller.full.property("section") == "notes")),
        (200, controller.on_hotkey, None),
        (200, None, ("press with full app visible -> full app hidden", lambda: not controller.full_visible() and not controller.overlay_visible())),
        # file library
        (0, lambda: library_checks(check), None),
        # pomodoro (fast mode: 20 s / 8 s / 12 s)
        (0, start_pomodoro, None),
        (1500, None, ("pomodoro: phase 0 hits 0 -> phase 1 'Short break', paused, 8 s left, toast signal fired", pomodoro_advanced)),
        (0, skip_from_6, ("pomodoro: skip 6 -> 7 shows 'Long break' with a fresh 12 s", lambda: store.property("focusPhase") == 7 and store.property("focusLabel") == "Long break" and store.property("focusRemaining") == 12)),
        (0, skip_from_7, ("pomodoro: skip 7 -> 0 shows 'Session 1 of 4'", lambda: store.property("focusPhase") == 0 and store.property("focusLabel") == "Session 1 of 4" and store.property("focusRemaining") == 20)),
        # take a note with a title and a body
        (0, lambda: controller.overlay.captureNote("Selftest title", "Selftest body"), ("take note: capture path saves title + body as the newest note", new_note_ok)),
    ]
    # PROTO_SELFTEST_ONLY=tasks,settings runs just those groups of the new round (for quick reruns)
    only = {name for name in os.environ.get("PROTO_SELFTEST_ONLY", "").split(",") if name}
    if only:
        steps = []
    groups = {
        "ui": lambda: ui_steps(controller),
        "layout-unit": lambda: [(0, lambda: layout_tests.unit_checks(check, controller.overlay), None)],
        "layout": lambda: layout_tests.ui_steps(controller, controller.settings_path),
        "unit": lambda: [(0, lambda: round5_tests.unit_checks(check, controller.full), None)],
        "tasks": lambda: round5_tests.tasks_steps(controller),
        "reminders": lambda: round5_views.reminders_steps(controller),
        "focus": lambda: round5_views.focus_steps(controller),
        "trash": lambda: round5_views.trash_steps(controller),
        "settings": lambda: round5_settings.settings_steps(controller),
        "round6": lambda: round6_tests.round6_steps(controller),
        "window": lambda: window_state_steps(controller),
        "recurrence": lambda: [(0, lambda: round7_tests.recurrence_unit_checks(check, controller.full), None)],
        "repeat": lambda: round7_tests.repeat_steps(controller),
        "checklist": lambda: round7_tests.checklist_steps(controller),
        "search": lambda: round7_tests.search_steps(controller),
        "greeting": lambda: round7_tests.greeting_steps(controller),
        "tour": lambda: round7_tests.tour_steps(controller),
    }
    for name, build in groups.items():
        if not only or name in only:
            steps += build()

    def run(index: int) -> None:
        if index >= len(steps):
            controller.quit()
            return
        delay, action, assertion = steps[index]

        def go() -> None:
            try:
                if action is not None:
                    action()
                if assertion is not None:
                    name, predicate = assertion
                    check(name, bool(predicate()))
                    if not results[-1]:
                        print(f"      state: {state()}")
                        if name.startswith("ui:"):
                            print(f"      newest note: {js_to_python(controller.store.property('notes'))[0]}")
            except Exception as exc:
                check(f"step {index} crashed: {exc!r}", False)
            run(index + 1)

        QTimer.singleShot(delay, go)

    def finish() -> None:
        passed = sum(results)
        print(f"selftest: {passed}/{len(results)} passed")
        if passed != len(results):
            controller.app.setProperty("selftest_failed", True)

    controller.app.aboutToQuit.connect(finish)
    run(0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path, help="save a render of the window and exit")
    parser.add_argument("--state", choices=STATES, help="demo state for screenshots")
    parser.add_argument("--delay", type=int, default=1800, help="ms to wait before the screenshot")
    parser.add_argument("--hotkey", default=None, help="global hotkey (two modifiers + key); default: the one saved in Settings, else Ctrl+Alt+Space")
    parser.add_argument("--fake-backdrop", action="store_true", help="use a generated backdrop, not the screen or wallpaper")
    parser.add_argument("--native-toast", action="store_true", help="use Windows notifications for alerts")
    parser.add_argument("--show", choices=["full", "overlay"], help="open one at start")
    parser.add_argument("--quit-after", type=int, help="quit after this many ms (for scripted runs)")
    parser.add_argument("--demo-reminder", type=int, metavar="MS", help="fire a demo reminder after MS")
    parser.add_argument("--selftest", action="store_true", help="exercise the hotkey, library, pomodoro and capture logic and exit")
    parser.add_argument("--library", type=Path, help="folder for the Files library (default: %%LOCALAPPDATA%%\\ProductivityAppPrototype\\Files)")
    parser.add_argument("--fast-focus", action="store_true", help="Pomodoro phases last 20 s / 8 s / 12 s")
    parser.add_argument("--clock-12h", action="store_true", help="show the overlay clock in 12-hour form (for tests; normally it follows Windows)")
    parser.add_argument("--reset-tour", action="store_true", help="show the first-launch tour again (for tests)")
    parser.add_argument("--morph-stats", choices=["normal", "maximized"], help="measure overlay frame intervals during the morph and exit")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    headless = bool(args.screenshot or args.selftest or args.morph_stats)
    # The screen and wallpaper are never captured for screenshots or the self-test.
    fake_backdrop = args.fake_backdrop or headless
    if args.screenshot and args.state in OVERLAY_STATES and not args.fake_backdrop:
        print("note: screenshots always use the fake backdrop", file=sys.stderr)

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setOrganizationName("ProductivityAppPrototype")
    app.setApplicationName("ProductivityAppPrototype")
    app.setQuitOnLastWindowClosed(False)

    if QFontDatabase.addApplicationFont(str(ROOT / "fonts" / "Outfit[wght].ttf")) < 0:
        print("warning: could not load Outfit font", file=sys.stderr)

    # Test runs without an explicit --library get a throwaway folder, never the real one.
    saved_library = "" if headless else str(QSettings().value("app/libraryPath", "") or "")
    library_root = args.library or (
        Path(tempfile.mkdtemp(prefix="proto-library-")) if headless
        else Path(saved_library) if saved_library and Path(saved_library).is_dir() else default_library_root()
    )
    library = FileLibrary(library_root, hard_delete=bool(args.selftest), stub_launch=bool(args.selftest))
    print(f"library: {library.root}")

    # The Full App's window state lives in QSettings (group "full") and the overlay's layout in
    # QML Settings (group "overlay"). Test runs use a throwaway ini file, never the user's real
    # settings (nor the registry).
    settings_dir = Path(tempfile.mkdtemp(prefix="proto-settings-")) if headless else None

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "qml"))
    engine.rootContext().setContextProperty("Library", library)
    # The Settings page records a new global hotkey through this bridge. Test runs and screenshots
    # get a stub manager: the real global hotkey is never touched.
    hotkeys = HotkeyBridge(StubHotkeyManager() if headless else None)
    engine.rootContext().setContextProperty("Hotkeys", hotkeys)
    # Notification sounds and their previews (Settings). Test runs and screenshots get a stub that
    # only records the calls: no sound is ever played.
    sounds = SoundPlayer(stub=headless)
    engine.rootContext().setContextProperty("Sounds", sounds)
    engine.rootContext().setContextProperty(
        "SettingsLocation",
        QUrl.fromLocalFile(str(settings_dir / "settings.ini")) if settings_dir else QUrl(),
    )
    provider = SnapshotProvider()
    engine.addImageProvider("snapshot", provider)
    for name in ("Main", "Overlay", "Toast"):
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / f"{name}.qml")))
    roots = engine.rootObjects()
    if len(roots) != 3:
        return 1
    full, overlay, toast = roots
    store = engine.singletonInstance("Proto", "Store")
    style_full_window(full, engine.singletonInstance("Proto", "Theme"))
    if args.fast_focus or args.selftest:
        store.setProperty("fastFocus", True)
        store.focusSetPhase(0)
    # The overlay clock follows the Windows 12h/24h setting. Tests and screenshots always show 24-hour
    # unless --clock-12h says otherwise.
    store.setProperty(
        "twelveHour",
        True if args.clock_12h else False if headless
        else clock_is_12h(QLocale.system().timeFormat(QLocale.FormatType.ShortFormat)),
    )
    # The first-launch tour: tests and screenshots start with it done (a test turns it on); --reset-tour
    # forces it back on.
    if headless:
        store.setProperty("tourDone", True)
    if args.reset_tour:
        store.setProperty("tourDone", False)

    settings = (
        QSettings(str(settings_dir / "settings.ini"), QSettings.Format.IniFormat)
        if settings_dir else QSettings()
    )
    controller = Controller(
        app, full, overlay, toast, store, provider,
        use_fake_backdrop=fake_backdrop, native_toast=args.native_toast, tray=not headless,
        library=library, state=FullWindowState(settings),
    )
    controller.settings_path = settings_dir / "settings.ini" if settings_dir else None
    controller.hotkeys = hotkeys
    controller.sounds = sounds

    manager = None
    if not headless:
        hotkey_window = QWindow()  # hidden; only its handle receives WM_HOTKEY
        manager = HotkeyManager(app, hotkey_window, WindowsHotkeyBackend(), controller.on_hotkey)
        hotkeys.manager = manager
        wanted = args.hotkey or str(store.property("hotkey") or "Ctrl+Alt+Space")
        try:
            manager.start(wanted)
            print(f"Ready — press {manager.active_hotkey.canonical if manager.active_hotkey else wanted}")
        except Exception as error:
            print(f"warning: could not register {wanted} ({error}); use the tray icon", file=sys.stderr)
        if controller.tray is not None:
            controller.tray.showMessage(
                "Productivity", f"Running in the tray. Press {wanted} for the overlay."
            )
        app.aboutToQuit.connect(manager.close)

    engine.quit.connect(controller.quit)
    if args.screenshot:
        run_screenshot(controller, store, args.state or "full", args.screenshot, args.delay)
    else:
        # Pre-warm the Full App's window (invisibly), then start whatever was asked for.
        def start() -> None:
            if args.selftest:
                QTimer.singleShot(100, lambda: run_selftest(controller))
            elif args.morph_stats:
                QTimer.singleShot(100, lambda: run_morph_stats(controller, args.morph_stats))
            elif args.show == "overlay":
                controller.open_overlay()
            elif args.show == "full":
                controller.show_full()
            elif not store.property("tourDone"):
                controller.show_full("notes")   # first launch (or --reset-tour): the Full App opens with the tour

        if args.morph_stats:
            controller.prepare_morph_stats(args.morph_stats)  # as if saved by an earlier run
        QTimer.singleShot(0, lambda: controller.prewarm_full(start))

    if args.quit_after:
        QTimer.singleShot(args.quit_after, controller.quit)
    if args.demo_reminder:
        QTimer.singleShot(args.demo_reminder, controller.fire_demo_reminder)

    code = app.exec()
    QCoreApplication.processEvents()
    library.close()
    if headless and not args.library:
        shutil.rmtree(library.root, ignore_errors=True)
    if settings_dir is not None:
        shutil.rmtree(settings_dir, ignore_errors=True)
    if app.property("selftest_failed"):
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
