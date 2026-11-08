"""Domain-event subscriptions must follow the live runtime across a restore."""

from __future__ import annotations

from typing import Any

from panelo.core.events import DomainEventType
from panelo.schemas.backups import RestoreBackup
from panelo.schemas.tasks import CreateTask


def test_runtime_event_subscriptions_are_isolated_across_real_restore(application: Any) -> None:
    old_events: list[str] = []
    old_subscription = application.subscribe(
        DomainEventType.TASK_CREATED, lambda event: old_events.append(event.entity_id)
    )
    before = application.tasks.create_task(CreateTask(title="before restore"))
    assert old_events == [before.id]
    backup = application.backups.create_backup()
    old_runtime, old_bus = application._active_runtime(), application._event_bus
    application.restore_backup(
        RestoreBackup(source_path=application.backups.backup_directory / backup.filename)
    )
    assert application._active_runtime() is not old_runtime
    current_events: list[str] = []
    current_subscription = application.subscribe(
        DomainEventType.TASK_CREATED, lambda event: current_events.append(event.entity_id)
    )
    after = application.tasks.create_task(CreateTask(title="after restore"))
    assert current_events == [after.id]
    assert old_events == [before.id]
    assert old_bus is not application._event_bus
    old_subscription.close()
    current_subscription.close()
