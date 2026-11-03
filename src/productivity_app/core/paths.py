"""Windows-friendly per-user application paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir


@dataclass(frozen=True, slots=True)
class AppPaths:
    data_dir: Path
    log_dir: Path

    @classmethod
    def for_application(cls, app_name: str, app_author: str = "ProductivityApp") -> AppPaths:
        return cls(
            data_dir=Path(user_data_dir(app_name, app_author)),
            log_dir=Path(user_log_dir(app_name, app_author)),
        )

    def ensure_data_directory_exists(self) -> None:
        """Create the required data directory; logging owns its optional directory."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
