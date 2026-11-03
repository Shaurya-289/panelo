from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from productivity_app.repositories.search import SearchRepository
from productivity_app.schemas.folders import CreateFolder, RenameFolder
from productivity_app.schemas.notes import CreateNote, UpdateNote
from productivity_app.schemas.reminders import CreateReminder, UpdateReminder
from productivity_app.schemas.search import FullTextSearchQuery
from productivity_app.schemas.tags import CreateTag, RenameTag
from productivity_app.schemas.tasks import CreateTask, UpdateTask
from productivity_app.schemas.timers import (
    CreateTimer,
    CreateTimerPreset,
    UpdateTimer,
    UpdateTimerPreset,
)


def _create_all(application, prefix: str = "Matrix"):
    folder = application.folders.create_folder(CreateFolder(name=f"{prefix} Folder"))
    tag = application.tags.create_tag(CreateTag(name=f"{prefix} Tag"))
    task = application.tasks.create_task(CreateTask(title=f"{prefix} Task", description=f"{prefix} task body"))
    note = application.notes.create_note(CreateNote(title=f"{prefix} Note", content=f"{prefix} note body"))
    reminder = application.reminders.create_reminder(CreateReminder(
        title=f"{prefix} Reminder", message=f"{prefix} reminder body",
        due_at_utc=application._clock.now, scheduled_timezone="UTC",
    ))
    timer = application.timers.create_timer(CreateTimer(name=f"{prefix} Timer", planned_duration_ms=60_000))
    preset = application.timers.create_preset(CreateTimerPreset(name=f"{prefix} Preset", duration_ms=60_000))
    return folder, tag, task, note, reminder, timer, preset


def test_full_text_search_finds_timer_and_preserves_legacy_search(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Write quarterly report", planned_duration_ms=60_000)
    )

    full_text = application.search.search_full_text(FullTextSearchQuery(text="quarterly"))
    legacy = application.search.search_full_text(
        FullTextSearchQuery(text="write quart", mode="PREFIX")
    )

    assert [item.entity_id for item in full_text] == [timer.id]
    assert full_text[0].matched_field == "name"
    assert [item.entity_id for item in legacy] == [timer.id]


def test_full_text_query_treats_operators_as_literal_text(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="OR NOT NEAR (draft)", planned_duration_ms=60_000)
    )

    results = application.search.search_full_text(
        FullTextSearchQuery(text="OR NOT", mode="TERMS")
    )

    assert [item.entity_id for item in results] == [timer.id]


def test_full_text_soft_deleted_rows_follow_visibility_filter(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Archived discovery", planned_duration_ms=60_000)
    )
    application.timers.delete_timer(timer.id, timer.version)

    assert application.search.search_full_text(FullTextSearchQuery(text="discovery")) == ()
    included = application.search.search_full_text(
        FullTextSearchQuery(text="discovery", include_deleted=True)
    )
    assert [item.entity_id for item in included] == [timer.id]
    assert included[0].is_deleted


def test_rebuild_repairs_derived_index(application) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Rebuild target", planned_duration_ms=60_000)
    )
    with application.unit_of_work_factory() as uow:
        uow.acquire_write_lock()
        assert uow.session is not None
        uow.session.execute(
            text("DELETE FROM search_documents WHERE entity_type='TIMER' AND entity_id=:id"),
            {"id": timer.id},
        )
        uow.commit()

    assert application.search.search_full_text(FullTextSearchQuery(text="rebuild")) == ()
    application.search.rebuild_full_text_index()
    assert [item.entity_id for item in application.search.search_full_text(FullTextSearchQuery(text="rebuild"))] == [timer.id]


def test_all_seven_service_writes_are_synchronized_by_database_triggers(application) -> None:
    folder, tag, task, note, reminder, timer, preset = _create_all(application, "FTS")
    expected = {
        "FOLDER": (folder.id, "FTS Folder", ""),
        "TAG": (tag.id, "FTS Tag", ""),
        "TASK": (task.id, "FTS Task", "FTS task body"),
        "NOTE": (note.id, "FTS Note", "FTS note body"),
        "REMINDER": (reminder.id, "FTS Reminder", "FTS reminder body"),
        "TIMER": (timer.id, "FTS Timer", ""),
        "TIMER_PRESET": (preset.id, "FTS Preset", ""),
    }
    with application.engine.connect() as connection:
        documents = {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                text("SELECT entity_type, entity_id, title, body FROM search_documents WHERE title LIKE 'FTS %'")
            )
        }
    assert documents == expected
    result_types = {item.entity_type for item in application.search.search_full_text(FullTextSearchQuery(text="fts"))}
    assert result_types == set(expected)


def test_full_text_terms_prefix_phrase_and_hostile_text_are_safe(application) -> None:
    both = application.timers.create_timer(CreateTimer(name="alpha beta", planned_duration_ms=60_000))
    alpha_only = application.timers.create_timer(CreateTimer(name="alpha only", planned_duration_ms=60_000))
    assert [item.entity_id for item in application.search.search_full_text(FullTextSearchQuery(text="alpha beta", mode="TERMS"))] == [both.id]
    assert {item.entity_id for item in application.search.search_full_text(FullTextSearchQuery(text="alp", mode="PREFIX"))} == {both.id, alpha_only.id}
    assert [item.entity_id for item in application.search.search_full_text(FullTextSearchQuery(text="alpha beta", mode="PHRASE"))] == [both.id]
    for hostile in ("OR", "NOT", "NEAR", "*", "-", "()", '"""', "title:alpha", "😀", "alpha OR beta"):
        application.search.search_full_text(FullTextSearchQuery(text=hostile, mode="TERMS"))


def test_all_seven_service_updates_refresh_documents_and_fts(application) -> None:
    folder, tag, task, note, reminder, timer, preset = _create_all(application, "Oldtoken")
    application.folders.rename_folder(folder.id, RenameFolder(name="Newtoken Folder", expected_version=folder.version))
    application.tags.rename_tag(tag.id, RenameTag(name="Newtoken Tag", expected_version=tag.version))
    application.tasks.update_task(task.id, UpdateTask(expected_version=task.version, title="Newtoken Task", description="Newtoken task body"))
    application.notes.update_note(note.id, UpdateNote(expected_version=note.version, title="Newtoken Note", content="Newtoken note body"))
    application.reminders.update_reminder(reminder.id, UpdateReminder(expected_version=reminder.version, title="Newtoken Reminder", message="Newtoken reminder body"))
    application.timers.update_timer(timer.id, UpdateTimer(expected_version=timer.version, name="Newtoken Timer"))
    application.timers.update_preset(preset.id, UpdateTimerPreset(expected_version=preset.version, name="Newtoken Preset"))

    assert application.search.search_full_text(FullTextSearchQuery(text="oldtoken")) == ()
    results = application.search.search_full_text(FullTextSearchQuery(text="newtoken"))
    assert {item.entity_type for item in results} == {"TASK", "NOTE", "REMINDER", "TIMER", "FOLDER", "TAG", "TIMER_PRESET"}
    with application.engine.connect() as connection:
        rows = connection.execute(text("SELECT entity_type,title,body FROM search_documents WHERE title LIKE 'Newtoken %'" )).all()
    assert len(rows) == 7
    assert {row[0] for row in rows} == {"TASK", "NOTE", "REMINDER", "TIMER", "FOLDER", "TAG", "TIMER_PRESET"}


def test_all_seven_hard_delete_triggers_remove_documents_and_matches(application) -> None:
    entities = _create_all(application, "Harddelete")
    table_names = ("folders", "tags", "tasks", "notes", "reminders", "timers", "timer_presets")
    with application.engine.begin() as connection:
        for table_name, entity in zip(table_names, entities, strict=True):
            connection.execute(text(f"DELETE FROM {table_name} WHERE id=:id"), {"id": entity.id})
    with application.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM search_documents WHERE title LIKE 'Harddelete %'")) == 0
    assert application.search.search_full_text(FullTextSearchQuery(text="harddelete", include_deleted=True)) == ()


@pytest.mark.parametrize("mode", ["TERMS", "PREFIX", "PHRASE"])
@pytest.mark.parametrize("value", [
    "OR", "NOT", "NEAR", "*", "-", "+", ":", "^", "()", "((nested))", '"quoted"',
    '"""', "can't", "slash/value", r"back\\slash", "—", "😀", "!!!", "title:alpha",
])
def test_hostile_full_text_input_never_escapes_application_syntax(application, mode, value) -> None:
    application.search.search_full_text(FullTextSearchQuery(text=value, mode=mode))


def test_rebuild_repairs_orphan_and_stale_documents_and_is_idempotent(application) -> None:
    timer = application.timers.create_timer(CreateTimer(name="Authoritative rebuild", planned_duration_ms=60_000))
    with application.engine.begin() as connection:
        connection.execute(text("UPDATE search_documents SET title='Stale value' WHERE entity_type='TIMER' AND entity_id=:id"), {"id": timer.id})
        connection.execute(text("INSERT INTO search_documents(entity_type,entity_id,title,body) VALUES('TIMER','orphan','Phantom orphan','')"))
    application.search.rebuild_full_text_index()
    with application.engine.connect() as connection:
        first = connection.execute(text("SELECT entity_type,entity_id,title,body FROM search_documents ORDER BY entity_type,entity_id")).all()
        assert connection.scalar(text("SELECT title FROM search_documents WHERE entity_type='TIMER' AND entity_id=:id"), {"id": timer.id}) == "Authoritative rebuild"
        assert connection.scalar(text("SELECT count(*) FROM search_documents WHERE entity_id='orphan'")) == 0
    application.search.rebuild_full_text_index()
    with application.engine.connect() as connection:
        second = connection.execute(text("SELECT entity_type,entity_id,title,body FROM search_documents ORDER BY entity_type,entity_id")).all()
    assert first == second
    assert [item.entity_id for item in application.search.search_full_text(FullTextSearchQuery(text="authoritative"))] == [timer.id]


def test_full_text_search_has_no_domain_or_recovery_side_effects(
    application, monkeypatch
) -> None:
    timer = application.timers.create_timer(
        CreateTimer(name="Side effect sentinel", planned_duration_ms=60_000)
    )
    tables = ("reminders", "timers", "focus_sequences", "focus_sessions", "timer_runs")

    def snapshot():
        with application.engine.connect() as connection:
            return {
                table_name: connection.execute(
                    text(f"SELECT * FROM {table_name} ORDER BY 1")
                ).all()
                for table_name in tables
            }

    before = snapshot()

    def unexpected_event(_event) -> list[Exception]:
        raise AssertionError("search published a domain event")

    monkeypatch.setattr(application._event_bus, "publish", unexpected_event)
    assert application.search.search_full_text(
        FullTextSearchQuery(text="side effect")
    )[0].entity_id == timer.id

    assert snapshot() == before


@pytest.mark.parametrize(
    "method_name,invoke",
    [
        (
            "search_full_text",
            lambda application: application.search.search_full_text(
                FullTextSearchQuery(text="unexpected")
            ),
        ),
        ("rebuild_full_text_index", lambda application: application.search.rebuild_full_text_index()),
        ("verify_full_text_index", lambda application: application.search.rebuild_full_text_index()),
    ],
)
def test_unexpected_full_text_database_errors_are_not_hidden(
    application, monkeypatch, method_name, invoke
) -> None:
    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError(f"unexpected {method_name} failure")

    monkeypatch.setattr(SearchRepository, method_name, fail)

    with pytest.raises(sqlite3.OperationalError, match=f"unexpected {method_name} failure"):
        invoke(application)
