"""Normalization rules used for user-owned labels."""

from __future__ import annotations

import re
import unicodedata

from productivity_app.core.exceptions import ValidationError

_WHITESPACE = re.compile(r"\s+")


def normalize_label(value: str, *, max_length: int = 120) -> tuple[str, str]:
    display = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())
    if not display:
        raise ValidationError("A label must not be empty.")
    if len(display) > max_length:
        raise ValidationError(f"A label must contain at most {max_length} characters.")
    return display, display.casefold()
