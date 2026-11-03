# Plan: Prototype round 6 (focus reset + fullscreen, sound picker, settings tweaks)

## Context
`prototype/` is a Qt Quick (PySide6 6.11) UI prototype with fake data. Read `PLAN-round5-views.md`
(the latest) and skim the earlier `PLAN-*.md` files for conventions and gotchas. Then read the code
you'll touch: `qml/Proto/FocusPage.qml`, `FocusTimer.qml`, `SettingsPage.qml`, `Store.qml`,
`qml/Main.qml`, `qml/Overlay.qml`, `app.py`, `main.py` and the round-5 test modules. **Only modify
files inside `prototype/`; never touch `src/` or `tests/`.**

The user tested round 5 and asked for exactly these changes. Everything else works; don't change it.
The click-through rule still applies: clickable things in popups and layers use an accepting
`MouseArea`, and scrims use `ClickBlocker`.

## 1. Focus timer: a real Reset
The user found "no timer reset for focus mode" (the ■ stop button wasn't read as a reset).
- In `FocusTimer` (shared by the Focus page, the Full App rail card and the overlay Timers panel),
  replace the ■ stop button with a **↺ Reset** button (Segoe Fluent glyph `\uE72C` "Refresh"; or
  `\uE777` if that looks better). It pauses and resets the **current session** to its full length.
  Add a tooltip-style muted hint on hover: "Reset session".
- In the Focus page's **⚙ popover**, add a **"Start over from Session 1"** text button under the
  steppers. It pauses, goes to phase 0, and sets the full focus length.
- Selftest: Reset restores the full length and pauses; Start over goes to phase 0 / "Session 1 of
  4".

## 2. Fullscreen focus timer
- Add a **⛶ fullscreen** `IconButton` next to ⚙ on the Focus page (glyph `\uE740` FullScreen; exit
  glyph `\uE73F` BackToWindow).
- **Fullscreen focus mode:** the Full App window goes `showFullScreen()` and shows a dedicated
  full-window "zen" view (a new `FocusFullscreen.qml`) on top of everything else in the window:
  - the same `BlurredBackdrop` (wallpaper glass) background
  - huge light digits (about 22% of the screen height), the session label, the 8 phase dots and a
    thin progress ring or line, with the ↺ / ▶ / ⏭ controls and the exit button underneath
  - **calm mode:** after **3 s without mouse movement** the controls, the cursor and the exit button
    fade out, leaving only the time, the label and the dots; they fade back on any mouse move
  - exit with **Esc**, the exit button, or a double-click on the time. The window then returns to
    exactly its previous state (maximized or normal geometry); reuse the round-3 window-state code,
    and don't save the fullscreen geometry as the normal geometry
  - phase-end toasts still fire while in fullscreen
  - the global hotkey while in fullscreen: the first press exits fullscreen *and* hides the app to
    the tray, as for the normal Full App (keep the existing state machine; treat fullscreen as
    "Full App visible")
- Selftest: entering fullscreen shows the zen view and the window is fullscreen; Esc restores the
  previous maximized/normal state; calm mode hides the controls after 3 s (make the delay
  injectable for tests) and a mouse move shows them; a click on a control in fullscreen doesn't leak.

## 3. Settings: notification sound picker with preview
- Replace the **"Toast sound"** toggle with a **"Notification sound"** picker: a dropdown-style button
  that opens a glass popup list (`ContextMenu`-like, MouseArea rows).
  - **Options:** "None" plus these Windows sounds **if the file exists** in `C:\Windows\Media`
    (filter out missing ones), with friendly names:
    - Notify: `Windows Notify System Generic.wav`, falling back to `Windows Notify.wav`
    - Ding: `Windows Ding.wav`
    - Chimes: `chimes.wav`
    - Chord: `chord.wav`
    - Balloon: `Windows Balloon.wav`
    - Alarm: `Alarm01.wav`
    - Ring: `Ring01.wav`
  - **Selecting an option (and hovering over the rows, after a short delay) plays a preview of that
    sound** (the user asked: "play notification sound when user is choosing between sounds"). On
    hover/click, play it once via `QSoundEffect` (QtMultimedia) in Python, and stop any previous
    preview first.
  - The chosen sound plays when a **glass toast** shows: reminders, focus phase end, timer done.
    "None" = silent. Persist it as the setting `notificationSound` (the file path, or "" for none),
    replacing `toastSound`.
  - **Under selftest:** stub the player, so it records the calls and makes no real sound.
- Selftest: the list contains only existing files; selecting plays a preview (stub called with that
  path) and persists; a toast plays the selected sound; None plays nothing.

## 4. Settings: remove the focus lengths section
- Remove the **FOCUS** section (the three length steppers) from the Settings page entirely. The
  lengths are now edited **only** in the Focus page's ⚙ popover. Keep the stored values and their
  persistence; only the UI moves.
- Remove the old line "The same lengths as the ⚙ on the Focus page".
- Update the selftest checks that relied on the Settings steppers.

## 5. Overlay size: max 90%
- The **Overlay size** slider range becomes **60%–90%** (was 95%).
- Clamp any stored value above 0.90 down to 0.90 on load.
- The "raise size until the layout fits" logic must also cap at **0.90**, not 0.98. If the layout
  can't fit at 90%, keep 90% and let the existing minimum-size handling apply.
- Selftest: the slider max is 0.90; a stored 0.95 loads as 0.90.

## Verification (do all of it, and report honestly)
1. `QT_FORCE_STDERR_LOGGING=1` with timeouts; zero QML warnings or errors.
2. **`--selftest`:** all the current checks (236) still pass, adapted where §4 changed them, plus
   the new checks above. Paste the totals and any FAIL lines.
3. **Screenshots** (`--fake-backdrop`, temp `--library`): `focus` (the ↺ and ⛶ buttons visible),
   `focus-lengths` (with "Start over"), `focus-fullscreen`, `focus-fullscreen-calm` (controls
   hidden), `settings-top`, `settings-sound-picker` (popup open) and `settings-appearance` (the size
   slider at max = 90%). View each one and fix any visual problems.
4. `ruff check prototype` is clean; leave no processes running; no real sounds play during tests.
