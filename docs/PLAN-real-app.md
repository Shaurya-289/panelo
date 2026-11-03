# Plan: Turn the prototype into the real app

## Context
The new Qt Quick UI is fully designed and approved in `prototype/`, which runs on fake data in
`prototype/qml/Proto/Store.qml`. The design specs are in `prototype/PLAN-*.md`. The real backend in
`src/productivity_app/` is a tested SQLite service layer: 633 non-desktop tests pass, and the
`tests/desktop/` tests cover the OLD Widgets UI.

**Goal:** the shipped app (`python -m productivity_app.desktop`) is the new UI, running on the real
backend, with the backend changed to support the new design. The old UI is removed and everything is
tested. Work happens in three phases. **Each phase ends green** (all tests passing, ruff and mypy
clean) **and is committed locally** (no push).

**Conventions** (from the prototype rounds; see `prototype/PLAN-overlay.md` § Known gotchas):
- `QT_FORCE_STDERR_LOGGING=1` on every Qt run.
- The click-through rule: pointer handlers don't accept events, so use an accepting `MouseArea` or
  `ClickBlocker`.
- Tests never touch the real registry, the real user database, real user files, or the real
  Recycle Bin, and never launch real apps or play real sounds.
- Screenshots only via QML `grabToImage` with a fake backdrop.

---

## Phase 1: Backend changes (the `src/productivity_app` service layer)
Add a single new Alembic migration, `20261103_0003_new_design.py`, that keeps the existing data. It
uses batch mode for SQLite and must have a working downgrade. Update the models, schemas,
repositories, services, events and search to match, and update and add tests.

1. **Several timers at once.**
   - Drop the unique partial index `ux_timers_one_active`.
   - Remove the "one active timer" rule from `TimerService` (start/resume no longer reject another
     active timer).
   - Replace `get_active_timer` with `list_active_timers()`. Keep `get_active_timer` only if other
     code needs it, returning the most recently started one.
   - `reconcile_expired_timer` → `reconcile_expired_timers()`, which completes every expired timer.
   - Focus sequences must coexist with other running timers.
   - Update the concurrency tests that asserted single-active.
2. **Note color.** Add `notes.color`, `String(16)`, NOT NULL, default `'none'`, with a CHECK in
   `('none','plum','rose','amber','moss','teal','slate','sand')`. Add it to the schemas (create,
   update, view, summary) and the service, and emit `NOTE_UPDATED`.
3. **Task date-only flag.** Add `tasks.due_has_time`, Boolean NOT NULL, default true for existing
   rows that have a due date. A date-only task stores its due date at local 00:00 with the flag off.
   Add it to the schemas and service. Also expose `completed_at_utc` in summaries if missing (the UI
   keeps tasks done today visible).
4. **Repeating reminders.**
   - **Columns on `reminders`:** `repeat_kind` (NULL or one of
     `'DAILY','WEEKDAYS','WEEKLY','MONTHLY','YEARLY','EVERY'`), `repeat_days` (a weekday bitmask int,
     Sun=bit0..Sat=bit6, for WEEKLY), `repeat_day_of_month` (1..31, for MONTHLY), `repeat_every_n`
     (≥1) and `repeat_every_unit` (`'HOURS','DAYS','WEEKS'`, for EVERY), and `anchor_at_utc`
     (the scheduled time used to compute the next occurrence). Add CHECK constraints for every
     combination.
   - **A pure module `core/recurrence.py`**, ported from `prototype/qml/Proto/recurrence.js`, with
     identical semantics:
     - `next_occurrence(rule, after, anchor, tz)` keeps the local time of day (DST-safe via `zoneinfo`
       and the reminder's `scheduled_timezone`), clamps month ends, catches up on missed
       occurrences, and steps EVERY-N-HOURS from the current due time.
     - `describe(rule)`.
     - Full unit tests.
   - **Service changes:**
     - Create and update accept an optional `repeat` rule.
     - `complete_reminder` on a repeating reminder **advances it to the next occurrence** (status
       stays PENDING, `due_at_utc` = the next one, `snooze_count` = 0, and an audit `ReminderEvent`
       `"COMPLETED_OCCURRENCE"` is recorded) instead of finishing it.
     - Snooze moves the due time, not the anchor.
     - Setting a rule recomputes the due time to the next valid occurrence ≥ the current due time.
     - Add a method or field for "times done" (counted from the events).
5. **Remove tags completely.** Drop `tags`, `note_tags` and `task_tags`. Delete `TagService`, the tag
   repository, schemas, events (`TAG_*`, `NOTE_TAGS_CHANGED`, `TASK_TAGS_CHANGED`) and the
   `set_*_tags` methods. Remove `tag_ids` from the note and task schemas and views, and update
   search. Delete or adjust the tests.
6. **Remove folders completely** (notes and tasks). The new Files page uses real disk folders. Drop
   the `folders` table and the `notes.folder_id` and `tasks.folder_id` columns (with their indexes).
   Delete `FolderService`, the folder repository, schemas, events (`FOLDER_*`, `NOTE_MOVED`,
   `TASK_MOVED`), `move_note`/`move_task`, and the folder filters in queries and search. Delete or
   adjust the tests. **Check `db/fts_contract.py`** and the FTS triggers, and rebuild the FTS
   tables/triggers in the migration if they reference the removed columns.
7. **Trash: a 30-day purge.**
   - Add `TrashService` (or methods on the notes and tasks services):
     - `list_trash()` → deleted notes and tasks, newest first, with `deleted_at_utc`
     - `restore` (the existing restore methods)
     - `delete_forever(kind, id)` → a hard delete, cascading to links: reminders linked to a purged
       task or note get their link set to NULL
     - `empty_trash()`
     - `purge_expired(now, days=30)`
   - Reminders are still deleted permanently, as the design says. Make sure `delete_reminder` is a
     hard delete, or that deleted reminders are purged immediately.
8. **New settings.** Add columns to `app_settings` and fields to the settings schemas:
   - `user_name` (≤60, default "")
   - `tour_done` (bool, default false)
   - `notification_style` (`'TOAST','WINDOWS','BOTH'`, default TOAST)
   - `notification_sound` (≤260, default "", the path)
   - `backdrop_blur` (0..100 int, default matching the prototype)
   - `backdrop_dim` (0..100)
   - `overlay_size_pct` (60..90, default 85)
   - `overlay_layout_json` (Text, default "")
   - `files_view_mode` (`'LIST','GRID'`)
   - `library_path` (≤1024, default "", meaning the platform default)
   - `auto_backup` (bool, default true)
   - `keep_backups` (1..60, default 7)
   - `last_auto_backup_at_utc` (nullable)

   Keep `minimize_to_tray` (the UI label is "Close button hides to tray"), `notifications_enabled`
   ("Reminder alerts"), `start_with_windows`, `global_hotkey` and the pomodoro lengths. Leave the
   `theme` column in place but unused (dark only).
9. **Automatic backups.** Add `BackupService.run_auto_backup_if_due(now)`: if `auto_backup` is on and
   more than 24 h have passed since `last_auto_backup_at_utc`, create a backup, record the time, and
   **prune** the automatic backups to the newest `keep_backups`. Manual backups count toward the same
   limit; keep the newest N overall, as the Settings list shows them all. Unit-test it with a fake
   clock.
10. **Focus stats for the UI.** Add `FocusService.daily_totals(days, tz)` → `[(date, sessions,
    minutes)]` and `streak(tz)` (consecutive days up to today with ≥1 completed focus session). Use
    them for "Today · Week (rolling 7 days) · streak".
11. **Files library (no database).** Move `prototype/files.py`'s `FileLibrary` logic into
    `src/productivity_app/files/library.py`. Make it pure Python where possible, with a thin Qt
    adapter in phase 2. Its root is the setting `library_path`, or
    `platformdirs.user_data_dir/Files`. Keep the path-escape guard, the name-clash " (2)" naming,
    and moving to the Recycle Bin via `QFile.moveToTrash` (in the adapter). Port its selftest
    checks to pytest using `tmp_path`.

**Phase 1 done when:**
- the migration upgrades a copy of a database at the current head with data preserved (test it)
  and downgrades
- `uv run pytest tests --ignore=tests/desktop` passes
- ruff and mypy (strict) are clean
- commit: `Backend: support new UI design (multi-timer, note color, recurrence, trash purge, settings, auto backup; remove tags and folders)`

---

## Phase 2: Connect the new UI to the backend (new real app package)
1. **Move the UI into the package:**
   - `prototype/qml` → `src/productivity_app/ui/qml`, with the `Proto` module renamed `App`.
   - Fonts → `src/productivity_app/ui/fonts`.
   - Make sure hatch includes these non-Python files in the wheel (`[tool.hatch.build]` include
     patterns).
2. **A Python bridge, `src/productivity_app/ui/bridge/`.** These are QObjects registered as context
   properties or singletons:
   - **Keep `Store.qml`'s public API** (property and function names the pages use) so the QML pages
     change as little as possible. **Re-implement `Store.qml` as a thin QML facade over a Python
     `Backend` QObject.**
   - The `Backend` holds the `Application`, exposes JS-friendly lists (list of dicts, with
     milliseconds-since-epoch for dates, as the pages expect), and **refreshes the affected lists
     on domain events** (subscribe via `Application.subscribe`).
   - Mutations call the services. CRUD runs synchronously on the GUI thread (local SQLite is fast).
     Backup, restore, the library move and big file copies run on a worker thread with completion
     signals.
   - Map the optimistic-concurrency `expected_version` and `StaleVersionError` cleanly: reload,
     then retry once or show a small error.
   - **Map the UI concepts to the backend:**
     - note `tint` ↔ `color`
     - task `done` ↔ COMPLETED/TODO via complete/reopen
     - task priority lower-case ↔ the enum
     - task `hasTime` ↔ `due_has_time`
     - reminder `repeat` rule ↔ the new columns
     - reminder Past = COMPLETED/DISMISSED
     - Due now = due ≤ now and active
     - the focus timer = a focus sequence
     - other timers = `TimerService` timers
     - presets = `TimerService` presets; seed 5m/10m/15m/1h if none exist
     - Trash = `list_trash`
     - Settings = `SettingsService`, with `overlay_layout_json` and `files_view_mode` persisted
       there
     - backups = `BackupService` (list, create, restore via `Application.restore_backup`, delete)
   - **Window state, the overlay search history and similar UI-only state** may stay in QSettings.
3. **A scheduler** in the bridge: a QTimer (and wake-from-sleep handling) that triggers due
   reminders (the toast/notification path with the chosen sound and style),
   `reconcile_expired_timers`, focus phase completion, the 30-day trash purge (at startup + hourly),
   and `run_auto_backup_if_due` (at startup + hourly).
4. **Shell:**
   - Port `prototype/app.py`'s `Controller` into `src/productivity_app/desktop/`. That covers the
     hotkey state machine, overlay, morph, Full App, toast, tray, snapshot/wallpaper providers,
     fullscreen focus and sound player.
   - Reuse the existing real pieces:
     - `desktop/instance.py` (single-instance activation IPC)
     - `desktop/startup.py` + `windows/startup.py` (**Start with Windows now real**, driven by the
       setting)
     - `desktop/hotkey.py` `HotkeyManager` (the hotkey stored in settings)
     - `windows/presentation.py`
   - `python -m productivity_app.desktop [--startup] [--show]` launches the new UI. `--startup`
     starts hidden in the tray, and the first launch shows the tour (`tour_done`).
5. **Tests:**
   - Port the prototype's `--selftest` checks into the real app as `--selftest`. It runs against a
     **temporary database and temporary library** created by the test harness, never the user's.
   - Add pytest tests for the bridge (offscreen Qt, a temp database): each mapping above, the
     event-driven refresh, the scheduler firing a due reminder, the repeating-reminder Done, the
     trash purge, the auto backup, and multiple timers.

**Phase 2 done when:**
- the real app runs end to end on a temp database (a smoke run plus screenshots matching the
  prototype's look)
- the real-app `--selftest` passes
- pytest, ruff and mypy are clean
- commit: `New Qt Quick UI connected to the real backend`

---

## Phase 3: Remove the old UI, clean up
1. **Delete the old Widgets UI:** `src/productivity_app/desktop/ui/`, the old `window.py`/`controller.py`
   if they're superseded, and anything else only it used. Delete the `tests/desktop/` tests that test
   the removed code, and keep or port the ones that test the reused shell pieces (instance, hotkey,
   startup, mutex, presentation).
2. **Delete `prototype/`** (it stays in git history). Move `prototype/PLAN-*.md` to `docs/design/`.
3. **Update the README:** status, run instructions, layout and testing.
4. **Final full run:**
   - `uv run pytest` (all), `ruff`, `mypy`
   - the real-app `--selftest`
   - a manual-style smoke run on a temp database
5. **Commit:** `Remove old Widgets UI and prototype; new UI is the app`. Then push to `origin main`.
