# Plan: Prototype round 5 (the remaining Full App views)

## Context
`prototype/` is a Qt Quick (PySide6 6.11) prototype of the app's new UI, with fake data in
`qml/Proto/Store.qml`. Read `PLAN-overlay.md`, `PLAN-round2.md`, `PLAN-round3.md` and
`PLAN-round4-layout.md` first for conventions and gotchas, then the current code. **Only modify files
inside `prototype/`; never touch `src/` or `tests/`.**

The Full App's sections `tasks`, `reminders`, `focus`, `settings` and `trash` still show the placeholder
"This view gets designed in the next step". The user has now designed them; build them exactly as
described. Reuse the existing look: `Theme` tokens, `GlassPanel`, `SectionLabel`, `TextBox`,
`IconButton`, `TextButton`, `Checkbox`, `ClickBlocker`, the `NoteEditor` pop-out pattern,
`TimePickerPopup`, `ContextMenu` and `FocusTimer`.

**The click-through rule is mandatory.** Pointer handlers (TapHandler/DragHandler) don't accept events,
so anything clickable inside a pop-out, popup or overlay layer must use an accepting `MouseArea`. Every
scrim or click-outside layer uses `ClickBlocker`. The shared buttons are already MouseArea-based.

**Parked by the user, do NOT build these:**
- tags: also **remove the "Tags" section from the drawer** and any tag UI. `#word` typed in smart-add
  is just left in the title.
- linking focus sessions to tasks
- Ctrl+V paste in Files
- repeating reminders

## 0. Data model upgrade (`Store.qml`)
The fake data uses display strings ("Today", "in 52 min"). Switch it to **real dates**, so the grouping
and drag-rescheduling are real:
- **Tasks:**
  `{ id, title, description, due: <ms epoch or null>, hasTime: bool, priority: "low"|"normal"|"high"|"urgent", done: bool, doneAt: <ms or null>, deletedAt: null }`.
  Seed about 14 tasks spread over: overdue (2–3), today, tomorrow, this week, later, and no date.
  Use mixed priorities, a few done today, and a couple done on earlier days.
- **Reminders:**
  `{ id, title, message, due: <ms>, done: bool, doneAt, snoozeCount, createdAt, link: null | {kind:"task"|"note", id} }`.
  "Due now" means `due <= now && !done`. Seed about 8: one due now, today, tomorrow, this week,
  later, and about 4 past (done). Link 2 of them to a task and a note.
- **Shared date helpers** (in Store, or a new `qml/Proto/dates.js` `.pragma library`):
  `groupOf(ms)` → overdue/today/tomorrow/week/later/none; `relative(ms)` ("in 52 min", "2 h ago");
  `formatDue(ms, hasTime)` ("17:00", "Mon", "Nov 12", "Mon 09:30").
- **Update every existing consumer** to the new fields:
  - overlay `TodoPanel`: Today + overdue, with the "Overdue" label
  - `TodayCard` (Full App rail)
  - `RemindersPanel` / `RemindersCard`: due now + upcoming
  - search
  - toasts
  - the selftest

  No regressions. "Today 3/9"-style counts should come from the real data.
- **Notes and tasks get `deletedAt`.** Deleting moves an item to the Trash instead of removing it (see
  §5). The existing note delete buttons now trash the note.
- **Focus stats (fake history):** `focusHistory: [{ start: ms, minutes: 25 }]` for the past about 10
  days. Derive today's sessions and minutes, this week's minutes, and the streak (consecutive days
  with at least 1 session). When a focus phase completes, append to it.
- **Timers:** keep the existing multiple timers. Presets are `[{id, label:"5m", ms}]`: 5m, 10m, 15m and 1h.
- **Settings (fake, persisted with `Settings`/QSettings using the existing `SettingsLocation`
  mechanism):**
  - `startWithWindows`, `closeHidesToTray`
  - `hotkey`
  - `focusMs`, `shortMs`, `longMs` (wire these into the pomodoro durations; `--fast-focus` still
    overrides)
  - `reminderAlerts`, `notificationStyle` ("toast"|"windows"|"both"), `toastSound`
  - `backdropBlur` (0..1), `backdropDim` (0..1), `overlaySize` (0.60..0.95, default 0.85)
  - `autoBackup`, `keepBackups` (default 7)
  - `libraryPath`
- **Fake backups:** `backups: [{id, at: ms, sizeMb}]`, seeded with about 5 daily ones.

## 1. Tasks page (section `tasks`)
```
┌────────────────────────────────────────────────────┬────────────────────┐
│  ┌──────────────────────────────────────────────┐  │ FOCUS (card)       │
│  │ + Call mom tomorrow 5pm !high                 │  │                    │
│  └──────────────────────────────────────────────┘  ├────────────────────┤
│  OVERDUE · 2                                       │ UPCOMING (card)    │
│ ▌ ☐ Submit expense report              Mon         │                    │
│  TODAY · 4                                         │                    │
│ ▌ ☐ Finish quarterly report          17:00         │                    │
│   ☑ B̶u̶y̶ ̶g̶r̶o̶c̶e̶r̶i̶e̶s̶   (crossed out, dimmed)            │                    │
│  TOMORROW · 1 / THIS WEEK · 2 / LATER · 3 / NO DATE · 5                  │
└────────────────────────────────────────────────────┴────────────────────┘
```
- **Groups,** in order: Overdue, Today, Tomorrow, This week (the rest of the next 7 days), Later,
  No date. Each has a `SectionLabel` header "TODAY · 4" (the count of not-done tasks). Hide empty
  groups, *except* while dragging: then all groups (including empty ones) show as drop targets. In
  each group, open tasks come first, sorted by time and then priority.
- **Row:**
  - the **left priority color bar** (3px, full row height, rounded): urgent = red (`Theme.danger`),
    high = amber, low = muted gray, normal = none
  - a `Checkbox`, then the title
  - right-aligned muted due text (`formatDue`; the time only for today and tomorrow)
  - hover background
- **Done tasks stay in their group,** crossed out and dimmed, **until the next day**. Tasks done on
  earlier days aren't shown on this page.
- **No in-progress state.** Done or not done only.
- **Smart-typing quick add** at the top (a `TextBox` in a GlassPanel, like `TakeNoteBar` collapsed).
  As you type, recognized parts are **highlighted** inline as rounded pills: for example `tomorrow
  5pm` shows as a date pill and `!high` as a priority pill. Show this as a preview row under the field:
  "→ Tomorrow 17:00 · High". Enter creates the task with the parsed parts removed from the title.
  The parser goes in `qml/Proto/smartparse.js` (`.pragma library`) and must handle:
  - `today`, `tonight` (20:00), `tomorrow`, weekday names (`mon` and `monday` → the next one),
    `next week` (next Monday), `in 3 days`, `in 2 h`/`hours`, `in 30 min`
  - dates: `nov 5` / `5 nov` / `5/11` (day/month)
  - times: `5pm`, `5:30pm`, `17:00`, `at 9`
  - priorities: `!low`, `!high`, `!urgent`, `!!` (= high), `!!!` (= urgent)

  Unparsed text is the title. Add unit checks to the selftest.
- **Drag to reschedule:** press-and-drag a row (using the threshold, with a MouseArea-based drag like
  round 4's headers) to lift it, then drop it on another group's header or rows. Dropping on Today,
  Tomorrow or This week sets that day (This week = the next day after tomorrow that's ≤7 days out; or
  the day under the cursor if you show day sub-labels; keep it simple: the first day of that range).
  It keeps the time if there was one. Later = +8 days; No date clears the due date. Dropping on
  Overdue is not allowed. Rows glide.
- **Click a row → task pop-out** (`TaskEditor.qml`, the same grow-from-row animation, scrim and
  blur as `NoteEditor`):
  - Checkbox + Title (editable)
  - Description (multi-line)
  - a **date/time** chip that opens a date + time picker popup (extend `TimePickerPopup` with quick
    days: Today / Tomorrow / Next week / No date, plus a small month grid and an HH:MM field)
  - a **priority** chip with a 4-option mini menu
  - a **"Remind me"** bell button that creates a reminder linked to this task at the due time (or
    opens the time picker if there's no due date)
  - Delete (→ Trash) and Done buttons; clicking outside saves and closes
- **Right rail on this page:** Focus + Upcoming cards only (**hide the Today card**). The other
  sections keep the full rail.

## 2. Reminders page (section `reminders`)
- A smart-typing quick add, "Remind me to…", with the same parser. A time is required: if none is
  typed, Enter opens the time picker.
- **Groups:**
  - **Due now** (highlighted like the overlay's due card, with **Snooze +10 min** and **Done**)
  - Today, Tomorrow, This week, Later
  - **Past** (done reminders), collapsed by default as "▸ PAST · 14"
- **Row:** a bell glyph, the title, a muted relative time ("in 52 min") under it, and the time on the
  right. If linked, a small **link chip** (a ☑ task or 📝 note glyph + name) **opens that task's
  pop-out or that note's editor** when clicked.
- **One-time only.** No repeat UI.
- **Click a row → reminder pop-out** (`ReminderEditor.qml`, the same pop-out pattern):
  - 🔔 + title (editable)
  - message (multi-line)
  - a 🕒 time chip that opens the date + time picker
  - the linked chip, if any
  - muted info: "Snoozed 2× · created Mon"
  - Delete, Done and Close
- **Right rail:** the full rail (Focus, Today, Upcoming).

## 3. Focus page (section `focus`)
```
│              24:13                    │
│         Session 2 of 4   ■ ▶ ⏭    ⚙   │
│        ● • ● • ○ • ○ •                │
│ Today 3 sessions · 1h 15m · Week 9h 40m · 🔥 4-day streak │
│ ───────────────────────────────────── │
│ TIMERS                                │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│ │Tea 02:59 │ │Laundry   │ │Stretch   │ │
│ │   ❚❚     │ │ 25:20 ❚❚ │ │ 10:00 ▶  │ │
│ └──────────┘ └──────────┘ └──────────┘ │
│ Start: [5m] [10m] [15m] [1h] [+ New]   │
```
- A **big calm pomodoro timer** centered: about 96px light digits. It reuses Store's focus state
  (shared with the rail and the overlay, so they stay in sync), with the phase dots, the session
  label, stop / play / skip, and a thin progress ring or line.
- **⚙ lengths popover** next to the controls: Focus / Short break / Long break, each a stepper
  (−/+ in 1-min steps plus an editable minutes field). It writes the same settings the Settings page
  uses, both ways.
- **Stats strip:** today's sessions and time, this week's time, and the streak, from
  `focusHistory`.
- **The TIMERS row:**
  - Cards for the other timers (running ones first): name, live mm:ss, a thin progress line,
    play/pause, and a ⋯ menu (Reset, Delete).
  - **Preset chips**, "Start: [5m] [10m] [15m] [1h] [+ New]": a chip starts a new timer named after
    it ("5 min timer"). "+ New" opens a small popup with a name and minutes.
  - When a timer finishes it fires the existing toast path ("Tea timer done").
- **Several timers can run at once** (the user chose this; the real backend change comes later).
- **Right rail on this page:** hide the Focus card (the page *is* focus); keep Today + Upcoming.

## 4. Settings page (section `settings`)
**One scrolling page** with section labels. Use toggle switches (a new `Switch.qml`: a pill track and
knob, white when on, with a MouseArea), sliders (a new `Slider.qml`: a thin track and white knob,
with a MouseArea drag) and steppers. Changes apply live and persist.
- **GENERAL:**
  - Start with Windows. **Prototype: store only, don't touch the registry**; show a muted
    "(applies in the real app)".
  - Close button hides to tray.
- **HOTKEY:** "Open overlay" uses a **press-to-record** box.
  - Click it → "Press keys…" (focused, with a highlighted border).
  - Press a combo → it is validated with `panelo.desktop.hotkey.parse_hotkey`
    (≥2 modifiers + a supported key), via a Python slot on the controller.
  - It then tries to **re-register** the real global hotkey through the controller's existing
    `HotkeyManager.replace(...)` (under selftest/headless: stub it).
  - Show "✓ available" or an error ("already used by another app" / "needs two modifiers").
  - Esc cancels.
- **FOCUS:** the three length steppers, synced with the Focus page popover.
- **NOTIFICATIONS:**
  - Reminder alerts (toggle).
  - Style: a segmented control of Glass toast / Windows notification / Both. Wire it into the
    existing toast vs native-notification path.
  - Toast sound (toggle; play `QApplication.beep()` or a short system sound when a toast shows, if on).
- **APPEARANCE:**
  - **Background blur** slider and **Background dim** slider. These apply live to
    `BlurredBackdrop` in both the Full App and the overlay.
  - **Overlay size** slider, 60–95% (applies to the overlay's panel-group size; keep the layout
    minimum sizes valid, and clamp if a panel would go below 220×160).
- **FILES:**
  - The library location: show the path, the storage used ("142 MB, 37 files"), **Open folder**
    (Explorer; stubbed under selftest), and **Change…** (a `FolderDialog`).
  - **Only accept an empty or new folder.** Confirm "Move your files to ‹path›?"; on confirm, move
    the library contents there (in Python, `shutil.move` per top-level entry), then switch the
    library root. Under selftest only use temp dirs.
- **BACKUPS:**
  - Automatic daily backup (toggle), and Keep last [7 ▾].
  - **Back up now** adds a fake backup entry at the top.
  - A list of the backups (date, size), each with **Restore** (confirm dialog → a fake success
    toast) and a ⋯ menu (Delete).
  - The prototype uses fake data and never touches the real database.

## 5. Trash page (section `trash`)
- A header note, "Items are deleted forever after 30 days", and an **Empty trash** button (with
  confirmation).
- Rows show trashed notes and tasks, newest first: a glyph (📝 note, ☐ task), the title, the kind,
  and "N days left" (from `deletedAt + 30 days`), plus **Restore** and **Delete forever** buttons.
- Items older than 30 days are purged on load and once an hour.
- **Empty state:** a large muted trash glyph + "Trash is empty".
- Deleting a note (anywhere: card toolbar, editor, overlay) or a task (pop-out) sends it here. The
  overlay and the lists hide trashed items. Restore puts the item back where it was.

## 6. Drawer and cleanup
- **Drawer:** Notes, Tasks, Reminders, Focus, Files, then a divider, Archive, Trash, Settings.
  **Tags removed.**
- **Remove the "This view gets designed in the next step" placeholder** completely.

## Verification (do all of it, and report honestly)
1. `QT_FORCE_STDERR_LOGGING=1`, `timeout 60`, zero QML warnings or errors. If the selftest gets close to
   60 s, it may use `timeout 120`.
2. **`--selftest`:** all existing checks (114) still pass, adapted where the data model changed.
   New checks:
   - **smartparse** units (each pattern above)
   - date grouping across midnight and week boundaries
   - task quick add creates a correctly parsed task
   - ticking a task strikes it and it stays in its group
   - dragging a task from Today to Tomorrow moves its date, keeping the time; drop on No date clears it
   - the task pop-out edits title/priority/date; "Remind me" creates a linked reminder
   - delete → the task appears in Trash → restore brings it back; delete forever removes it
   - a reminder in Due now: Snooze moves it to +10 min, and Done moves it to Past
   - the reminder link chip opens the linked note/task
   - a reminder quick add without a time opens the picker
   - the Focus ⚙ length change updates the Settings value, and vice versa
   - completing a focus phase adds to the history and updates the stats
   - a preset chip starts a timer; "+ New" creates a named one
   - several timers run at once
   - the settings toggles persist (re-read from the settings file)
   - hotkey record: an invalid combo shows the error, and a valid one calls the (stubbed) replace
   - the blur/dim sliders change `BlurredBackdrop`'s values in both windows
   - the overlay size slider changes the group rect
   - library Change to an empty temp folder moves the files and switches the root; a non-empty folder
     is refused
   - Back up now adds an entry
   - Empty trash with confirmation
   - **click-through** for every new pop-out and popup: clicking outside closes it without
     activating what's underneath
3. **Screenshots** (`--fake-backdrop`, temp `--library`): `tasks`, `tasks-quickadd` (pills plus a
   preview row), `tasks-drag` (mid-drag over Tomorrow), `task-editor`, `task-datepicker`,
   `reminders`, `reminder-editor`, `focus`, `focus-lengths`, `focus-newtimer`, `settings-top`,
   `settings-bottom`, `settings-hotkey-recording`, `trash`, `trash-empty` and `drawer`. View each one
   and fix any visual problems.
4. `ruff check prototype` is clean; leave no processes running.
