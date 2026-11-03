# Productivity App

A local-first productivity app for Windows: notes, tasks, repeating reminders, timers and focus
sessions, a files library, full-text search, trash with a 30-day purge, and automatic
backup/restore. It is a PySide6 (Qt Quick) desktop app with a system tray icon, a global hotkey
and single-instance activation. All data is stored in a local SQLite database; files live in a
plain folder on disk.

The UI has two modes:
- **Quick overlay:** opened with a global hotkey (default Ctrl+Alt+Space). Five rearrangeable
  glass panels (Notes, To-Do, Files, Timers, Reminders) float over a blurred snapshot of the
  screen.
- **Full App:** a Keep-style notes grid plus pages for Tasks, Reminders, Focus, Files, Trash and
  Settings, over a blurred copy of the desktop wallpaper. Double-click an overlay panel header to
  morph the overlay into the Full App.

## Status

| Part | Status |
|---|---|
| Backend (database, services, migrations) | Done, tested |
| New Qt Quick UI | Done |
| UI connected to the backend | Done (bridge, scheduler, shell, `--selftest`) |
| Packaging / installer | Not started |

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
# The app (a second launch just asks the running one to show itself)
uv run python -m productivity_app.desktop [--startup] [--show]

# Automated UI checks: opens real windows on screen, runs on a temporary database and library
uv run python -m productivity_app.desktop --selftest

# Backend only (initializes/migrates the database, then exits)
uv run python -m productivity_app
```

`--startup` is what the "Start with Windows" entry runs: it starts hidden in the tray unless
`--show` is also given. The first launch shows the welcome tour.

Data and logs live in per-user directories chosen by `platformdirs` (app name
`ProductivityApp`); the database file is `productivity.db`. The Files library defaults to a
`Files` folder in the data directory and can be moved in Settings.

## Development and testing

```powershell
uv run pytest                  # all tests, with coverage (Qt runs offscreen)
uv run pytest tests/ui         # just the UI bridge tests
uv run ruff check .            # lint
uv run mypy                    # strict type checking
$env:QT_FORCE_STDERR_LOGGING = "1"; uv run python -m productivity_app.desktop --selftest
```

Tests use temporary databases and never touch the real data directory, registry Run key or
Recycle Bin. `--selftest` takes a few minutes and should finish with every check passing and no
QML warnings on stderr.

## Layout (`src/productivity_app/`)

| Package | Role |
|---|---|
| `core/` | Clock, config, event bus, exceptions, ids, lifecycle, logging, paths, recurrence rules |
| `db/` | Engine/session, unit of work, ORM models, Alembic migrations |
| `repositories/` | Data access per feature |
| `schemas/` | Pydantic input/output types |
| `services/` | Business logic and validation |
| `bootstrap.py` | Composition root; safe runtime replacement (e.g. after restore) |
| `files/` | Files library on disk (path guard, name clashes, moves), no database |
| `ui/` | The Qt Quick UI: `qml/` (App module), `fonts/`, `bridge/` (Python `Backend`, scheduler, library adapter, system helpers), `selftest/` |
| `desktop/` | Process shell: entry point, overlay/Full App controller, tray, hotkey, single-instance IPC, startup registration |
| `windows/` | Windows adapters: mutex, hotkeys, startup registry, window presentation |

## Design specs

The design decisions for each UI round are in [`docs/design/`](docs/design/). The plan for
turning the prototype into this app is [`docs/PLAN-real-app.md`](docs/PLAN-real-app.md). The
original prototype remains in git history (commit `acdedab`).

## Migrations

Migrations run automatically at startup. To work on them with the Alembic CLI, set
`sqlalchemy.url` explicitly (see `alembic.ini`).
