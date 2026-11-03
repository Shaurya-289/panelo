# Plan: Prototype round 3 (click-through fix, window state, smoother morph, small fixes)

## Context
`prototype/` is a Qt Quick (PySide6 6.11) prototype of the app's new UI with fake data. Read
`PLAN-overlay.md` and `PLAN-round2.md` first for conventions and gotchas, then the current code
(`main.py`, `app.py`, `files.py`, `qml/Main.qml`, `qml/Overlay.qml`, `qml/Toast.qml`, all of
`qml/Proto/`). The user tested round 2 and reported the issues below. Fix exactly these; don't
redesign anything. **Only modify files inside `prototype/`; never touch `src/` or `tests/`.**

Parked by the user, so do NOT work on these: Ctrl+V paste in Files, panel resizing.

## 1. Important bug: click-through on dimmed layers and click-outside layers
**Symptom (user):** with the Full App drawer open, clicking also clicked what was underneath
(e.g. a note opened).
**Root cause:** the drawer scrim (`NavDrawer.qml`, the `TapHandler` on the scrim Rectangle) uses
only a `TapHandler`. Pointer handlers do not *accept* or stop events, so the click closes the
drawer **and** reaches the items below it.
**Fix:** every scrim and every "click outside to close" layer must **block** the press. Use a
`MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; hoverEnabled: true; onPressed: …close…;
onWheel: (w) => w.accepted = true }`, or an equivalent that accepts press, release, wheel and hover,
so nothing underneath gets the click or a hover highlight. Close on press (or on click), and swallow
the matching release.

**Audit and fix all of these.** For each, confirm that the click closes the layer and that nothing
underneath reacts (no note opens, no hover highlight, no checkbox toggles):
- `NavDrawer` scrim (Full App)
- `NoteEditor` scrim (Full App and overlay)
- `TimePickerPopup` click-outside
- `ContextMenu` click-outside, plus the "Move to folder…" picker
- the `TakeNoteBar` / overlay capture-box click-away layer (round 2 made it a passive `TapHandler`
  at `z: -1` so text fields still work). Make sure the click that saves and collapses the box does
  **not** also trigger what's under it, while text fields inside the box still receive clicks.
- Any other popup or overlay layer you find (grep for `TapHandler` used with `close`/`open = false`).

**Add selftest checks** using real QTest mouse events:
1. With the drawer open, click on the scrim over a note card. The drawer closes and the editor does
   **not** open.
2. With the editor open, click outside it over another note. The editor closes and the second note
   is **not** opened.
3. With the right-click menu open, click outside over a file. The menu closes and the file is **not**
   opened.

## 2. Full App remembers its window state
**Symptom:** the Full App always opens at normal size, even after the user maximized it, whether
it's opened by the morph, a double hotkey press or the tray.
**Fix:**
- Persist the window state: normal geometry (x, y, w, h), maximized yes/no, and the screen. Save on
  move, resize and state change (debounced) and on hide. Use `QSettings` in Python (org/app
  `ProductivityAppPrototype`, group `full`). It must also survive app restarts.
- **Every** way of showing the Full App restores it, maximized if it was maximized. That covers
  `show_full`, the morph finish, a double press, the tray and `--show full`.
- **Morph target:** if the Full App was maximized, the morph's target rect is the screen's
  **available geometry** (excluding the taskbar), and the window is then shown maximized. Otherwise
  the target is the saved normal geometry (clamped onto the current screen).
- Restoring from maximized (the title-bar button, or a double-click on the title bar) returns to the
  saved normal geometry.
- The frameless window must still maximize correctly to the available geometry (not covering the
  taskbar). Check `window.showMaximized()` with the frameless flag; if Qt covers the taskbar or
  misbehaves, set the geometry to `availableGeometry` manually and track a `maximized` flag.

**Selftest:**
- Maximize, hide, then show via a double press; assert it's maximized.
- Un-maximize, move it to a new position, hide, then show via the tray path; assert the same
  geometry.
- Restart persistence: write the settings, re-create the controller in-process if feasible
  (otherwise unit-test the load and save functions).

## 3. Smoother morph
**Symptom (user):** "smooth, but not that smooth". Likely causes to investigate and fix:
- **Animating the panel group's width/height** forces every panel to re-layout each frame (text
  wrapping, lists, splitters). Instead, during the morph, **cache the panel group as a texture**
  (`layer.enabled: true` only while morphing, `layer.smooth: true`) and animate the cached item's
  **scale and position** (transform) instead of width/height. Or render a static snapshot
  (`grabToImage` before the morph starts) and animate that image.
- **Animating the full-screen `MultiEffect` blur amount** re-runs a large blur every frame. Instead,
  keep the blurred backdrop static and **crossfade** (animate opacity) between the blurred overlay
  backdrop and the target. Never animate `blur` or `blurMax` during the morph.
- **The first `show()` of the Full App window is slow** (scene graph and window creation). Pre-warm
  it: create the Full App window at startup and render it once, e.g. show it off-screen at opacity 0,
  or call `create()` and render a frame, then hide it. Showing it at the end of the morph should then
  be instant.
- **The overlay → Full App handoff:** show the Full App *before* hiding the overlay (already 30 ms).
  Make sure the Full App's first frame is already rendered, using `frameSwapped` or one
  `QTimer.singleShot(0)` after `show()`, so there's no flash of an empty window.
- Use a single easing curve (`Easing.OutCubic` or `Easing.InOutCubic`) for 300–340 ms. Measure the
  frame times during the morph: connect to the overlay window's `frameSwapped`, record the
  timestamps, and print the average and max frame interval with a `--morph-stats` flag. Report the
  numbers before and after. The goal is a steady 60 fps (the max frame interval under 25 ms on this
  machine).

## 4. "Close" → "Done"
In the "Take a note" capture boxes (Full App `TakeNoteBar` and the overlay capture box), rename the
**Close** button to **Done**. (The pop-out note editor's button can stay "Close". The user only
mentioned the take-note box. If the editor's button shares the same component, keep the editor as
"Close".)

## 5. Opening a file from the overlay closes the overlay
When a file is opened from the overlay's Files panel (click a file, or search result → file), call
`Library.open(rel)` and **close the overlay** (the normal exit animation), so the opened app (PDF
reader, PowerPoint…) isn't hidden behind the full-screen, always-on-top overlay. Also apply this to
**Show in Explorer** from the overlay's right-click menu. Opening files from the Full App does not
hide the Full App.

## Verification (do all of it, and report honestly)
1. Run with `QT_FORCE_STDERR_LOGGING=1` and `timeout 60`. **Zero QML warnings or errors.**
2. **`--selftest`:**
   - all the existing checks still pass (39 at last count)
   - the new click-through checks (§1)
   - the window-state checks (§2)
   - a check that opening a file from the overlay closes it (stub `Library.open` under selftest so no
     real app launches)
   Paste every PASS/FAIL line.
3. **Morph frame stats** (`--morph-stats`, with `--fake-backdrop` and a temp `--library`): report the
   average and max frame interval before and after your changes, for both a normal and a maximized
   target.
4. **Screenshots** go in `prototype/.shots/`, always with `--fake-backdrop` and `--library <temp dir
   under .shots>`: the drawer open, the editor open, the take-note box expanded showing "Done", and a
   mid-morph frame. View each one and fix any problems.
5. `.venv/Scripts/python.exe -m ruff check prototype` must be clean.
6. Leave no running processes; don't launch real apps or touch real files during tests.
