"""Narrow real-Windows ownership tests; all resources use unique test identities."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest

from productivity_app.desktop.errors import StartupRegistrationError
from productivity_app.desktop.instance import (
    ActivationResponse,
    ActivationServer,
    activation_server_name,
)
from productivity_app.windows.mutex import WindowsMutexBackend, build_instance_identity
from productivity_app.windows.startup import RegistryValue, WindowsStartupRegistry

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows shell APIs")


def test_real_windows_mutex_releases_after_normal_owner_close(tmp_path: Path) -> None:
    identity = build_instance_identity(f"S-1-5-21-pass3-{uuid4().hex}", tmp_path / "shell.db")
    first = WindowsMutexBackend().acquire(identity)
    try:
        assert first.primary
        second = WindowsMutexBackend().acquire(identity)
        try:
            assert not second.primary
        finally:
            second.close()
    finally:
        first.close()
    replacement = WindowsMutexBackend().acquire(identity)
    try:
        assert replacement.primary
    finally:
        replacement.close()


def test_real_local_endpoint_is_reusable_after_close() -> None:
    name = f"pass3-endpoint-{uuid4().hex}"
    first = ActivationServer(name, lambda: ActivationResponse.OK)
    first.start()
    first.close()
    replacement = ActivationServer(name, lambda: ActivationResponse.OK)
    try:
        replacement.start()
        assert replacement.is_listening
    finally:
        replacement.close()


def test_distinct_database_identities_can_be_primary_together(tmp_path: Path) -> None:
    sid = f"S-1-5-21-pass3-{uuid4().hex}"
    one = WindowsMutexBackend().acquire(build_instance_identity(sid, tmp_path / "one.db"))
    two = WindowsMutexBackend().acquire(build_instance_identity(sid, tmp_path / "two.db"))
    try:
        assert one.primary and two.primary
    finally:
        two.close()
        one.close()


def test_multiple_process_contenders_leave_exactly_one_owner(tmp_path: Path) -> None:
    identity = build_instance_identity(f"S-1-5-21-pass3-{uuid4().hex}", tmp_path / "shared.db")
    child = Path(__file__).with_name("_pass3_child.py")
    base = [
        sys.executable,
        str(child),
        "primary-owner",
        "--identity",
        identity,
        "--server-name",
        activation_server_name(identity),
        "--hold-ms",
        "1500",
    ]
    owner = subprocess.Popen(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        first_line = owner.stdout.readline() if owner.stdout is not None else ""
        assert json.loads(first_line)["primary"] is True
        contenders = [
            subprocess.Popen(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(4)
        ]
        completed = [item.communicate(timeout=10) for item in contenders]
        results = [json.loads(stdout.splitlines()[0]) for stdout, _stderr in completed]
        assert all(item.returncode == 0 for item in contenders)
        assert [result["primary"] for result in results] == [False] * 4
    finally:
        owner.wait(timeout=10)


def test_forced_primary_termination_releases_mutex_and_endpoint(tmp_path: Path) -> None:
    identity = build_instance_identity(f"S-1-5-21-pass3-{uuid4().hex}", tmp_path / "crash.db")
    child = Path(__file__).with_name("_pass3_child.py")
    base = [
        sys.executable,
        str(child),
        "crash-owner",
        "--identity",
        identity,
        "--server-name",
        activation_server_name(identity),
        "--hold-ms",
        "10000",
    ]
    owner = subprocess.Popen(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        first_line = owner.stdout.readline() if owner.stdout is not None else ""
        assert json.loads(first_line) == {
            "primary": True,
            "role": "crash-owner",
            "listening": True,
            "ready": True,
        }
        owner.terminate()
        owner.wait(timeout=10)
        replacement = subprocess.run(base, capture_output=True, text=True, timeout=15)
        assert replacement.returncode == 0
        assert json.loads(replacement.stdout.splitlines()[0])["primary"] is True
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)


def test_delayed_primary_secondary_retries_busy_until_explicit_release(tmp_path: Path) -> None:
    identity = build_instance_identity(f"S-1-5-21-pass3-{uuid4().hex}", tmp_path / "delayed.db")
    child = Path(__file__).with_name("_pass3_child.py")
    server_name = activation_server_name(identity)
    release_name = f"{server_name}-release"
    primary_command = [
        sys.executable,
        str(child),
        "delayed-primary",
        "--identity",
        identity,
        "--server-name",
        server_name,
        "--release-name",
        release_name,
        "--hold-ms",
        "1500",
    ]
    secondary_command = [
        sys.executable,
        str(child),
        "secondary-contender",
        "--identity",
        identity,
        "--server-name",
        server_name,
    ]
    primary = subprocess.Popen(
        primary_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        initial = json.loads(primary.stdout.readline() if primary.stdout is not None else "")
        assert initial == {
            "primary": True,
            "role": "delayed-primary",
            "listening": True,
            "ready": False,
        }
        secondary = subprocess.Popen(
            secondary_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        busy = json.loads(primary.stdout.readline() if primary.stdout is not None else "")
        assert busy == {"busy": 1}
        release = subprocess.run(
            [sys.executable, str(child), "release-primary", "--server-name", release_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert release.returncode == 0
        assert json.loads(release.stdout.splitlines()[0]) == {"released": True}
        secondary_out, secondary_err = secondary.communicate(timeout=10)
        final_out, primary_err = primary.communicate(timeout=10)
        assert secondary.returncode == primary.returncode == 0, (secondary_err, primary_err)
        assert json.loads(secondary_out.splitlines()[0]) == {
            "primary": False,
            "role": "secondary-contender",
            "backend_created": False,
            "activation_result": "OK",
        }
        final = json.loads(final_out.splitlines()[-1])
        assert final["ready"] is True and final["activations"] == 1 and final["busy"] >= 1
    finally:
        if primary.poll() is None:
            primary.kill()
            primary.wait(timeout=10)


def test_cross_process_hotkey_collision_is_typed_and_releases_after_owner_exit() -> None:
    child = Path(__file__).with_name("_pass3_child.py")
    hotkey_id = 0x6F00 + (int(uuid4().hex[:3], 16) % 0x0F0)
    command = [
        sys.executable,
        str(child),
        "hotkey-owner",
        "--server-name",
        f"pass3-hotkey-{uuid4().hex}",
        "--hotkey-id",
        str(hotkey_id),
        "--virtual-key",
        "122",
        "--hold-ms",
        "1000",
    ]
    owner = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        owner_result = json.loads(owner.stdout.readline() if owner.stdout is not None else "")
        if not owner_result["registered"]:
            pytest.skip(f"No interactive global-hotkey capability: {owner_result['error']}")
        contender = subprocess.run(command, capture_output=True, text=True, timeout=10)
        result = json.loads(contender.stdout.splitlines()[0])
        assert contender.returncode == 0
        assert result["registered"] is False and result["typed_error"] is True
        assert owner.poll() is None
        owner.wait(timeout=10)
        reacquired = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert reacquired.returncode == 0
        assert json.loads(reacquired.stdout.splitlines()[0])["registered"] is True
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)


def test_isolated_real_registry_preserves_exact_raw_value_and_type() -> None:
    registry = WindowsStartupRegistry(value_name=f"ProductivityApp-Pass3-Test-{uuid4().hex}")
    original = None
    try:
        try:
            original = registry.inspect()
            registry.write('"C:\\pass3 launcher.exe" --startup')
        except StartupRegistrationError as exc:
            cause = exc.__cause__
            if isinstance(cause, PermissionError) or getattr(cause, "winerror", None) == 5:
                pytest.skip(f"HKCU Run write denied by policy: {exc}")
            raise
        assert registry.inspect() == RegistryValue('"C:\\pass3 launcher.exe" --startup', 1)
        mismatch = RegistryValue('"C:\\external.exe" --startup', 1)
        registry.restore(mismatch)
        assert registry.inspect() == mismatch
        raw_dword = RegistryValue(73, 4)
        registry.restore(raw_dword)
        assert registry.inspect() == raw_dword
    finally:
        with suppress(StartupRegistrationError):
            registry.restore(original)
