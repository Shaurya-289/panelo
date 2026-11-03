# Plan: Prototype round 2 (wallpaper glass, Files explorer, note body, pomodoro fix)

## Context
`prototype/` holds a Qt Quick (PySide6 6.11) prototype of the app's new UI, with fake data and no
backend. It has two modes:
- the **Full App** (`qml/Main.qml`, a Keep-style dashboard)
- the **Quick Overlay** (`qml/Overlay.qml`, opened by the global hotkey; it morphs into the Full App)

The previous build followed `prototype/PLAN-overlay.md`. Read it for background and conventions. The
user tested it and loved it. This round applies their feedback. Every decision below is the
user's; implement exactly.

**Only modify files inside `prototype/`. Never touch `src/` or `tests/`.**

Read first: `PLAN-overlay.md`, `main.py`, `app.py`, `qml/Main.qml`, `qml/Overlay.qml`, and every file in
`qml/Proto/` (especially `Store.qml`, `Theme.qml`, `FocusTimer.qml`, `NotesListPanel.qml`,
`FilesPanel.qml`, `TakeNoteBar.qml`, `NavDrawer.qml`, `TimePickerPopup.qml`, `qmldir`).

## 1. Full App gets the overlay's blurred background, from the wallpaper
The user wants the Full App to have **the same blurred, darkened glass background as the overlay**.
A window moves, so use the **desktop wallpaper** (this is how Windows 11 Mica works), not a
snapshot.
- **Reusable background:** extract the overlay's background (the snapshot `Image` + `MultiEffect` blur
  + dark layer) into a component `qml/Proto/BlurredBackdrop.qml` with properties `source`,
  `sourceX/sourceY` (the offset of the image relative to the item), `sourceWidth/sourceHeight`,
  `blurAmount` (0..1) and `darkness` (default = the overlay's current value). Make `Overlay.qml` use it,
  with no visual change.
- **Getting the wallpaper (Python):** call `SystemParametersInfoW(SPI_GETDESKWALLPAPER=0x0073, 260,
  buf, 0)`. Load the file and scale it to *cover* the screen the window is on ("fill" behavior;
  center-crop). If there is no wallpaper file, or it fails to load, use `fake_backdrop()`. Serve it via
  the existing `SnapshotProvider` pattern (a separate id like `image://snapshot/wall-<n>`, or a second
  provider). With `--fake-backdrop`, the Full App also uses the fake image.
- **Main.qml:** replace the flat `Theme.base` rectangle with `BlurredBackdrop`. Size the image to the
  window's screen and position it at `(screen.x - window.x, screen.y - window.y)`, so the blur stays
  aligned when the window moves (bind to `window.x/y` and `window.screen.virtualX/virtualY`).
  Reload the wallpaper on every `show_full` and when the window changes screen.
- **The Full App window is now opaque:** stop requesting Acrylic for it (keep DWM dark mode and rounded
  corners). `Theme.acrylic` can stay but shouldn't make panels unreadable. The cards keep their
  current glass look over the new background.
- The morph's "window ghost" should now match the Full App's look (backdrop + darkness), so the morph
  lands seamlessly.

## 2. "Take a note" in the overlay gets a body
`NotesListPanel`'s one-line field only saves a title. Replace it with an expanding capture box like
the Full App's `TakeNoteBar` (reuse it, adding a `compact` mode if needed):
- **Collapsed:** "Take a note…"; click it and it expands into a **Title** field and a multi-line
  **Note** field, plus a "Close" button. Keep the tint picker if it fits; drop it in compact mode if
  the panel is narrow.
- **Keys:** Enter in Title moves to the body. Ctrl+Enter, Close, or clicking elsewhere in the overlay
  saves (if title or body is non-empty) and collapses. Esc while expanded saves and collapses (it does
  *not* close the overlay).
- The new note appears at the top of the list immediately.

## 3. Pomodoro fix
Bug: when the focus countdown reaches 0 nothing advances, and Skip changes the phase without
resetting the time, so "Session N of 4" seems stuck. Fix in `Store.qml` + `FocusTimer.qml`:
- **Phases 0..7:**
  - Focus phases (0, 2, 4, 6): 25 min.
  - Short breaks (1, 3, 5): 5 min.
  - Long break (7): 15 min.
  - Add `focusDuration(phase)` and `focusSetPhase(p)`, which sets `focusPhase`, `focusTotal` and
    `focusRemaining = focusTotal`.
- **When the running countdown hits 0:**
  - Advance to the next phase (7 → 0 starts a new cycle) and **pause** (`focusRunning = false`).
  - Fire a toast: "Focus session done — time for a 5 min break" / "Break over — ready for session
    3?" / "Cycle complete — take a 15 min break". Reuse the existing toast path (`Toast.qml`,
    fallback to the native notification) through a Store signal that Python listens to, as for
    reminders.
- **Buttons:** Skip = `focusSetPhase(next)` (keeps the running/paused state). Stop = pause + reset the
  current phase's time.
- **Label:** "Session N of 4" on focus phases (N = phase/2 + 1); "Short break" on 1/3/5; "Long
  break" on 7. Dots: done phases filled, the current one half-bright.
- **Fast mode:** add a `--fast-focus` flag (Python sets a Store property). Durations become 20 s / 8 s
  / 12 s, so the user can watch the whole cycle quickly.

## 4. Files becomes a real mini file explorer
User decisions:
- **Notes no longer have folders.** Notes are one Keep-style collection (pinned / archive / tags
  stay). Remove the `folder` field from the notes, the note-folder tree from `Store`, the `folder:<Name>`
  sections in `Main.qml`, and the drawer's "Folders" section.
- **Files is one master folder, "Files".** The user creates subfolders and puts real files in them
  (PDF, PPT, DOCX, images, anything).
- **Adding a file copies it** into the app's own library folder (the original stays put).
- **Ways to add:**
  - **drag & drop** (from Explorer or the desktop)
  - a **"+ Add file"** button (the Windows file picker, multi-select)
  - **paste** (files copied in Explorer)
  - **"New folder"**
- **Clicking a file opens it in its default app.** Clicking a folder goes inside it.
- **Overlay Files panel:** a compact **list**. Folders use **drill-in navigation with a breadcrumb**
  ("‹  Files / Study / Physics", where the back arrow goes up and each segment is clickable).
- **Full App Files view:** a **list/grid toggle** (remembered via `Settings`). The grid has big tiles.
- **Right-click a file or folder:** Rename, Delete, Move to folder…, Show in Explorer, Copy. Copy puts
  the file on the clipboard so it can be pasted into Explorer, an email, Discord and so on.

### Backend: `prototype/files.py`, class `FileLibrary(QObject)`
- **Root:** `--library PATH`, default `%LOCALAPPDATA%\ProductivityAppPrototype\Files`. Create it if
  missing.
- **Seed on first run** (only if the root is new or empty):
  - folders `Study/Physics`, `Study/Maths`, `Study/Chemistry`, `Coding`, `Personal`
  - `Study/Physics/Chapter 3 - Kinematics.pdf`, generated with `QPdfWriter` (a title and a few
    lines)
  - `Study/Physics/Lab graph.png`, painted with `QPainter`
  - `Coding/ideas.txt`
  - `Personal/Trip budget.csv`
  - `Study/Maths/Formulas.md`
- **Expose to QML** via `engine.rootContext().setContextProperty("Library", library)`. Every
  path argument is **relative to the root, using `/`**; reject anything that resolves outside the root.
  - `list(rel) -> list[dict]`: folders first, then files, case-insensitive by name. Each dict has
    `name, rel, isDir, ext, kind, size` (human-readable, e.g. "2.1 MB"),
    `modified` (e.g. "Oct 28" / "Today 14:02"), `items` (count, for folders), and `url`
    (`file:///…`, for image thumbnails).
  - `search(query) -> list[dict]`: recursive, case-insensitive name match. Same dicts plus `parent`
    (the relative folder, for display).
  - `addFiles(urls: list[str], destRel) -> str`: copy the files in. Copying a folder copies it
    recursively. On a name clash, append " (2)", " (3)" and so on. Return an error message or "".
  - `pasteInto(destRel) -> str`: read `QGuiApplication.clipboard().mimeData().urls()` and add them.
    If there are no file URLs, return "Clipboard has no files".
  - `createFolder(parentRel, name) -> str` (default name "New folder", unique),
    `rename(rel, newName) -> str` (validate: not empty, no `\/:*?"<>|`, no clash),
    `remove(rel) -> str` (`QFile.moveToTrash`, i.e. the Recycle Bin; hard-delete only under
    `--selftest`), `move(rel, destRel) -> str` (no moving a folder into itself),
    `folders() -> list[str]` (all folder rels, for the Move picker).
  - `open(rel)` (`QDesktopServices.openUrl`), `showInExplorer(rel)` (`explorer /select,"<abs>"`
    via `subprocess.Popen`), and `copyToClipboard(rel)` (QMimeData with the file URL; also set the
    `Preferred DropEffect` format = copy (DWORD 1), so Explorer's paste works).
  - Signal `changed`. Emit it after every mutation, and also from a `QFileSystemWatcher` on the
    currently listed folders (debounced 200 ms), so changes made in Explorer show up live.
- **Kinds:**
  - `pdf`
  - `slides` (ppt, pptx, key, odp)
  - `doc` (doc, docx, odt, rtf, md, txt)
  - `sheet` (xls, xlsx, csv, ods)
  - `image` (png, jpg, jpeg, gif, webp, bmp, svg)
  - `video`, `audio`
  - `archive` (zip, rar, 7z)
  - `code` (py, js, ts, html, css, json, …)
  - `other`

### QML
- **`FileBadge.qml`:** a rounded square (6px) showing the **extension text** (e.g. "PDF", "PPTX";
  4 characters max) on a muted kind color:
  - pdf: red
  - slides: orange
  - doc: blue
  - sheet: green
  - image: purple
  - video: pink
  - audio: teal
  - archive: sand
  - code: slate
  - other: gray

  All muted, like `Theme.tints` but a bit stronger, with white text. Folders use a folder glyph badge.
  Sizes: 28px (list), 64px (grid). Image files in the grid show a cropped thumbnail
  (`Image { source: url; asynchronous: true; sourceSize… }`) instead of the badge.
- **`FileRow.qml`** (list): badge, name (elided), then muted `kind · size` and `modified` on the right
  (hidden when narrow); hover background. **`FileTile.qml`** (grid): about 120×128, the badge or
  thumbnail, and the name in 2 lines (elided).
- **`ContextMenu.qml`:** a glass popup like `TimePickerPopup` (opaque `Theme.raised`, border,
  radius 6), with rows of icon + label, hover highlight, Esc or click-outside to close, kept inside
  the window bounds. Use it for the right-click menu. **Rename** turns the row's name into an inline
  `TextBox` (Enter saves, Esc cancels). **Move to folder…** opens a second popup listing
  `Library.folders()` as an indented list.
- **`FileBrowser.qml`:** the shared explorer logic used by both views:
  - properties `rel` (current folder), `mode` ("list"/"grid"), `query`
  - breadcrumb `‹  Files / Study / Physics`
  - toolbar: **New folder** button, **+ Add file** button (`FileDialog` from
    `QtQuick.Dialogs`, `fileMode: FileDialog.OpenFiles`), **Paste** icon button
  - list or grid content
  - empty state: "Drop files here, or use Add file"
  - a **`DropArea`** over the whole browser: on `hasUrls`, show a dashed white highlight +
    "Drop to add to ‹folder›"; on drop, call `Library.addFiles`
  - errors from Library calls show as a small muted red line under the toolbar for 4 s
  - while `query` is non-empty, it shows `Library.search(query)` results as rows with the parent path
    as a subtitle (clicking a result's folder path navigates there)
- **Overlay `FilesPanel`:** replace the note-folder tree with `FileBrowser { mode: "list" }`, in a
  compact toolbar (icons only when narrow). It follows the overlay's search `query`. The header
  double-click → morph to the Full App section **`files`**, opening at the same `rel`. Folder click =
  drill in (no longer a morph). File click = `Library.open`. Note: the overlay covers the whole
  screen, so dragging from Explorer is rarely possible there. The Add and Paste buttons cover it, and
  drag & drop still works if a file is dragged in.
- **Full App:** a new section **`files`** (the drawer: replace "Folders" with one **Files** item using
  the folder glyph, between Focus and Tags). It shows the `FileBrowser` filling the notes area. The
  title bar shows "Files", and the list/grid toggle (two `IconButton`s) sits at the right of the
  browser toolbar. In the `files` section, `Ctrl+V` → `Library.pasteInto(rel)`, but only when no text
  field has active focus.
- **Overlay search:** the Files panel shows matching files and folders across the whole library.

## 5. Keep working (don't regress)
- the hotkey state machine and selftest
- the morph
- the overlay panels and splitters with `Settings` persistence
- the toast and the tray
- the Full App's masonry, editor, drawer and archive
- Remove the stale note-folder code paths cleanly (no dead references, no QML warnings).

## Verification (do all of it, and report honestly)
1. Run with `QT_FORCE_STDERR_LOGGING=1` and `timeout 60`. **Zero QML warnings or errors** in all runs.
2. **Screenshots** go in `D:\PROJECTS\Productivity app\prototype\.shots\` (create it; it is gitignored).
   - Always pass `--fake-backdrop` *and* `--library <a temp dir under .shots>`, so no real screen,
     wallpaper or personal files appear.
   - States: `full` (wallpaper-style glass background), `overlay`, `overlay-compose` (the expanded
     take-note box with a title and body typed), `files-full-grid`, `files-full-list`,
     `files-overlay` (drilled into Study/Physics), `files-context` (right-click menu open),
     `files-drop` (the drop highlight showing; set the state directly), `files-search` (query "ph"),
     `focus-break` (after a phase advance, showing "Short break").
   - Look at every one with the Read tool and fix what looks wrong.
3. **Extend `--selftest`** (keep the 4 hotkey checks). Add, printing PASS/FAIL each:
   - **Library** (in a fresh temp dir): seed creates the folders and files; `addFiles` copies a temp
     file in; a name clash gives " (2)"; `createFolder`; `rename` (and the invalid-name error); `move`
     (and the into-itself error); `remove` (hard delete under selftest); `search` finds the seeded PDF;
     a path that escapes the root is rejected; `copyToClipboard` puts a file URL on the clipboard and
     `pasteInto` adds it to another folder.
   - **Pomodoro:** with fast mode, set the phase-0 remaining to 1 s and running; after about 1.5 s
     assert phase == 1, label "Short break", paused, remaining == the short-break duration, and that the
     toast signal fired. Skip from phase 6 → 7 shows "Long break"; skip from 7 → 0 shows
     "Session 1 of 4".
   - **Take note:** call the capture path with a title + body and assert the new note has both.
4. `.venv/Scripts/python.exe -m ruff check prototype` must be clean.
5. Leave no running processes. Don't put files in the real Recycle Bin during tests.
