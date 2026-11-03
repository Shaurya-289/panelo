"""Translation of known SQLite constraint races into domain errors."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from productivity_app.core.exceptions import (
    ConflictError,
    DuplicateNameError,
    NotFoundError,
    ValidationError,
)
from productivity_app.db.unit_of_work import SqlAlchemyUnitOfWork


def translate_integrity_error(exc: IntegrityError) -> None:
    message = str(exc.orig).lower() if exc.orig is not None else ""
    if "foreign key constraint failed" in message:
        raise NotFoundError("A related item is no longer available.") from exc
    if (
        "unique constraint failed: folders.normalized_name" in message
        or "unique constraint failed: folders.parent_id, folders.normalized_name" in message
    ):
        raise DuplicateNameError("An active sibling already has this name.") from exc
    if "unique constraint failed: tags.normalized_name" in message:
        raise DuplicateNameError("An active tag already has this name.") from exc
    if "unique constraint failed" in message:
        raise ConflictError("The requested change conflicts with current data.") from exc
    if "check constraint failed" in message:
        raise ValidationError("The requested change violates a domain invariant.") from exc
    raise exc


def commit_with_translation(unit_of_work: SqlAlchemyUnitOfWork) -> None:
    try:
        unit_of_work.commit()
    except IntegrityError as exc:
        translate_integrity_error(exc)
