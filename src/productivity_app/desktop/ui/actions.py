"""Allowlisted facade calls shared by widgets and application commands."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

METHODS: dict[str, tuple[str, str]] = {}
_DOMAINS = {
    "notes": "list_notes get_note create_note update_note move_note set_note_tags pin_note unpin_note archive_note restore_from_archive delete_note restore_note search_notes",
    "tasks": "list_tasks get_task create_task update_task start_task return_task_to_todo complete_task cancel_task reopen_task move_task set_task_tags delete_task restore_task list_today list_next_seven_days list_upcoming list_overdue list_completed list_important search_tasks",
    "folders": "list_children get_folder get_tree create_folder rename_folder move_folder reorder_folder delete_folder restore_folder",
    "tags": "list_tags get_tag create_tag rename_tag delete_tag restore_tag",
    "timers": "get_active_timer list_timers get_timer create_timer update_timer start_timer pause_timer resume_timer complete_timer cancel_timer reset_timer delete_timer restore_timer reconcile_expired_timer list_timer_runs list_presets get_preset create_preset update_preset move_preset delete_preset restore_preset",
    "focus": "create_session create_sequence get_session get_sequence list_sessions list_sequences advance_sequence cancel_sequence get_stats",
    "reminders": "list_reminders get_reminder create_reminder update_reminder snooze_reminder complete_reminder dismiss_reminder cancel_reminder reopen_reminder delete_reminder restore_reminder list_upcoming list_due list_overdue list_history list_reminder_events",
    "search": "search search_full_text",
    "settings": "get_settings update_settings",
    "backups": "create_backup list_backups validate_backup delete_backup",
}
for _domain, _names in _DOMAINS.items():
    for _name in _names.split():
        METHODS[f"{_domain}.{_name}"] = (_domain, _name)


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """Commands are copied as immutable data, not shared mutable Pydantic inputs."""

    schema: type[BaseModel]
    fields: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class FrozenMapping:
    fields: tuple[tuple[str, Any], ...]


def _freeze(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return SchemaSnapshot(
            type(value),
            tuple(
                (key, _freeze(item)) for key, item in value.model_dump(exclude_unset=True).items()
            ),
        )
    if isinstance(value, dict):
        return FrozenMapping(tuple((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _materialize(value: Any) -> Any:
    if isinstance(value, SchemaSnapshot):
        return value.schema.model_validate({key: _materialize(item) for key, item in value.fields})
    if isinstance(value, FrozenMapping):
        return {key: _materialize(item) for key, item in value.fields}
    if isinstance(value, tuple):
        return tuple(_materialize(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class Action:
    name: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(_freeze(value) for value in self.args))
        object.__setattr__(
            self, "kwargs", tuple((key, _freeze(value)) for key, value in self.kwargs)
        )


def dispatch(application: Any, action: Action) -> Any:
    if action.name not in METHODS:
        raise ValueError("Unsupported application action")
    service, method = METHODS[action.name]
    return getattr(getattr(application, service), method)(
        *(_materialize(value) for value in action.args),
        **{key: _materialize(value) for key, value in action.kwargs},
    )


@dataclass(frozen=True, slots=True)
class PartialSave:
    view: Any
    completed: tuple[str, ...]
    error: Exception | None = None


def save_relationships(
    application: Any,
    domain: str,
    view: Any,
    folder: str | None,
    tags: tuple[str, ...],
    *,
    text_saved: bool = True,
) -> PartialSave:
    """Public commands commit independently; no pretend compound rollback."""
    done = ["text"] if text_saved else []
    try:
        singular = "note" if domain == "notes" else "task"
        if folder != view.folder_id:
            view = dispatch(
                application, Action(f"{domain}.move_{singular}", (view.id, folder, view.version))
            )
            done.append("folder")
        if set(tags) != set(view.tag_ids):
            view = dispatch(
                application, Action(f"{domain}.set_{singular}_tags", (view.id, tags, view.version))
            )
            done.append("tags")
    except Exception as exc:
        return PartialSave(view, tuple(done), exc)
    return PartialSave(view, tuple(done))
