from __future__ import annotations

from panelo.schemas.timers import CreateTimer, TimerQuery


def test_timer_listing_is_deterministic(application) -> None:
    one = application.timers.create_timer(CreateTimer(name="A", planned_duration_ms=1_000))
    two = application.timers.create_timer(CreateTimer(name="B", planned_duration_ms=1_000))
    assert [item.id for item in application.timers.list_timers(TimerQuery(sort="name"))] == [
        one.id,
        two.id,
    ]
