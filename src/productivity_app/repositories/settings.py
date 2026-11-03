"""Persistence access for the typed singleton settings row."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from productivity_app.db.models.initial import DEFAULT_SETTINGS_VALUES, AppSettings


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> AppSettings | None:
        return self._session.scalar(select(AppSettings).where(AppSettings.id == 1))

    def create_defaults(self, now_utc: datetime) -> AppSettings:
        settings = AppSettings(id=1, updated_at_utc=now_utc, version=1, **DEFAULT_SETTINGS_VALUES)
        self._session.add(settings)
        self._session.flush()
        return settings

    def update_if_version(
        self, expected_version: int, values: dict[str, object], now_utc: datetime
    ) -> AppSettings | None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(AppSettings)
                .where(AppSettings.id == 1, AppSettings.version == expected_version)
                .values(**values, updated_at_utc=now_utc, version=AppSettings.version + 1)
            ),
        )
        if result.rowcount != 1:
            return None
        return self.get()

    def record_auto_backup(self, at_utc: datetime) -> None:
        """Note when the last automatic backup ran.

        Bookkeeping, not a user edit: it neither bumps the settings version (so a cached
        copy a user is editing stays current) nor changes ``updated_at_utc``.
        """
        self._session.execute(
            update(AppSettings).where(AppSettings.id == 1).values(last_auto_backup_at_utc=at_utc)
        )
