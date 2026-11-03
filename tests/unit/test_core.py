from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from productivity_app.core.exceptions import ValidationError
from productivity_app.core.normalization import normalize_label
from productivity_app.db.types import UTCDateTime, require_aware_utc


def test_normalize_label_applies_nfkc_whitespace_and_casefold() -> None:
    display, normalized = normalize_label("  Caf\u00e9\t\u3000Tasks  ")
    assert display == "Caf\u00e9 Tasks"
    assert normalized == "caf\u00e9 tasks"


def test_normalize_label_rejects_empty_values() -> None:
    with pytest.raises(ValidationError):
        normalize_label(" \t ")


def test_utc_datetime_round_trip_preserves_utc() -> None:
    value = datetime(2026, 9, 24, 18, 30, 5, 123456, tzinfo=UTC)
    codec = UTCDateTime()
    bound = codec.process_bind_param(value, None)  # type: ignore[arg-type]
    assert codec.process_result_value(bound, None) == value  # type: ignore[arg-type]


def test_utc_datetime_normalizes_offsets() -> None:
    value = datetime(2026, 9, 24, 18, 30, tzinfo=UTC) + timedelta(hours=2)
    assert require_aware_utc(value).tzinfo == UTC


def test_utc_datetime_rejects_naive_values() -> None:
    with pytest.raises(ValidationError):
        require_aware_utc(datetime(2026, 9, 24, 18, 30))
