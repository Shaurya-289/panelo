from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from productivity_app.bootstrap import create_application
from productivity_app.core.clock import Clock
from productivity_app.core.config import AppConfig, DatabaseConfig
from productivity_app.core.paths import AppPaths


class FakeClock(Clock):
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.monotonic = 0

    def now_utc(self) -> datetime:
        return self.now

    def monotonic_ns(self) -> int:
        return self.monotonic


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    paths = AppPaths(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    return AppConfig(
        paths=paths, database=DatabaseConfig(path=paths.data_dir / "test.db"), log_level="CRITICAL"
    )


@pytest.fixture
def application(app_config: AppConfig, clock: FakeClock):
    app = create_application(app_config, clock)
    yield app
    app.close()
