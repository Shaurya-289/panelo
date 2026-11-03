import threading

import pytest
from PySide6.QtTest import QTest

from productivity_app.desktop.ui.actions import Action
from productivity_app.desktop.ui.session import DesktopUiSession, error_text


def test_session_returns_public_dto(application, qapplication):
    session = DesktopUiSession(application)
    results = []
    session.call(
        Action("settings.get_settings"),
        "settings",
        lambda value, error: results.append((value, error)),
        read=True,
    )
    for _ in range(100):
        QTest.qWait(10)
        if results:
            break
    assert results and results[0][1] is None and results[0][0].version >= 1
    session.close()


def test_selection_and_runtime_generations_discard_old_results(application, qapplication):
    session = DesktopUiSession(application)
    entered, release = threading.Event(), threading.Event()
    delivered = []

    def slow():
        entered.set()
        assert release.wait(2)
        return "old"

    session.run(slow, "selection", lambda value, error: delivered.append(value), read=True)
    assert entered.wait(2)
    session.run(lambda: "new", "selection", lambda value, error: delivered.append(value), read=True)
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if not session.runner.busy:
            break
    assert delivered == ["new"]
    entered.clear()
    release.clear()
    session.run(slow, "old-runtime", lambda value, error: delivered.append(value), read=True)
    assert entered.wait(2)
    session.rebind()
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if not session.runner.busy:
            break
    assert delivered == ["new"]
    session.close()
    with pytest.raises(RuntimeError):
        session.call(
            Action("settings.get_settings"), "closed", lambda value, error: None, read=True
        )


def test_event_bridge_delivers_gui_only_and_detaches(application, qapplication):
    from productivity_app.schemas.tasks import CreateTask

    session = DesktopUiSession(application)
    gui_thread = threading.get_ident()
    events = []
    session.changed.connect(lambda event: events.append((event, threading.get_ident())))
    session.call(
        Action("tasks.create_task", (CreateTask(title="Event task"),)),
        "mutation",
        lambda value, error: None,
    )
    for _ in range(100):
        QTest.qWait(10)
        if events and not session.runner.busy:
            break
    assert events and all(thread == gui_thread for event, thread in events)
    session.close()
    before = len(events)
    application.tasks.create_task(CreateTask(title="After detach"))
    QTest.qWait(20)
    assert len(events) == before and not session.subscriptions


def test_unexpected_error_logging_does_not_leak_note_contents(caplog):
    import logging

    private = "Sensitive whole-note contents must not be logged"
    logger = logging.getLogger("productivity_app.desktop.ui.session")
    logger.addHandler(caplog.handler)
    try:
        try:
            raise KeyError(private)
        except KeyError as error:
            with caplog.at_level(logging.ERROR, logger=logger.name):
                text = error_text(error)
    finally:
        logger.removeHandler(caplog.handler)
    assert private not in text and private not in caplog.text
    assert "KeyError" in caplog.text and "UI operation failed" in caplog.text


def test_result_detects_facade_runtime_swap_before_any_new_request(qapplication):
    from types import SimpleNamespace

    facade = SimpleNamespace(search=object())
    session = DesktopUiSession(facade)
    entered, release = threading.Event(), threading.Event()
    delivered = []

    def old_runtime():
        entered.set()
        assert release.wait(5)
        return "Old DTO"

    session.run(old_runtime, "selection", lambda value, error: delivered.append(value), read=True)
    assert entered.wait(5)
    facade.search = object()
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if not session.runner.busy:
            break
    assert delivered == [] and session.state.runtime_generation == 1
    session.close()
