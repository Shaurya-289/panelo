# Plan: Prototype round 7 (final prototype round)

## Context
`prototype/` is a Qt Quick (PySide6 6.11) UI prototype with fake data (`qml/Proto/Store.qml`).
- Read `PLAN-round6.md` and `PLAN-round5-views.md` first, and skim the earlier `PLAN-*.md` files for
  conventions and gotchas.
- Then read the files you'll touch.
- **Only modify files inside `prototype/`; never touch `src/` or `tests/`.**

The click-through rule is mandatory:
- clickable things in popups and layers use an accepting `MouseArea`
- scrims use `ClickBlocker`
- add click-through checks for every new popup

All the decisions below are the user's. Build exactly these, and change nothing else.
**Keep** the Files Paste button. Ctrl+V stays dropped (if a Ctrl+V shortcut still exists, remove it).
Tags stay removed.

## 1. Repeating reminders
**Data:** a reminder gains `repeat: null | rule`. The rule shape is:
```js
{ kind: "daily" }
{ kind: "weekdays" }                       // Mon–Fri
{ kind: "weekly", days: [1,3,5] }          // 0=Sun..6=Sat, at least one day
{ kind: "monthly", day: 1 }                // day of month 1..31; short months use the last day
{ kind: "yearly" }                         // same month/day as the original due date
{ kind: "every", n: 2, unit: "hours"|"days"|"weeks" }
```
Every rule keeps the reminder's time of day. The exception is `every N hours`, which steps from the
current due time.

**Seed data:** add 3 repeating reminders:
- "Drink water", every 2 hours
- "Gym", weekly Mon/Wed/Fri 18:00
- "Pay rent", monthly on the 1st 09:00

**Logic:** put it in `qml/Proto/recurrence.js` (`.pragma library`). It needs:
- `nextOccurrence(rule, afterMs, anchorMs)` → the next due time strictly after `afterMs`
- `describe(rule)` → "every day", "weekdays", "every Mon, Wed, Fri", "monthly on the 1st",
  "every year on Nov 3", "every 2 hours"
- ordinal suffixes 1st/2nd/3rd/4th…
- edge cases: the 31st in February, and skipping past several missed occurrences when the app was closed

**Done** on a repeating reminder:
- **jumps to its next occurrence** (strictly in the future; it does not move to Past)
- resets the snooze count
- records the completion in a small `history` list on the reminder (the pop-out shows "Done 5×")

**Snooze** works as before (+10 min). It doesn't change the rule.

**Setting a repeat:** use the **"↻ Repeat" chip in the reminder pop-out** (`ReminderEditor`), next to
the time chip. **Not** through smart typing. The chip reads "Repeat" when off, or the rule text when
on. Clicking it opens a glass popup:
```
○ Never
○ Every day
○ Weekdays (Mon–Fri)
● Every week on   [M] [T] [W] [T] [F] [S] [S]   (toggle pills; at least one stays selected)
○ Every month on the [1st ▾]                    (defaults to the due date's day)
○ Every year
○ Custom: every [2] [hours ▾]                   (a Stepper plus a small unit menu: hours/days/weeks)
                                    [Done]
```
- Changes apply live.
- Picking a rule recalculates the due time to the next valid occurrence at or after the current due
  time. For example, setting weekly Mon/Wed/Fri on a Tuesday reminder moves it to Wednesday at the
  same time.

**List display** (Reminders page, overlay Reminders panel, Full App Upcoming card):
- a small **↻** glyph next to the time
- the **rule text in muted text under the title**, joined with the relative time:
  "every Mon, Wed, Fri · in 2 days"
- the Due-now card shows ↻ too

**Search** matches the reminder title as before.

**Tests (selftest):**
- `recurrence.js` unit checks for every rule, Feb 31 → Feb 28/29, catching up multiple missed
  occurrences, and `describe` strings
- Done on a weekly reminder moves it to the next day in the set, at the same time
- Done on every-2-hours moves it +2h
- the Repeat popup sets each rule kind and the chip text updates
- Never clears the rule
- the weekly pills can't all be deselected
- click-through checks for the Repeat popup and its menus

## 2. Search in the Full App title bar
- Add a search box in the Full App **title bar**, to the right of the section title. It is about
  360px wide, centered between the title and the window buttons, collapsing to an icon below 900px
  window width.
- Typing shows a **glass dropdown** of results grouped as NOTES, TASKS, REMINDERS and FILES. Each
  group shows up to 5 results, plus "Show all in ‹section›". Each row has an icon, the title, and a
  muted snippet or detail.
- Use ↑/↓ to move, Enter to open, and Esc to clear or close.
- **Opening a result:**
  - a note → the Notes section + note editor
  - a task → the Tasks section + task pop-out
  - a reminder → the Reminders section + reminder pop-out
  - a file → `Library.open` (stubbed under selftest)
  - a folder → the Files section at that folder
- Reuse the overlay's search filtering and `Library.search`.
- **Ctrl+F** focuses the box.
- The title-bar drag area must still work around the box, and the box must not leak clicks.
- **Tests:**
  - typing filters each group
  - Enter on a note opens its editor
  - the keyboard navigation
  - Esc
  - Ctrl+F
  - click-outside closes the dropdown without activating what's under it

## 3. Remove the checklist icon
- Remove the checklist `IconButton` from the "Take a note" bar (the Full App `TakeNoteBar` and the
  overlay capture box). Notes stay plain text. Keep the palette icon.

## 4. Overlay greeting and clock
- **Top-left of the overlay**, aligned with the panel group's left edge and vertically centered with
  the search bar: a **greeting**.
  - "Good morning" 05:00–11:59, "Good afternoon" 12:00–16:59, "Good evening" 17:00–21:59, and
    "Good night" 22:00–04:59.
  - Add **", ‹Name›"** if the user's name is set.
  - Style: `Theme.font`, about 22px, `Font.Light`, `Theme.text` at about 0.9 opacity. Calm.
- **Top-right**, aligned with the panel group's right edge: a **clock only (no date)**. It uses the
  same size and style, with tabular-ish digits, and updates every minute (align the timer to the
  minute boundary).
- **The clock follows the Windows 12h/24h setting.** Read it in Python with
  `QLocale.system().timeFormat(QLocale.ShortFormat)`: if it contains "h" (lowercase) or "AP"/"ap", use
  12-hour with a small muted "PM". Expose it as a context property or a Store field.
- Both fade in with the overlay. While a panel drag is in progress (when the snap bar shows), they
  fade out like the search bar does.
- If the screen is narrow and they would collide with the search bar, hide the greeting first.
- **Overlay only**, not the Full App.
- **Tests:**
  - the greeting text for boundary times (use an injectable clock)
  - the name appended and blank
  - 12h vs 24h formatting
  - hidden during a drag

## 5. First-launch 3-step tour (asks for the name)
- **First launch:** if the setting `tourDone` is false, the Full App opens and shows the tour, a
  centered glass card over a dimmed and blurred page (the `NoteEditor` scrim pattern, `ClickBlocker`).
  - The card has dots "● ○ ○  Step 1 of 3", a **Skip** button (always visible), and a **Next ›**
    button. Step 3's button reads **Get started**.
  - **Step 1, "Welcome":**
    - text: "What should we call you?"
    - a name `TextBox` (optional; Enter = Next)
    - text: "Press **Ctrl + Alt + Space** anytime to open your quick overlay. Press it twice
      quickly to open this window." This uses the real current hotkey text.
    - a **Change hotkey** text button that jumps to the Settings hotkey recorder after the tour
      finishes
  - **Step 2, "Notes & tasks":** a short line each about the Take a note bar, smart typing
    (`Call mom tomorrow 5pm !high`), and drag to reschedule. Include a tiny illustration made of
    existing components (a mini note card + a task row), not images.
  - **Step 3, "Files & focus":** your Files library (drag files in, click to open), the focus
    timer + fullscreen, and the layout picker in the overlay.
- **Finishing or skipping** sets `tourDone = true` and saves the name (if entered) as the setting
  `userName`.
- **Settings:** add **"Your name"** (a `TextBox`) at the top of GENERAL, used by the greeting, and a
  **"Show the welcome tour again"** text button. Showing the tour again sets `tourDone=false` and
  opens it.
- **Launch:** under a normal launch with `tourDone=false`, show the Full App with the tour instead of
  starting hidden. Add a CLI flag `--reset-tour` for testing. **Under selftest or screenshots**,
  `tourDone` defaults to true unless a test sets it.
- **Tests:**
  - the tour shows when `tourDone` is false
  - Next and Skip
  - the name is saved and shows in the overlay greeting
  - Change hotkey lands on the recorder
  - "Show the welcome tour again" works
  - click-through: clicking the scrim does nothing (the tour is modal; only Skip closes it)

## Verification (do all of it, and report honestly)
1. `QT_FORCE_STDERR_LOGGING=1` with timeouts (the selftest takes about 2.5 min; use `timeout 300`).
   Zero QML warnings or errors.
2. **`--selftest`:** all the current checks (277) still pass, plus the new ones. Paste the totals and
   any FAIL lines.
3. **Screenshots** (`--fake-backdrop`, temp `--library`):
   - `reminders-repeat` (the list with ↻ and rule texts)
   - `reminder-repeat-popup`
   - `search-dropdown`
   - `takenote-noicon`
   - `overlay-greeting` (a name set, with the clock)
   - `overlay-greeting-12h` (12h forced via a test flag)
   - `tour-1`, `tour-2`, `tour-3`
   - `settings-name`

   View each one and fix any visual problems.
4. `ruff check prototype` is clean; leave no processes running; never touch the real registry or
   database, or launch real apps.
