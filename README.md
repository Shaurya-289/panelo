# Productivity App

A local-first productivity app for Windows: notes, tasks, reminders, timers and focus
sessions, folders and tags, full-text search, and backup/restore. It runs as a PySide6 (Qt)
desktop app with a system tray icon, a global hotkey, and single-instance activation.
All data is stored in a local SQLite database.

## Status: work in progress

| Part | Status |
|---|---|
| Backend (database, services, migrations) | Working, fully tested |
| Current desktop UI (`src/productivity_app/desktop/ui/`) | Working, being replaced |
| **New UI** (`prototype/`) | Fully designed Qt Quick prototype, running on **sample data** |
| Connecting the new UI to the backend | Not started |

### The new UI prototype

The prototype is a Qt Quick (QML) redesign with two modes:
- **Quick overlay:** opened with a global hotkey. It has five rearrangeable glass panels
  (Notes, To-Do, Files, Timers, Reminders) over a blurred snapshot of the screen.
- **Full app:** a Keep-style notes grid plus pages for Tasks, Reminders, Focus, Files, Trash
  and Settings, over a blurred copy of the desktop wallpaper.

Double-click a panel header to morph the overlay into the full app.

```powershell
uv run python prototype/main.py              # starts in the tray; press Ctrl+Alt+Space
uv run python prototype/main.py --reset-tour # show the first-launch tour
uv run python prototype/main.py --selftest   # automated UI checks
```

The design specs for each prototype round are in `prototype/PLAN-*.md`.
The prototype does not touch the real database.

## Requirements

- Windows 10/11
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- SQLite built with FTS5 (checked at startup)

## Setup

```powershell
uv sync
```

## Run

```powershell
# Desktop app
uv run python -m productivity_app.desktop [--startup] [--show]

# Backend only (initializes/migrates the database, then exits)
uv run python -m productivity_app
```

`--startup` is used by the launch-at-login entry and starts hidden unless `--show` is also given.

Data and logs live in per-user directories chosen by `platformdirs`
(app name `ProductivityApp`); the database file is `productivity.db`.

## Development

```powershell
uv run pytest                  # tests with coverage
uv run ruff check .            # lint
uv run mypy                    # strict type checking
```

Desktop tests run Qt offscreen. Some of them start real processes and use the real Windows
mutex, registry, and local IPC, so they can be sensitive to the environment.

## Layout (`src/productivity_app/`)

| Package | Role |
|---|---|
| `core/` | Clock, config, event bus, exceptions, ids, lifecycle, logging, paths |
| `db/` | Engine/session, unit of work, ORM models, Alembic migrations |
| `repositories/` | Data access per feature |
| `schemas/` | Pydantic input/output types |
| `services/` | Business logic and validation |
| `bootstrap.py` | Composition root; safe runtime replacement (e.g. after restore) |
| `desktop/` | Qt shell: controller, tray, hotkey, activation IPC, window, `ui/` panels |
| `windows/` | Windows adapters: mutex, hotkeys, startup registry, window presentation |

## Migrations

Migrations run automatically at startup. To work on them with the Alembic CLI, set
`sqlalchemy.url` explicitly (see `alembic.ini`).
