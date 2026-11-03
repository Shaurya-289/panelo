"""Focus sequence/session mappings.

NOTE: these tables hold foreign keys to tables mapped in ``initial`` but no ORM
relationships, so SQLAlchemy orders inserts by ``module.ClassName``. Keep this module
name sorting after ``initial`` or focus rows will be inserted before their parents.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    literal_column,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from productivity_app.db.base import Base, MutableEntity
from productivity_app.db.types import UTCDateTime


class FocusSequence(MutableEntity, Base):
    __tablename__ = "focus_sequences"
    __table_args__ = (
        CheckConstraint("state IN ('ACTIVE', 'COMPLETED', 'CANCELLED')", name="ck_focus_sequences_state"),
        CheckConstraint("current_phase_index BETWEEN 0 AND 7", name="ck_focus_sequences_phase_index"),
        CheckConstraint("work_duration_ms BETWEEN 1000 AND 86400000", name="ck_focus_sequences_work_duration"),
        CheckConstraint("short_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_focus_sequences_short_duration"),
        CheckConstraint("long_break_duration_ms BETWEEN 1000 AND 86400000", name="ck_focus_sequences_long_duration"),
        CheckConstraint("version >= 1", name="ck_focus_sequences_version"),
        CheckConstraint(
            "(state = 'ACTIVE' AND completed_at_utc IS NULL AND cancelled_at_utc IS NULL) "
            "OR (state = 'COMPLETED' AND completed_at_utc IS NOT NULL AND cancelled_at_utc IS NULL) "
            "OR (state = 'CANCELLED' AND cancelled_at_utc IS NOT NULL AND completed_at_utc IS NULL)",
            name="ck_focus_sequences_terminal_timestamps",
        ),
        Index(
            "ux_focus_sequences_one_active", literal_column("1"), unique=True,
            sqlite_where=text("state = 'ACTIVE'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    current_phase_index: Mapped[int] = mapped_column(Integer, nullable=False)
    work_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    short_break_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    long_break_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FocusSession(Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (
        CheckConstraint("phase_kind IN ('FOCUS', 'SHORT_BREAK', 'LONG_BREAK')", name="ck_focus_sessions_kind"),
        CheckConstraint(
            "(sequence_id IS NULL AND phase_index IS NULL) OR "
            "(sequence_id IS NOT NULL AND phase_index BETWEEN 0 AND 7)",
            name="ck_focus_sessions_sequence_phase",
        ),
        UniqueConstraint("sequence_id", "phase_index", name="uq_focus_sessions_sequence_phase"),
        Index("ix_focus_sessions_sequence_phase", "sequence_id", "phase_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence_id: Mapped[str | None] = mapped_column(
        ForeignKey("focus_sequences.id", ondelete="CASCADE")
    )
    timer_id: Mapped[str] = mapped_column(
        ForeignKey("timers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    phase_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    phase_index: Mapped[int | None] = mapped_column(Integer)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
