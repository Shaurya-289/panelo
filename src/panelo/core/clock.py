"""Clock abstractions used by time-sensitive application code."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime:
        """Return an aware UTC datetime."""

    def monotonic_ns(self) -> int:
        """Return a monotonic timestamp in nanoseconds."""


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()
