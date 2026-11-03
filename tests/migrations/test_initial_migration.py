from __future__ import annotations

from alembic.runtime.migration import MigrationContext


def test_database_is_at_phase7_head_revision(application) -> None:
    with application.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == "20261002_0002"
