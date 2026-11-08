from __future__ import annotations

from panelo.schemas.notes import CreateNote
from panelo.schemas.search import UnifiedSearchQuery
from panelo.schemas.tasks import CreateTask
from panelo.schemas.timers import CreateTimer, CreateTimerPreset


def test_unified_search_ranks_projects_and_is_read_only(application) -> None:
    task = application.tasks.create_task(CreateTask(title="Needle", description="body"))
    note = application.notes.create_note(CreateNote(title="Other", content="needle body"))
    application.timers.create_timer(CreateTimer(name="needle timer", planned_duration_ms=1_000))
    preset = application.timers.create_preset(
        CreateTimerPreset(name="needle preset", duration_ms=1_000)
    )

    result = application.search.search(UnifiedSearchQuery(query="needle"))

    assert [(row.entity_type, row.entity_id, row.matched_field) for row in result] == [
        ("TASK", task.id, "title"),
        ("TIMER", result[1].entity_id, "name"),
        ("TIMER_PRESET", preset.id, "name"),
        ("NOTE", note.id, "content"),
    ]
    assert application.tasks.get_task(task.id).version == task.version


def test_unified_search_escapes_like_wildcards_and_deleted_visibility(application) -> None:
    literal = application.tasks.create_task(CreateTask(title="100%_\\ literal"))
    deleted = application.tasks.create_task(CreateTask(title="100%_\\ deleted"))
    application.tasks.delete_task(deleted.id, deleted.version)

    normal = application.search.search(UnifiedSearchQuery(query="%_\\"))
    all_rows = application.search.search(UnifiedSearchQuery(query="%_\\", include_deleted=True))

    assert [row.entity_id for row in normal] == [literal.id]
    assert {row.entity_id for row in all_rows} == {literal.id, deleted.id}
