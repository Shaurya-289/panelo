"""Windows-friendly per-user application paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir

APP_NAME = "Panelo"
APP_AUTHOR = "Panelo"

# The app was called "ProductivityApp" before it became Panelo.  These name the old per-user
# locations so existing data can be found and carried over; nothing else should use them.
LEGACY_APP_NAME = "ProductivityApp"
LEGACY_APP_AUTHOR = "ProductivityApp"
LEGACY_DB_FILENAME = "productivity.db"
LEGACY_LOG_FILENAME = "productivity-app.log"
LEGACY_RUN_VALUE_NAME = "ProductivityApp"  # old HKCU\...\Run value name
LEGACY_GUI_SCRIPT = "productivity-app.exe"  # old launcher the Run value pointed at
LEGACY_QT_ORGANIZATION = "ProductivityApp"  # old QSettings() org/app (HKCU\Software\...)
LEGACY_QT_APPLICATION = "ProductivityApp"


@dataclass(frozen=True, slots=True)
class AppPaths:
    data_dir: Path
    log_dir: Path

    @classmethod
    def for_application(cls, app_name: str, app_author: str = APP_AUTHOR) -> AppPaths:
        return cls(
            data_dir=Path(user_data_dir(app_name, app_author)),
            log_dir=Path(user_log_dir(app_name, app_author)),
        )

    def ensure_data_directory_exists(self) -> None:
        """Create the required data directory; logging owns its optional directory."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
