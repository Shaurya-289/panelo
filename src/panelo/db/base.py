"""Declarative base and shared ORM columns."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from sqlalchemy import Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from panelo.db.types import UTCDateTime


class Base(DeclarativeBase):
    pass


CreatedAt = Annotated[datetime, mapped_column(UTCDateTime(), nullable=False)]
UpdatedAt = Annotated[datetime, mapped_column(UTCDateTime(), nullable=False)]


class MutableEntity:
    # Services must supply values from their injected Clock.
    created_at_utc: Mapped[CreatedAt]
    updated_at_utc: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
