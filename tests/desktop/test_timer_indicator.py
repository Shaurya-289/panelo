from types import SimpleNamespace

from productivity_app.desktop.ui.timer_display import TimerDisplay
from tests.desktop.test_final_ui_integration import settle


def test_fake_clock_integer_countdown_pause_and_zero(qapplication):
    now = [1000000000]
    display = TimerDisplay(now_ns=lambda: now[0])
    display.update(
        SimpleNamespace(name="Timer", state="RUNNING", effective_state="RUNNING", remaining_ms=2001)
    )
    assert display.remaining() == 2001
    now[0] += 1_999_999
    assert display.remaining() == 2000
    now[0] += 2_000_000_000
    assert display.remaining() == 0 and "Awaiting recording" in display.text()
    display.update(
        SimpleNamespace(name="Timer", state="PAUSED", effective_state="PAUSED", remaining_ms=1111)
    )
    now[0] += 900000000000
    assert display.remaining() == 1111 and "Paused" in display.text()
    display.set_visible(True)
    assert not display.tick.isActive()
    display.deleteLater()


def test_display_ticks_never_call_services_and_hidden_stops(final_ui, monkeypatch):
    calls = []
    monkeypatch.setattr(final_ui.session, "call", lambda *args, **kwargs: calls.append(args))
    final_ui.display.update(
        SimpleNamespace(
            name="Timer", state="RUNNING", effective_state="RUNNING", remaining_ms=60000
        )
    )
    for _ in range(10):
        final_ui.display.repaint()
    assert calls == []
    final_ui.display.set_visible(False)
    assert not final_ui.display.tick.isActive()


def test_indicator_uses_viewport_visibility_not_focus(final_ui):
    final_ui.window.show()
    settle(final_ui)
    final_ui.display.update(
        SimpleNamespace(name="Timer", state="PAUSED", effective_state="PAUSED", remaining_ms=60000)
    )
    final_ui.reveal("notes")
    final_ui.presentation_changed()
    assert final_ui.indicator.isVisible()
    final_ui.reveal("timers")
    final_ui.presentation_changed()
    assert not final_ui.indicator.isVisible()
    final_ui.window.hide()
    assert not final_ui.display.tick.isActive()
