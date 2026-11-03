from __future__ import annotations

from productivity_app.schemas.tasks import CreateTask, TaskQuery


def test_repository_backed_task_filters(application) -> None:
    first = application.tasks.create_task(CreateTask(title="Alpha", priority="HIGH"))
    second = application.tasks.create_task(CreateTask(title="Beta"))
    application.tasks.complete_task(second.id, second.version)
    assert {row.id for row in application.tasks.list_tasks(TaskQuery())} == {first.id, second.id}
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(priorities=("HIGH",)))] == [
        first.id
    ]
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(statuses=("COMPLETED",)))] == [
        second.id
    ]
