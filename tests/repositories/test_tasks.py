from __future__ import annotations

from productivity_app.schemas.folders import CreateFolder
from productivity_app.schemas.tasks import CreateTask, TaskQuery


def test_repository_backed_task_filters_distinguish_omitted_and_root_folder(application) -> None:
    root_task = application.tasks.create_task(CreateTask(title="Root"))
    folder = application.folders.create_folder(CreateFolder(name="Folder"))
    child_task = application.tasks.create_task(CreateTask(title="Child", folder_id=folder.id))
    assert {row.id for row in application.tasks.list_tasks(TaskQuery())} == {root_task.id, child_task.id}
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(folder_id=None))] == [root_task.id]
    assert [row.id for row in application.tasks.list_tasks(TaskQuery(folder_id=folder.id))] == [child_task.id]
