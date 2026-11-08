"""Bounded local activation protocol for the mutex-owning desktop process."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from time import monotonic

from PySide6.QtCore import QEventLoop, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from panelo.desktop.errors import SingleInstanceError

_REQUEST = b"ACTIVATE 1\n"
_RESPONSE_OK = b"OK\n"
_RESPONSE_BUSY = b"BUSY\n"
_RESPONSE_ERROR = b"ERROR\n"
_MAX_REQUEST_BYTES = 64


class ActivationResponse(StrEnum):
    """The only replies understood by the local activation client."""

    OK = "OK"
    BUSY = "BUSY"
    ERROR = "ERROR"


def activation_server_name(identity: str) -> str:
    return f"{identity}-activate"


def parse_activation_request(payload: bytes) -> bool:
    """Accept exactly the one bounded ASCII activation command."""
    if len(payload) > _MAX_REQUEST_BYTES:
        return False
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return False
    return text == "ACTIVATE 1\n"


class ActivationServer(QObject):
    """A same-user local IPC endpoint; the mutex remains ownership authority."""

    def __init__(self, server_name: str, on_activate: Callable[[], ActivationResponse]) -> None:
        super().__init__()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept_connections)
        self._on_activate = on_activate
        self._closed = False
        self._server_name = server_name
        self._buffers: dict[int, bytearray] = {}
        self._sockets: dict[int, QLocalSocket] = {}
        self._terminal_connections: set[int] = set()

    @property
    def is_listening(self) -> bool:
        return self._server.isListening()

    @property
    def server_name(self) -> str:
        return self._server_name

    def start(self) -> None:
        if not self._server.listen(self._server_name):
            raise SingleInstanceError(
                f"Could not start local activation endpoint: {self._server.errorString()}"
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.close()
        # Accepted sockets are process-owned until they disconnect.  Close
        # incomplete clients explicitly so shutdown cannot retain buffered
        # requests or QObject references after the listening endpoint stops.
        for socket in tuple(self._sockets.values()):
            # Cancel incomplete Windows pipe reads once. Starting a graceful
            # disconnect immediately before abort/delete can leave a queued
            # native completion referring to a retired socket. No further
            # activation callback is meaningful once ownership is closing.
            socket.readyRead.disconnect()
            socket.disconnected.disconnect()
            socket.abort()
            socket.deleteLater()
        self._sockets.clear()
        self._buffers.clear()
        self._terminal_connections.clear()

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            key = id(socket)
            self._buffers[key] = bytearray()
            self._sockets[key] = socket
            socket.readyRead.connect(lambda socket=socket: self._read(socket))
            socket.disconnected.connect(lambda socket=socket: self._discard(socket))

    def _read(self, socket: QLocalSocket) -> None:
        key = id(socket)
        if key in self._terminal_connections:
            return
        buffer = self._buffers.setdefault(key, bytearray())
        buffer.extend(bytes(socket.readAll().data()))
        if len(buffer) > _MAX_REQUEST_BYTES:
            self._terminal_reply(socket, _RESPONSE_ERROR)
            return
        if b"\n" not in buffer:
            return
        payload = bytes(buffer)
        if not parse_activation_request(payload):
            self._terminal_reply(socket, _RESPONSE_ERROR)
            return
        response = self._on_activate()
        response_bytes = {
            ActivationResponse.OK: _RESPONSE_OK,
            ActivationResponse.BUSY: _RESPONSE_BUSY,
            ActivationResponse.ERROR: _RESPONSE_ERROR,
        }[response]
        self._terminal_reply(socket, response_bytes)

    def _terminal_reply(self, socket: QLocalSocket, response: bytes) -> None:
        key = id(socket)
        if key in self._terminal_connections:
            return
        self._terminal_connections.add(key)
        QTimer.singleShot(0, lambda socket=socket, response=response: self._reply(socket, response))

    def _reply(self, socket: QLocalSocket, response: bytes) -> None:
        if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            socket.write(response)
            socket.flush()
            socket.disconnectFromServer()

    def _discard(self, socket: QLocalSocket) -> None:
        key = id(socket)
        self._buffers.pop(key, None)
        self._sockets.pop(key, None)
        self._terminal_connections.discard(key)
        socket.deleteLater()


def request_activation(
    server_name: str,
    *,
    startup_timeout_ms: int = 10_000,
    retry_interval_ms: int = 50,
    attempt_timeout_ms: int = 250,
    monotonic_clock: Callable[[], float] = monotonic,
) -> None:
    """Wait a bounded startup interval for a ready primary activation endpoint."""
    deadline = monotonic_clock() + startup_timeout_ms / 1_000
    while monotonic_clock() < deadline:
        socket = QLocalSocket()
        loop = QEventLoop()
        response: list[bytes] = []
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)

        def write_request(socket: QLocalSocket = socket) -> None:
            socket.write(_REQUEST)

        def read_response(
            socket: QLocalSocket = socket,
            response: list[bytes] = response,
            loop: QEventLoop = loop,
        ) -> None:
            response.append(bytes(socket.readAll().data()))
            loop.quit()

        socket.connected.connect(write_request)
        socket.readyRead.connect(read_response)
        socket.disconnected.connect(loop.quit)
        socket.connectToServer(server_name)
        remaining_ms = max(1, int((deadline - monotonic_clock()) * 1_000))
        timer.start(min(attempt_timeout_ms, remaining_ms))
        loop.exec()
        timer.stop()
        socket.disconnectFromServer()
        socket.abort()
        if response == [_RESPONSE_OK]:
            return
        if response == [_RESPONSE_ERROR]:
            raise SingleInstanceError("The existing application instance rejected activation.")
        if monotonic_clock() >= deadline:
            break
        retry_loop = QEventLoop()
        QTimer.singleShot(
            min(retry_interval_ms, max(1, int((deadline - monotonic_clock()) * 1_000))),
            retry_loop.quit,
        )
        retry_loop.exec()
    raise SingleInstanceError("An existing application instance could not be activated.")
