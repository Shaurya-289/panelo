import threading

import pytest
from PySide6.QtTest import QTest

from productivity_app.desktop.ui.operations import ServiceOperationRunner


def test_runner_executes_off_gui_and_delivers_on_gui(qapplication):
    runner = ServiceOperationRunner()
    owner = threading.get_ident()
    results = []
    runner.completed.connect(
        lambda request, value, error: results.append((value, error, threading.get_ident()))
    )
    runner.submit("read", 1, 0, True, threading.get_ident)
    for _ in range(100):
        QTest.qWait(10)
        if results:
            break
    assert results and results[0][0] != owner and results[0][2] == owner
    assert runner.pool.maxThreadCount() == 1


def test_bounded_queue_coalesces_reads_and_quiescing_keeps_mutations(qapplication):
    runner = ServiceOperationRunner(capacity=2)
    entered, release = threading.Event(), threading.Event()
    results, executed = [], []
    runner.completed.connect(lambda request, value, error: results.append((request.key, error)))

    def blocked():
        entered.set()
        assert release.wait(2)

    runner.submit("active", 1, 0, False, blocked)
    assert entered.wait(2)
    runner.submit("read", 1, 0, True, lambda: executed.append("old"))
    runner.submit("read", 2, 0, True, lambda: executed.append("new"))
    assert len(runner.queue) == 1
    runner.submit("mutation", 1, 0, False, lambda: executed.append("mutation"))
    with pytest.raises(RuntimeError):
        runner.submit("overflow", 1, 0, False, lambda: None)
    runner.quiesce()
    with pytest.raises(RuntimeError):
        runner.submit("closed", 1, 0, False, lambda: None)
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if not runner.busy:
            break
    assert executed == ["mutation"] and not runner.busy
    assert sum(error is not None for key, error in results) == 2
