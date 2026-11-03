"""The activation endpoint a second launch talks to: busy while starting, bounded when stuck."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtNetwork import QLocalSocket

from productivity_app.desktop.instance import ActivationResponse, ActivationServer


def _activation_request(server: ActivationServer) -> bytes:
    socket, loop, timeout = QLocalSocket(), QEventLoop(), QTimer()
    reply: list[bytes] = []
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    socket.connected.connect(lambda: socket.write(b"ACTIVATE 1\n"))
    socket.readyRead.connect(lambda: reply.append(bytes(socket.readAll().data())) or loop.quit())
    socket.disconnected.connect(loop.quit)
    socket.connectToServer(server.server_name)
    timeout.start(1_000)
    loop.exec()
    timeout.stop()
    socket.abort()
    return b"".join(reply)


def test_delayed_primary_endpoint_returns_busy_then_exactly_one_ok(qapplication: Any) -> None:
    ready = False
    calls: list[ActivationResponse] = []

    def activate() -> ActivationResponse:
        calls.append(ActivationResponse.OK if ready else ActivationResponse.BUSY)
        return calls[-1]

    server = ActivationServer(f"pass3-busy-{uuid4().hex}", activate)
    server.start()
    try:
        assert _activation_request(server) == b"BUSY\n"
        ready = True
        assert _activation_request(server) == b"OK\n"
    finally:
        server.close()
    assert calls == [ActivationResponse.BUSY, ActivationResponse.OK]


def test_activation_server_close_releases_incomplete_pending_socket(qapplication: Any) -> None:
    server = ActivationServer(f"pass3-pending-{uuid4().hex}", lambda: ActivationResponse.OK)
    socket = QLocalSocket()
    server.start()
    try:
        socket.connectToServer(server.server_name)
        socket.waitForConnected(1_000)
        socket.write(b"ACTI")
        socket.flush()
        loop, timeout, observed = QEventLoop(), QTimer(), QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        observed.timeout.connect(lambda: loop.quit() if server._buffers else None)
        timeout.start(1_000)
        observed.start(1)
        loop.exec()
        observed.stop()
        timeout.stop()
        assert server._buffers
        server.close()
        assert not server._buffers
    finally:
        socket.abort()
        server.close()
