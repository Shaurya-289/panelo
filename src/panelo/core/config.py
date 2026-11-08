"""Small bootstrap-only configuration model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from panelo.core.paths import AppPaths


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    path: Path
    busy_timeout_ms: int = 5_000


@dataclass(frozen=True, slots=True)
class AppConfig:
    paths: AppPaths
    database: DatabaseConfig
    log_level: str = "INFO"


def default_config(app_name: str = "Panelo") -> AppConfig:
    paths = AppPaths.for_application(app_name)
    return AppConfig(paths=paths, database=DatabaseConfig(path=paths.data_dir / "panelo.db"))
