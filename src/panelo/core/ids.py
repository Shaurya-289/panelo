"""Injectable UUID generation."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4


class IdGenerator(Protocol):
    def new(self) -> str:
        """Return a canonical UUID4 string."""


class Uuid4Generator:
    def new(self) -> str:
        return str(uuid4())
