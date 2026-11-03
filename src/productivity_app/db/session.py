"""Session factory construction."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # A session captured from a completed UoW must not silently reset itself and
    # reconnect.  The lifecycle proxy is the primary guard; this is a second
    # line of defence for any accidental raw-session escape.
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        close_resets_only=False,
    )
