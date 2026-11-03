# Plan: Quick Overlay + morph into Full App (prototype)

## Context
The user is redesigning the app's UI from scratch in **Qt Quick (QML) via PySide6 6.11**. A first
prototype already exists in `prototype/` (fake data, no backend): the **Full App**, a Google Keep–
inspired dashboard (masonry notes grid, right rail with Focus/Today/Upcoming, hidden ☰ drawer,
pop-out note editor). The user approved it.

Now we add the second mode, a **Quick Overlay** opened by a **global hotkey**, inspired by the
user's sketch. Its panels: Notes (tall, left), To-Do + Files (middle), Timers + Reminders (right).
Double-clicking a panel header **morphs** the overlay into the Full App on that section.

Everything stays inside `prototype/`. **Do not modify anything under `src/` or `tests/`.**
You may *import* from `src/productivity_app` (the launcher already adds `src` to `sys.path`).

## Existing prototype (read these first)
- `prototype/main.py`: launcher. Loads the Outfit font, loads `qml/Main.qml`, applies DWM dark mode,
  rounded corners and Acrylic (`productivity_app.windows.presentation.apply_backdrop`), sets
  `Theme.acrylic`. It has `--screenshot PATH`, `--state editor|drawer|compose` and `--delay MS`.
  Screenshots call `window.saveShot(path)` (QML `contentItem.grabToImage`), then the app quits.
- `prototype/qml/Main.qml`: the Full App window (frameless, custom title bar, resize edges,
  `section` property, `demo(state)`, `saveShot(path)`).
- `prototype/qml/Proto/` is a QML module (`qmldir` lists every component and singleton):
  - `Theme.qml` (singleton): **use these tokens everywhere** (colors, `radius: 6`, `gap`, `pad`,
    font `Outfit`, icon font `Segoe Fluent Icons`, `Theme.icon.*` glyphs, motion `fast/normal/easing`,
    note tints via `Theme.tint(name)`).
  - `Store.qml` (singleton): fake data plus mutators (`addNote`, `updateNote`, `deleteNote`,
    `toggleTask`, `addTask`). Arrays are **replaced, never mutated in place**, so bindings update.
  - Components: `GlassPanel`, `IconButton`, `Icon`, `TextButton`, `TextBox`, `Checkbox`,
    `SectionLabel`, `TintPicker`, `NoteCard`, `Masonry`, `TakeNoteBar`, `NoteEditor`
    (Keep-style pop-out with `open(note, sourceItem)`, `close()`, `progress`, `isOpen`),
    `NavDrawer`, `FocusCard`, `TodayCard`, `RemindersCard`.

## Design decisions (all made by the user; follow exactly)
**Look:** identical glass cards to the Full App (same Theme tokens, 6px corners, Outfit, neutral
white accent, dark). Panel titles are small uppercase `SectionLabel`s with **no underline/rule**.
Density between balanced and compact.

**Hotkey behavior** (default `Ctrl+Alt+Space`, overridable with `--hotkey`):
| State when pressed | Result |
|---|---|
| Nothing visible | Open overlay |
| Overlay visible, opened < 350 ms ago (double-press) | Morph overlay into the Full App (section `notes`) |
| Overlay visible (otherwise) | Close overlay |
| Full App visible (overlay not) | **Hide Full App to tray** |
The single press acts **immediately** (no waiting to detect a double-press). The second fast press
upgrades the already-open overlay into the Full App.

**Overlay window**
- Full-screen, on the screen under the mouse cursor. Frameless, stays on top, no taskbar entry
  (`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`). Opaque window: **no** transparency
  or Acrylic.
- **Background = blurred, darkened snapshot of the screen.** Capture `screen.grabWindow(0)` *before*
  showing the overlay and serve it to QML through a `QQuickImageProvider` (`image://snapshot/<n>`,
  bump `n` each time so it never caches). In QML: `Image` + `MultiEffect` blur (`blurMax` about 64,
  strong blur) plus a dark layer of about `rgba(0,0,0,0.45)`. Add a `--fake-backdrop` flag that uses a
  generated dark gradient image (with a few soft colored blobs) instead of a real screen capture.
  **Always use `--fake-backdrop` when taking screenshots**, so they never contain the user's screen.
- **The panel group occupies 85% of the screen, centered.**
- **Entrance:** background fades in while panels fade 0→1 and scale 0.97→1 (about 180 ms, OutCubic).
  **Exit:** the reverse in about 120 ms, then hide.
- **Close:** the hotkey again, or **Esc**. Esc first clears the search box if it has text, a second
  Esc closes. If the pop-out editor is open, Esc closes the editor first.

**Search (focused on open):** a slim search field centered *above* the panels
("Search everything…", search glyph). The cursor is in it as soon as the overlay opens. Typing filters
**live** across Notes (title + body), To-Do (title), Reminders (title) and Files (folder names,
keeping ancestors of matches visible), case-insensitive. Panels with zero matches show a muted
"No matches". If Notes has no match for a non-empty query, show a button **Create note "‹query›"**,
which adds a note titled with the query and opens it in the pop-out editor.

**Layout & resizing (fixed arrangement, resizable gaps):**
```
┌──────────┐ ┌────────────────┐ ┌──────────┐
│ NOTES    │ │ TO-DO          │ │ TIMERS   │
│          │ └────────────────┘ │          │
│          │ ┌────────────────┐ └──────────┘
│          │ │ FILES          │ ┌──────────┐
│          │ │                │ │REMINDERS │
└──────────┘ └────────────────┘ └──────────┘
```
- Column widths as fractions, default `[0.30, 0.40, 0.30]`. Middle column split (To-Do : Files)
  default `0.38`. Right column split (Timers : Reminders) default `0.50`.
- Splitter handles live in the gaps (gap = `Theme.gap`, hit area about 10px, resize cursor; on hover a
  faint 2px rounded line appears). Minimum panel size 200×140 px.
- Persist fractions with QML `Settings` from `QtCore` (category `overlay`). Set the organization and
  application names in Python (`ProductivityAppPrototype`).

**Double-click a panel header → morph into the Full App on a section:**
Notes→`notes`, To-Do→`tasks`, Files→`notes`, Timers→`focus`, Reminders→`reminders`.
On header hover, show a muted hint at the header's right: `⤢ Double-click to expand` (11px).

**Morph animation (about 320 ms, OutCubic):**
1. Python computes the Full App's target rect (its last normal geometry, default 1320×820 centered on
   the same screen), mapped into overlay coordinates, and calls QML `overlay.morphTo(section, rect)`.
2. In QML, the panel-group container animates its x/y/width/height from the 85% rect to the target
   rect. Panel contents fade out during the first 60%. A "window ghost" rectangle (Theme
   `raised`-like color, `Theme.border`, radius 8) fades in to fill the rect. The search bar fades out
   and the background darkness fades toward 0.
3. On finish, QML emits `morphFinished(section)`. Python sets the Full App's `section`, shows it at
   that geometry, raises/activates it, and **30 ms later** hides the overlay. Then reset the overlay
   state for the next open.

**Panels (Quick-action level: glance + small actions; real editing in the Full App)**
- **Notes:**
  - A one-line "Take a note…" `TextBox` at the top; Enter adds a note (title = text) and clears it.
  - Below it, a scrollable **compact list**, pinned first. Each row shows a tint dot (8px circle,
    `Theme.tint` at full alpha), the title (or the first line of the body if untitled) and a one-line
    muted preview, about 48px tall, with a hover background.
  - **Single click** a row → the existing `NoteEditor` pop-out opens **inside the overlay** (source
    item = the row). The overlay's panels blur behind it, as in `Main.qml`.
- **To-Do:**
  - Today's tasks plus overdue ones. Overdue rows get a small muted-red "Overdue" label.
  - `Checkbox` to tick, with a strike-through like `TodayCard`.
  - A "Add a task" field at the bottom.
  - Header right side: "3 / 7" done count.
- **Files:**
  - A **folder tree** with ▸/▾ chevrons (`` / ``) that expand and collapse **in place**,
    a folder glyph, the name, and a right-aligned muted note count. Indent 16px per level.
  - **Single click the folder name** → morph into the Full App with section `folder:<Name>`.
  - "+ New folder" at the bottom, with an inline text field; Enter adds a top-level folder.
- **Timers:**
  - Top: the big focus timer. Refactor `FocusCard` into `FocusTimer.qml` (content only, no
    GlassPanel) and make `FocusCard` = `GlassPanel { FocusTimer {} }` so the Full App rail still works.
  - Below it, **other running timers** from `Store.timers`:
    - 1 running → a single row (name, live `mm:ss`, thin progress line, play/pause).
    - More than 1 → a **carousel** showing one timer at a time, with ‹ › arrow buttons
      (`` / ``) and a muted "2 / 3" indicator; the slide animates horizontally.
    - 0 → nothing (no empty box).
  - Timers count down live (one 1 s ticker in `Store`).
- **Reminders:**
  - **Due now** reminders on top, highlighted with a slightly brighter panel and a bell glyph.
    - **Snooze** (+10 min) moves the reminder to upcoming as "in 10 min".
    - **Done** removes it.
  - Then **Upcoming**, ordered by time.
  - At the bottom, an add row: a title `TextBox` plus a clock `IconButton` that opens a small glass
    **time-picker popup**. It has quick chips ("In 15 min", "In 1 h", "Tonight 20:00",
    "Tomorrow 09:00") and an `HH:MM` field. Enter or picking a chip adds the reminder.

**Alerts (when the overlay is not open):** a **custom glass toast** window at the bottom right of
the primary screen (frameless, on top, `Qt.Tool`, about 340×96, `Theme.raised`, border, radius 6).
It shows the bell, title, time, and **Snooze** and **Done** buttons. It slides up and fades in,
auto-dismisses after 8 s, and pauses while hovered. The **fallback** is a Windows notification via
`QSystemTrayIcon.showMessage` when the toast can't be shown, or when `--native-toast` is passed.

**Tray icon** (switch `QGuiApplication` → `QApplication` for `QSystemTrayIcon`/`QMenu`;
`setQuitOnLastWindowClosed(False)`):
- The menu has *Open overlay*, *Open full app*, *Demo: fire reminder*, and *Quit*.
- Left-click the icon → open the overlay.
- Use a simple generated icon (white rounded square on dark, drawn with `QPainter`).
- The Full App's ✕ button and the hotkey **hide** the app to the tray; only *Quit* exits.
- On start, show nothing and print `Ready — press Ctrl+Alt+Space` (or the chosen hotkey), plus a
  tray message once. `--show full` / `--show overlay` opens one at start.

## Data changes (`Store.qml`)
- `folders` becomes a tree:
  `[{ name: "Study", count: 12, children: [{name:"Physics",count:4,children:[]}, {name:"Maths",count:6,children:[]}, {name:"Chemistry",count:2,children:[]}] }, { name: "Coding", count: 8, children: [...] }, { name: "Personal", count: 5, children: [{name:"Journal",...},{name:"Travel",...}] }]`.
  **Update `NavDrawer.qml`** to use `modelData.name` for the top-level folders.
- Add a `folder` field to the fake notes (spread them across Study / Coding / Personal and a few
  subfolders). In `Main.qml`, make section `folder:<Name>` show the masonry grid filtered to that folder
  (including its subfolders) instead of the placeholder. The title shows the folder name.
- Tasks: add 1–2 overdue tasks (`due: "Overdue"`).
- `timers`: `[{ id, name: "Tea", remaining: 180, total: 300, running: true }, { name: "Laundry", remaining: 1520, total: 2700, running: true }, { name: "Stretch break", remaining: 600, total: 600, running: false }]`.
  "Running" here means active (running or paused). Include all three so the carousel shows.
- Reminders: add a `due: true` reminder ("Drink water", "now"). Add `addReminder(title, time,
  relative)`, `snoozeReminder(id)` and `completeReminder(id)`.
- Add `addFolder(name)` and a ticker: `property Timer ticker: Timer { interval: 1000; repeat: true;
  running: true; onTriggered: ... }` that decrements running timers (replace the array).

## Files to add/change (all under `prototype/`)
- **New:**
  - `qml/Overlay.qml`: overlay window (background, search, splitter layout, five panels, NoteEditor
    instance, `morphTo`, `open()`/`close()` with animations, `demo(state)`, `saveShot(path)`).
  - `qml/Toast.qml`
  - `qml/Proto/` components: `OverlayPanel.qml` (GlassPanel + header with hint and double-click),
    `NotesListPanel.qml`, `TodoPanel.qml`, `FilesPanel.qml`, `TimersPanel.qml`, `RemindersPanel.qml`,
    `FocusTimer.qml`, `Splitter.qml`, `TimePickerPopup.qml`, `SearchField.qml`.
  - **Add every new component to `qmldir`.**
- **Change:**
  - `main.py`: QApplication, tray, hotkey controller, snapshot provider, show/hide/morph orchestration,
    toast, CLI flags.
  - `qml/Main.qml`: `visible: false` by default (Python shows it); closing → hide; folder sections.
  - `Store.qml`, `NavDrawer.qml`, `FocusCard.qml`.
- **Python structure in `main.py`** (or split into `prototype/app.py` if it grows over about 300 lines):
  a `Controller(QObject)` holding the engine, the `full` and `overlay` windows, the toast, the tray,
  `last_overlay_open` (monotonic time), and methods `on_hotkey()`, `open_overlay()`, `close_overlay()`,
  `morph_to_full(section)`, `show_full(section)` and `hide_full()`.

**Global hotkey:** reuse the existing code. Do not write new WinAPI hotkey code.
```python
from productivity_app.desktop.hotkey import HotkeyManager
from productivity_app.windows.hotkeys import WindowsHotkeyBackend
manager = HotkeyManager(app, overlay_window, WindowsHotkeyBackend(), controller.on_hotkey)
manager.start("Ctrl+Alt+Space")   # needs >=2 modifiers + A-Z/0-9/Space/F1-F11
```
`overlay_window` only needs `winId()`; a hidden window works. If `start` raises (e.g. the key is
taken), print a clear warning and keep running (the tray still works). Call `manager.close()` on quit.

## Known gotchas (already hit in this prototype)
- A QML property named `on<Capital>…` (e.g. `onAccent`) is parsed as a signal handler. Don't do that.
- On Windows, Qt warnings only reach the console with `QT_FORCE_STDERR_LOGGING=1`. Use it on every run.
- Bash heredocs containing many QML files broke once. Create files with the Write tool.
- `engine.rootObjects()[0]` is a `QWindow` wrapper in PySide; call QML functions on it directly
  (e.g. `window.saveShot(path)`), not `grabWindow()`.
- Get singletons in Python with `engine.singletonInstance("Proto", "Theme")`.
- Repeaters over JS arrays recreate their delegates when the array is replaced. Avoid `add` transitions
  that would flash the whole list.
- A scrim `TapHandler` underneath a sheet still receives taps on empty sheet areas. Put
  `MouseArea { anchors.fill: parent }` in the sheet to swallow them (see `NoteEditor.qml`).

## Verification (do all of it, and report the results honestly)
1. `QT_FORCE_STDERR_LOGGING=1 .venv/Scripts/python.exe prototype/main.py ...` must print **no QML
   warnings or errors**.
2. Add `--state` values for screenshots: `overlay`, `overlay-search` (query "re"), `overlay-editor`,
   `overlay-morph` (capture mid-morph, about 50%), `overlay-carousel`, `timepicker`, `toast`, and the
   existing `full`/`editor`/`drawer`/`compose` (Full App, now shown explicitly), plus `folder` (Full App
   on `folder:Study`). Overlay screenshots **must use `--fake-backdrop`**. Save screenshots to the
   scratchpad directory given in your task prompt, flatten translucent PNGs onto `#202125` if needed,
   then **look at each one** with the Read tool and fix anything misaligned, clipped or overlapping.
3. Smoke-test the hotkey logic without pressing real keys: add `--selftest`, which calls
   `controller.on_hotkey()` in timed sequences and asserts the state after each:
   - press → overlay open
   - press after 500 ms → closed
   - press, then press after 200 ms → full app visible, overlay hidden
   - press → full app hidden
   Print PASS/FAIL per step, then exit.
4. `.venv/Scripts/python.exe -m ruff check prototype` must be clean.
5. Don't leave windows or processes running. Every screenshot or selftest run must exit by itself
   (use `timeout 60`).
