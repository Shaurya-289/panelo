from __future__ import annotations

from alembic.runtime.migration import MigrationContext


def test_database_is_at_the_new_design_head_revision(application) -> None:
    with application.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == "20261103_0003"
