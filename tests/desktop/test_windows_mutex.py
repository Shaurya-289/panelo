from __future__ import annotations

import sys
import uuid

import pytest

from productivity_app.windows.mutex import WindowsMutexBackend


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex integration test")
def test_real_windows_mutex_reports_existing_then_can_be_reacquired() -> None:
    backend = WindowsMutexBackend()
    identity = f"ProductivityApp-test-{uuid.uuid4().hex}"
    first = backend.acquire(identity)
    second = backend.acquire(identity)
    assert first.primary is True
    assert second.primary is False
    second.close()
    first.close()
    third = backend.acquire(identity)
    assert third.primary is True
    third.close()
