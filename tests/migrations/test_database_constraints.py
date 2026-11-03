"""Direct-SQL constraint tests against a database created only by Alembic."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from productivity_app.bootstrap import _alembic_config
from productivity_app.db.engine import create_sqlite_engine, sqlite_url


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Iterator[Engine]:
    path = tmp_path / "constraints.db"
    command.upgrade(_alembic_config(sqlite_url(path), 5_000), "head")
    engine = create_sqlite_engine(path)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(connection: object) -> None:
    execute = connection.execute  # type: ignore[attr-defined]
    execute(text("INSERT INTO folders VALUES ('folder', 'Folder', 'folder', NULL, 0, NULL, 1, 1, 1)"))
    execute(text("INSERT INTO tags VALUES ('tag', 'Tag', 'tag', NULL, 1, 1, 1)"))
    execute(text("INSERT INTO notes VALUES ('note', 'Note', '', 'PLAIN_TEXT', 'folder', 0, 0, NULL, 1, 1, 1)"))
    execute(text("INSERT INTO tasks VALUES ('task', 'Task', '', 'TODO', 'NORMAL', 'folder', NULL, NULL, NULL, NULL, NULL, 1, 1, 1)"))
    execute(text("INSERT INTO timer_presets VALUES ('preset', 'Preset', 'preset', 1000, 0, 0, NULL, 1, 1, 1)"))
    execute(text("INSERT INTO timers VALUES ('timer', 'Timer', 'preset', 'task', 'READY', 1000, 0, NULL, NULL, NULL, NULL, NULL, 1, 1, 1)"))
    execute(text("INSERT INTO reminders VALUES ('reminder', 'Reminder', '', 1, 'UTC', 'PENDING', 'task', NULL, NULL, NULL, NULL, NULL, 0, NULL, 1, 1, 1)"))


_INVALID_WRITES = [
    ("folder duplicate root position", "INSERT INTO folders VALUES ('f2','Other','other',NULL,0,NULL,1,1,1)"),
    ("folder duplicate sibling position", "INSERT INTO folders VALUES ('child','Child','child','folder',0,NULL,1,1,1); INSERT INTO folders VALUES ('child2','Child2','child2','folder',0,NULL,1,1,1)"),
    ("folder self parent", "INSERT INTO folders VALUES ('self','Self','self','self',1,NULL,1,1,1)"),
    ("preset duplicate name", "INSERT INTO timer_presets VALUES ('p2','P2','preset',2000,1,0,NULL,1,1,1)"),
    ("preset duplicate order", "INSERT INTO timer_presets VALUES ('p2','P2','p2',2000,0,0,NULL,1,1,1)"),
    ("preset non-positive duration", "INSERT INTO timer_presets VALUES ('p2','P2','p2',0,1,0,NULL,1,1,1)"),
    ("task invalid status", "INSERT INTO tasks VALUES ('t2','T','', 'BAD','NORMAL',NULL,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("task invalid priority", "INSERT INTO tasks VALUES ('t2','T','', 'TODO','BAD',NULL,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("task start after due", "INSERT INTO tasks VALUES ('t2','T','', 'TODO','NORMAL',NULL,2,1,NULL,NULL,NULL,1,1,1)"),
    ("task completed missing timestamp", "INSERT INTO tasks VALUES ('t2','T','', 'COMPLETED','NORMAL',NULL,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("task cancelled has completed timestamp", "INSERT INTO tasks VALUES ('t2','T','', 'CANCELLED','NORMAL',NULL,NULL,NULL,1,2,NULL,1,1,1)"),
    ("reminder invalid status", "INSERT INTO reminders VALUES ('r2','R','',1,'UTC','BAD',NULL,NULL,NULL,NULL,NULL,NULL,0,NULL,1,1,1)"),
    ("reminder dual links", "INSERT INTO reminders VALUES ('r2','R','',1,'UTC','PENDING','task','note',NULL,NULL,NULL,NULL,0,NULL,1,1,1)"),
    ("reminder terminal mismatch", "INSERT INTO reminders VALUES ('r2','R','',1,'UTC','DISMISSED',NULL,NULL,NULL,NULL,NULL,NULL,0,NULL,1,1,1)"),
    ("reminder negative snooze", "INSERT INTO reminders VALUES ('r2','R','',1,'UTC','PENDING',NULL,NULL,NULL,NULL,NULL,NULL,-1,NULL,1,1,1)"),
    ("timer invalid state", "INSERT INTO timers VALUES ('x','X',NULL,NULL,'BAD',1000,0,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("timer running fields", "INSERT INTO timers VALUES ('x','X',NULL,NULL,'RUNNING',1000,0,NULL,NULL,1,NULL,NULL,1,1,1)"),
    ("timer paused fields", "INSERT INTO timers VALUES ('x','X',NULL,NULL,'PAUSED',1000,0,1,NULL,1,NULL,NULL,1,1,1)"),
    ("timer ready fields", "INSERT INTO timers VALUES ('x','X',NULL,NULL,'READY',1000,1,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("timer terminal fields", "INSERT INTO timers VALUES ('x','X',NULL,NULL,'FINISHED',1000,0,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("second active timer", "INSERT INTO timers VALUES ('active1','A',NULL,NULL,'PAUSED',1000,0,NULL,NULL,1,NULL,NULL,1,1,1); INSERT INTO timers VALUES ('active2','B',NULL,NULL,'RUNNING',1000,0,1,2,1,NULL,NULL,1,1,1)"),
    ("timer run invalid outcome", "INSERT INTO timer_runs VALUES ('run','timer',NULL,1000,0,1,2,'BAD')"),
    ("duplicate open timer run", "INSERT INTO timer_runs VALUES ('run1','timer',NULL,1000,0,1,NULL,NULL); INSERT INTO timer_runs VALUES ('run2','timer',NULL,1000,0,2,NULL,NULL)"),
    ("settings theme", "INSERT INTO app_settings VALUES (1,'BAD','',0,0,0,1000,1000,1000,1000,1,1)"),
    ("settings version", "INSERT INTO app_settings VALUES (1,'SYSTEM','',0,0,0,1000,1000,1000,1000,1,0)"),
    ("settings boolean", "INSERT INTO app_settings VALUES (1,'SYSTEM','',2,0,0,1000,1000,1000,1000,1,1)"),
    ("settings duration", "INSERT INTO app_settings VALUES (1,'SYSTEM','',0,0,0,999,1000,1000,1000,1,1)"),
    ("notification status", "INSERT INTO notification_deliveries VALUES ('n','k','a','a','d','t','b','BAD',0,1,NULL,NULL,NULL,NULL,1,NULL)"),
    ("notification attempts", "INSERT INTO notification_deliveries VALUES ('n','k','a','a','d','t','b','PENDING',-1,1,NULL,NULL,NULL,NULL,1,NULL)"),
    ("notification dedupe", "INSERT INTO notification_deliveries VALUES ('n1','k','a','a','d','t','b','PENDING',0,1,NULL,NULL,NULL,NULL,1,NULL); INSERT INTO notification_deliveries VALUES ('n2','k','a','a','d','t','b','PENDING',0,1,NULL,NULL,NULL,NULL,1,NULL)"),
    ("folder foreign key", "INSERT INTO folders VALUES ('f','F','f','missing',1,NULL,1,1,1)"),
    ("note foreign key", "INSERT INTO notes VALUES ('n','N','','PLAIN_TEXT','missing',0,0,NULL,1,1,1)"),
    ("task foreign key", "INSERT INTO tasks VALUES ('t','T','','TODO','NORMAL','missing',NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("note tag foreign key", "INSERT INTO note_tags VALUES ('missing','tag')"),
    ("task tag foreign key", "INSERT INTO task_tags VALUES ('missing','tag')"),
    ("reminder task foreign key", "INSERT INTO reminders VALUES ('r','R','',1,'UTC','PENDING','missing',NULL,NULL,NULL,NULL,NULL,0,NULL,1,1,1)"),
    ("reminder event foreign key", "INSERT INTO reminder_events VALUES ('e','missing','x',1,NULL,NULL,NULL,NULL,NULL)"),
    ("timer preset foreign key", "INSERT INTO timers VALUES ('x','X','missing',NULL,'READY',1000,0,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("timer task foreign key", "INSERT INTO timers VALUES ('x','X',NULL,'missing','READY',1000,0,NULL,NULL,NULL,NULL,NULL,1,1,1)"),
    ("timer run timer foreign key", "INSERT INTO timer_runs VALUES ('r','missing',NULL,1000,0,1,NULL,NULL)"),
    ("timer run task foreign key", "INSERT INTO timer_runs VALUES ('r','timer','missing',1000,0,1,NULL,NULL)"),
]


@pytest.mark.parametrize(("_name", "statements"), _INVALID_WRITES)
def test_migration_schema_rejects_invalid_direct_writes(
    migrated_engine: Engine, _name: str, statements: str
) -> None:
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _seed(connection)
            with pytest.raises(IntegrityError):
                for statement in statements.split("; "):
                    connection.execute(text(statement))
        finally:
            transaction.rollback()
