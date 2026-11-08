"""SQLite-safe SQLAlchemy types."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from panelo.core.exceptions import ValidationError

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Datetime values must be timezone-aware.")
    return value.astimezone(UTC)


def datetime_to_epoch_microseconds(value: datetime) -> int:
    """Convert an aware datetime without floating-point rounding."""
    delta = require_aware_utc(value) - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware datetimes as UTC epoch microseconds in SQLite."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> int | None:
        del dialect
        if value is None:
            return None
        return datetime_to_epoch_microseconds(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        return _EPOCH + timedelta(microseconds=int(value))
