"""Bounded subprocess probe used only by future interactive Windows test runs.

It deliberately owns no production startup policy: it exercises only the
mutex and local-endpoint primitives with a unique identity supplied by tests.
"""

from __future__ import annotations

import argparse
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from productivity_app.core.exceptions import HotkeyRegistrationError
from productivity_app.desktop.instance import (
    ActivationResponse,
    ActivationServer,
    request_activation,
)
from productivity_app.windows.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    WindowsHotkeyBackend,
)
from productivity_app.windows.mutex import WindowsMutexBackend


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "role",
        choices=(
            "primary-owner",
            "delayed-primary",
            "secondary-contender",
            "release-primary",
            "hotkey-owner",
            "hotkey-contender",
            "crash-owner",
        ),
    )
    parser.add_argument("--identity")
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--release-name")
    parser.add_argument("--hotkey-id", type=int, default=0x6E01)
    parser.add_argument("--virtual-key", type=int, default=0x7A)
    parser.add_argument("--hold-ms", type=int, default=1_000)
    args = parser.parse_args()
    QApplication.instance() or QApplication(["pass3-child"])
    if args.role.startswith("hotkey"):
        _run_hotkey(args)
        return
    if args.role == "release-primary":
        request_activation(args.server_name, startup_timeout_ms=5_000, retry_interval_ms=20, attempt_timeout_ms=200)
        print(json.dumps({"released": True}), flush=True)
        return
    if args.identity is None:
        parser.error("--identity is required for instance roles")
    lease = WindowsMutexBackend().acquire(args.identity)
    result: dict[str, object] = {"primary": lease.primary, "role": args.role}
    server: ActivationServer | None = None
    try:
        if args.role == "secondary-contender":
            assert not lease.primary
            request_activation(
                args.server_name,
                startup_timeout_ms=5_000,
                retry_interval_ms=20,
                attempt_timeout_ms=200,
            )
            result.update(backend_created=False, activation_result="OK")
            print(json.dumps(result), flush=True)
            return
        if lease.primary:
            ready = args.role != "delayed-primary"
            busy_count = activation_count = 0

            def activate() -> ActivationResponse:
                nonlocal activation_count, busy_count
                if not ready:
                    busy_count += 1
                    print(json.dumps({"busy": busy_count}), flush=True)
                    return ActivationResponse.BUSY
                activation_count += 1
                return ActivationResponse.OK

            server = ActivationServer(args.server_name, activate)
            server.start()
            result["listening"] = server.is_listening
            result["ready"] = ready
            release_server: ActivationServer | None = None
            if args.role == "delayed-primary":
                if args.release_name is None:
                    parser.error("--release-name is required for delayed-primary")

                def release() -> ActivationResponse:
                    nonlocal ready
                    ready = True
                    return ActivationResponse.OK

                release_server = ActivationServer(args.release_name, release)
                release_server.start()
        print(json.dumps(result), flush=True)
        loop = QEventLoop()
        QTimer.singleShot(max(1, args.hold_ms), loop.quit)
        loop.exec()
        if args.role == "delayed-primary":
            print(
                json.dumps({"ready": ready, "activations": activation_count, "busy": busy_count}),
                flush=True,
            )
    finally:
        if "release_server" in locals() and release_server is not None:
            release_server.close()
        if server is not None:
            server.close()
        lease.close()


def _run_hotkey(args: argparse.Namespace) -> None:
    backend = WindowsHotkeyBackend()
    try:
        backend.register(0, args.hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, args.virtual_key)
    except HotkeyRegistrationError as exc:
        print(json.dumps({"registered": False, "typed_error": True, "error": str(exc)}), flush=True)
        return
    try:
        print(json.dumps({"registered": True}), flush=True)
        loop = QEventLoop()
        QTimer.singleShot(max(1, args.hold_ms), loop.quit)
        loop.exec()
    finally:
        backend.unregister(0, args.hotkey_id)


if __name__ == "__main__":
    main()
