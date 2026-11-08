from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from panelo.core.exceptions import (
    HotkeyRegistrationError,
    StaleVersionError,
)
from panelo.desktop.errors import StartupRegistrationError
from panelo.desktop.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    WM_HOTKEY,
    HotkeyManager,
    _NativeFilter,
    parse_hotkey,
)
from panelo.desktop.startup import (
    StartupCoordinator,
    StartupState,
    build_startup_command,
    quote_windows_argument,
)
from panelo.windows.startup import REG_SZ, RegistryValue, WindowsStartupRegistry


@dataclass
class _Settings:
    global_hotkey: str = "Ctrl+Alt+Space"
    start_with_windows: bool = False
    version: int = 1
    minimize_to_tray: bool = True


class _SettingsService:
    def __init__(self, settings: _Settings | None = None, error: Exception | None = None) -> None:
        self.settings = settings or _Settings()
        self.error = error

    def get_settings(self) -> _Settings:
        return self.settings

    def update_settings(self, patch: Any) -> _Settings:
        if self.error is not None:
            raise self.error
        if patch.expected_version != self.settings.version:
            raise StaleVersionError("stale")
        if patch.global_hotkey is not None:
            self.settings.global_hotkey = patch.global_hotkey
        if patch.start_with_windows is not None:
            self.settings.start_with_windows = patch.start_with_windows
        self.settings.version += 1
        return self.settings


class _Application:
    def __init__(self, service: _SettingsService, close_error: Exception | None = None) -> None:
        self.settings = service
        self.close_error = close_error

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error


class _HotkeyBackend:
    def __init__(self, *, register_error: Exception | None = None, unregister_error: Exception | None = None) -> None:
        self.register_error, self.unregister_error = register_error, unregister_error
        self.registered: dict[int, tuple[int, int, int]] = {}
        self.register_calls: list[int] = []
        self.unregister_calls: list[int] = []

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, virtual_key: int) -> None:
        self.register_calls.append(hotkey_id)
        if self.register_error is not None:
            raise self.register_error
        if hotkey_id in self.registered:
            raise HotkeyRegistrationError("duplicate")
        self.registered[hotkey_id] = (hwnd, modifiers, virtual_key)

    def unregister(self, hwnd: int, hotkey_id: int) -> None:
        self.unregister_calls.append(hotkey_id)
        if self.unregister_error is not None:
            raise self.unregister_error
        self.registered.pop(hotkey_id, None)


class _SelectiveHotkeyBackend(_HotkeyBackend):
    def __init__(self, failures: dict[int, int]) -> None:
        super().__init__()
        self.failures = failures
        self.unregister_attempts: list[int] = []

    def unregister(self, hwnd: int, hotkey_id: int) -> None:
        self.unregister_attempts.append(hotkey_id)
        remaining = self.failures.get(hotkey_id, 0)
        if remaining:
            self.failures[hotkey_id] = remaining - 1
            raise HotkeyRegistrationError("unregister failed")
        super().unregister(hwnd, hotkey_id)










class _QApplication:
    def __init__(self) -> None:
        self.filters: list[object] = []

    def installNativeEventFilter(self, value: object) -> None:
        self.filters.append(value)

    def removeNativeEventFilter(self, value: object) -> None:
        self.filters.remove(value)


class _Window:
    def __init__(self) -> None:
        self.hidden = True
        self.minimized = False
        self.show_count = self.normal_count = self.raise_count = self.activate_count = 0

    def winId(self) -> int:
        return 12345

    def isHidden(self) -> bool:
        return self.hidden

    def isMinimized(self) -> bool:
        return self.minimized

    def show(self) -> None:
        self.hidden = False
        self.show_count += 1

    def showNormal(self) -> None:
        self.hidden = self.minimized = False
        self.normal_count += 1

    def raise_(self) -> None:
        self.raise_count += 1

    def activateWindow(self) -> None:
        self.activate_count += 1

    def hide(self) -> None:
        self.hidden = True




class _Registry:
    def __init__(self, value: RegistryValue | None = None) -> None:
        self.value, self.error, self.restore_error = value, None, None
        self.siblings: dict[str, RegistryValue] = {"UnrelatedApp": RegistryValue(b"unrelated", 3)}
        self.inspect_count = self.write_count = self.delete_count = self.restore_count = 0

    def inspect(self) -> RegistryValue | None:
        self.inspect_count += 1
        return self.value

    def write(self, command: str) -> None:
        self.write_count += 1
        if self.error:
            raise self.error
        self.value = RegistryValue(command, REG_SZ)

    def restore(self, value: RegistryValue | None) -> None:
        self.restore_count += 1
        if self.restore_error:
            raise self.restore_error
        self.value = value

    def delete(self) -> None:
        self.delete_count += 1
        if self.error:
            raise self.error
        self.value = None


class _RegistryKey:
    def __enter__(self) -> _RegistryKey:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _WinregFake:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_WRITE = 2
    KEY_SET_VALUE = 4

    def __init__(self) -> None:
        self.values: dict[str, tuple[object, int]] = {}

    def OpenKey(self, *_: object) -> _RegistryKey:
        return _RegistryKey()

    def CreateKeyEx(self, *_: object) -> _RegistryKey:
        return _RegistryKey()

    def QueryValueEx(self, _: _RegistryKey, name: str) -> tuple[object, int]:
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name]

    def SetValueEx(self, _: _RegistryKey, name: str, __: int, value_type: int, value: object) -> None:
        self.values[name] = (value, value_type)

    def DeleteValue(self, _: _RegistryKey, name: str) -> None:
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]


class _NativeMessage(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wparam", ctypes.c_size_t),
        ("lparam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


def _route_hotkey(filter_: _NativeFilter, hotkey_id: int) -> bool:
    message = _NativeMessage(12345, WM_HOTKEY, hotkey_id, 0, 0, 0, 0)
    return filter_.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))[0]


def _launcher(tmp_path: Path) -> Path:
    launcher = tmp_path / "desktop launcher.exe"
    launcher.write_bytes(b"launcher")
    return launcher


def test_hotkey_parser_normalizes_default_and_supported_values() -> None:
    default = parse_hotkey("Ctrl+Alt+Space")
    assert default.canonical == "Ctrl+Alt+Space"
    assert default.modifiers == MOD_CONTROL | MOD_ALT
    assert default.registration_modifiers == MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
    assert parse_hotkey("shift+ctrl+f11").canonical == "Ctrl+Shift+F11"
    assert parse_hotkey("alt+ctrl+9").canonical == "Ctrl+Alt+9"


@pytest.mark.parametrize("value", ["Ctrl+A", "A", "Ctrl+Alt+Win+A", "Ctrl+Alt+F12", "Ctrl++A", "Ctrl+Ctrl+A", "Ctrl+Alt", "Ctrl+Alt+A+B"])
def test_hotkey_parser_rejects_invalid_combinations(value: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(value)


def test_hotkey_replacement_persists_only_after_candidate_registration() -> None:
    backend, app, qt = _HotkeyBackend(), _Application(_SettingsService()), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    manager.replace(parse_hotkey("Ctrl+Shift+F1"), lambda value: setattr(app.settings.settings, "global_hotkey", value))
    assert app.settings.settings.global_hotkey == "Ctrl+Shift+F1"
    assert len(backend.registered) == 1
    assert manager.active_hotkey is not None and manager.active_hotkey.canonical == "Ctrl+Shift+F1"


def test_hotkey_replacement_compensates_when_settings_persistence_fails() -> None:
    backend, qt = _HotkeyBackend(), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    with pytest.raises(StaleVersionError):
        manager.replace(parse_hotkey("Ctrl+Shift+F1"), lambda _: (_ for _ in ()).throw(StaleVersionError("stale")))
    assert manager.active_hotkey is not None and manager.active_hotkey.canonical == "Ctrl+Alt+Space"
    assert len(backend.registered) == 1


def test_hotkey_conflict_leaves_existing_binding_untouched() -> None:
    backend, qt = _HotkeyBackend(), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    backend.register_error = HotkeyRegistrationError("busy")
    with pytest.raises(HotkeyRegistrationError):
        manager.replace(parse_hotkey("Ctrl+Shift+F1"), lambda _: None)
    assert manager.active_hotkey is not None and manager.active_hotkey.canonical == "Ctrl+Alt+Space"
    assert len(backend.registered) == 1


def test_hotkey_close_unregisters_all_bindings_and_removes_filter() -> None:
    backend, qt = _HotkeyBackend(), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    manager.close()
    manager.close()
    assert backend.registered == {}
    assert qt.filters == []


def test_native_filter_ignores_unrelated_messages_and_routes_only_active_id() -> None:
    class _Message(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wparam", ctypes.c_size_t),
            ("lparam", ctypes.c_ssize_t),
            ("time", ctypes.c_uint),
            ("x", ctypes.c_long),
            ("y", ctypes.c_long),
        ]

    calls: list[str] = []
    active = [1]
    filter_ = _NativeFilter(55, lambda: active[0], lambda: calls.append("activate"))
    unrelated = _Message(55, WM_HOTKEY, 2, 0, 0, 0, 0)
    assert filter_.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(unrelated))[0] is False
    expected = _Message(55, WM_HOTKEY, 1, 0, 0, 0, 0)
    assert filter_.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(expected))[0] is True
    assert calls == ["activate"]


def test_startup_command_quotes_launcher_and_has_only_startup_flag(tmp_path: Path) -> None:
    command = build_startup_command(_launcher(tmp_path))
    assert command.endswith('" --startup')
    assert command.count("--startup") == 1
    assert quote_windows_argument("C:\\a b\\") == '"C:\\a b\\\\"'


def test_startup_inspection_distinguishes_disabled_enabled_mismatched_and_unavailable(tmp_path: Path) -> None:
    app, registry = _Application(_SettingsService()), _Registry()
    coordinator = StartupCoordinator(app, registry, _launcher(tmp_path))
    assert coordinator.inspect().state is StartupState.DISABLED
    registry.value = RegistryValue(coordinator.inspect().expected_command or "", REG_SZ)
    assert coordinator.inspect().state is StartupState.ENABLED
    registry.value = RegistryValue('"other.exe" --startup', REG_SZ)
    assert coordinator.inspect().state is StartupState.MISMATCHED
    assert StartupCoordinator(app, registry, None).inspect().state is StartupState.UNAVAILABLE


def test_startup_enable_disable_and_registry_compensation_preserve_exact_value(tmp_path: Path) -> None:
    previous = RegistryValue("external command", 7)
    registry, service = _Registry(), _SettingsService()
    coordinator = StartupCoordinator(_Application(service), registry, _launcher(tmp_path))
    assert coordinator.set_enabled(True, 1).state is StartupState.ENABLED
    assert service.settings.start_with_windows is True
    assert coordinator.set_enabled(False, 2).state is StartupState.DISABLED
    registry.value = previous
    service.error = StaleVersionError("stale")
    with pytest.raises(StaleVersionError):
        coordinator.set_enabled(True, 3, repair=True)
    assert registry.value == previous


def test_startup_mismatch_requires_explicit_repair_and_compensation_failure_is_reported(tmp_path: Path) -> None:
    registry, service = _Registry(RegistryValue("external", 7)), _SettingsService(error=StaleVersionError("stale"))
    coordinator = StartupCoordinator(_Application(service), registry, _launcher(tmp_path))
    with pytest.raises(StartupRegistrationError):
        coordinator.set_enabled(True, 1)
    registry.restore_error = OSError("cannot restore")
    with pytest.raises(StartupRegistrationError):
        coordinator.set_enabled(True, 1, repair=True)


def test_windows_startup_adapter_preserves_registry_type_for_compensation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _WinregFake()
    monkeypatch.setattr(WindowsStartupRegistry, "_winreg", staticmethod(lambda: fake))
    registry = WindowsStartupRegistry(value_name="isolated")
    assert registry.inspect() is None
    registry.write("expected")
    assert registry.inspect() == RegistryValue("expected", REG_SZ)
    registry.restore(RegistryValue("external", 7))
    assert registry.inspect() == RegistryValue("external", 7)
    registry.delete()
    registry.delete()
    assert registry.inspect() is None


@pytest.mark.parametrize("launcher", [Path("launcher.exe"), Path(".") / "launcher.exe", Path("..") / "launcher.exe"])
def test_startup_launcher_rejects_relative_paths(launcher: Path) -> None:
    with pytest.raises(StartupRegistrationError):
        build_startup_command(launcher)


def test_startup_launcher_requires_an_existing_absolute_file(tmp_path: Path) -> None:
    with pytest.raises(StartupRegistrationError):
        build_startup_command(tmp_path / "missing launcher.exe")
    assert build_startup_command(_launcher(tmp_path)).endswith('" --startup')


def test_registry_adapter_preserves_raw_non_string_values(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _WinregFake()
    fake.values["isolated"] = (b"\x01\x02", 3)
    monkeypatch.setattr(WindowsStartupRegistry, "_winreg", staticmethod(lambda: fake))
    registry = WindowsStartupRegistry(value_name="isolated")
    original = registry.inspect()
    assert original == RegistryValue(b"\x01\x02", 3)
    registry.write("expected")
    registry.restore(original)
    assert registry.inspect() == original


def test_startup_compensation_failure_preserves_both_errors_and_drift(tmp_path: Path) -> None:
    primary = StaleVersionError("stale")
    compensation = OSError("cannot restore")
    registry = _Registry(RegistryValue(17, 4))
    registry.restore_error = compensation
    coordinator = StartupCoordinator(_Application(_SettingsService(error=primary)), registry, _launcher(tmp_path))
    with pytest.raises(StartupRegistrationError) as caught:
        coordinator.set_enabled(True, 1, repair=True)
    error = caught.value
    assert error.primary_error is primary
    assert error.compensation_error is compensation
    assert error.operation == "enable"
    assert error.drift_state in {StartupState.ENABLED.value, StartupState.MISMATCHED.value, "UNKNOWN"}


def test_startup_enable_disable_and_repair_matrix_preserves_external_values(tmp_path: Path) -> None:
    service = _SettingsService()
    registry = _Registry()
    coordinator = StartupCoordinator(_Application(service), registry, _launcher(tmp_path))
    assert coordinator.set_enabled(True, 1).state is StartupState.ENABLED
    expected = registry.value
    assert coordinator.set_enabled(True, 2).state is StartupState.ENABLED
    assert registry.value == expected
    assert coordinator.set_enabled(False, 2).state is StartupState.DISABLED
    external = RegistryValue(b"foreign", 3)
    registry.value = external
    with pytest.raises(StartupRegistrationError):
        coordinator.set_enabled(True, 3)
    assert registry.value == external
    assert coordinator.set_enabled(True, 3, repair=True).state is StartupState.ENABLED
    assert coordinator.set_enabled(False, 4).state is StartupState.DISABLED


def test_hotkey_pending_cleanup_reuses_old_id_or_fails_cleanly() -> None:
    backend, qt = _SelectiveHotkeyBackend({0x5041: 1}), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    manager.replace(parse_hotkey("Ctrl+Shift+F1"), lambda _: None)
    assert manager.cleanup_pending
    manager.replace(parse_hotkey("Ctrl+Alt+F2"), lambda _: None)
    assert manager.active_hotkey is not None and manager.active_hotkey.canonical == "Ctrl+Alt+F2"
    assert not manager.cleanup_pending

    blocked, qt = _SelectiveHotkeyBackend({0x5041: 99}), _QApplication()
    blocked_manager = HotkeyManager(qt, _Window(), blocked, lambda: None)
    blocked_manager.start("Ctrl+Alt+Space")
    blocked_manager.replace(parse_hotkey("Ctrl+Shift+F1"), lambda _: None)
    with pytest.raises(HotkeyRegistrationError, match="No reserved"):
        blocked_manager.replace(parse_hotkey("Ctrl+Alt+F2"), lambda _: None)
    assert blocked_manager.active_hotkey is not None
    assert blocked_manager.active_hotkey.canonical == "Ctrl+Shift+F1"


def test_stale_old_hotkey_is_ignored_and_cleanup_retries() -> None:
    calls: list[str] = []
    backend, qt = _SelectiveHotkeyBackend({0x5041: 1}), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: calls.append("show"))
    manager.start("Ctrl+Alt+Space")
    manager.replace(parse_hotkey("Ctrl+Shift+F1"), lambda _: None)
    assert manager._filter is not None
    assert _route_hotkey(manager._filter, 0x5041) is False
    assert _route_hotkey(manager._filter, 0x5042) is True
    assert calls == ["show"]
    manager.close()
    assert 0x5041 in backend.unregister_attempts
    assert 0x5042 in backend.unregister_attempts
    assert not manager.cleanup_pending


def test_same_hotkey_checks_version_without_os_reregistration() -> None:
    backend, qt = _HotkeyBackend(), _QApplication()
    manager = HotkeyManager(qt, _Window(), backend, lambda: None)
    manager.start("Ctrl+Alt+Space")
    with pytest.raises(StaleVersionError):
        manager.replace(
            parse_hotkey("Ctrl+Alt+Space"),
            lambda _: (_ for _ in ()).throw(StaleVersionError("stale")),
        )
    assert len(backend.registered) == 1










def test_disable_settings_failure_restores_exact_raw_registry_value(tmp_path: Path) -> None:
    previous = RegistryValue(b"external", 3)
    service = _SettingsService(_Settings(start_with_windows=True), error=StaleVersionError("stale"))
    registry = _Registry(previous)
    coordinator = StartupCoordinator(_Application(service), registry, _launcher(tmp_path))
    with pytest.raises(StaleVersionError):
        coordinator.set_enabled(False, 1, repair=True)
    assert registry.value == previous










def test_reg_dword_startup_value_is_mismatched_and_preserved(tmp_path: Path) -> None:
    raw = RegistryValue(1, 4)
    registry = _Registry(raw)
    coordinator = StartupCoordinator(_Application(_SettingsService()), registry, _launcher(tmp_path))
    assert coordinator.inspect().state is StartupState.MISMATCHED
    assert registry.value == raw and isinstance(registry.value.value, int)
    assert registry.write_count == registry.delete_count == 0


def test_enable_matrix_covers_string_mismatch_write_failure_and_generic_compensation(
    tmp_path: Path,
) -> None:
    mismatch = RegistryValue('"other.exe" --startup', REG_SZ)
    service, registry = _SettingsService(), _Registry(mismatch)
    coordinator = StartupCoordinator(_Application(service), registry, _launcher(tmp_path))
    with pytest.raises(StartupRegistrationError):
        coordinator.set_enabled(True, 1)
    assert registry.value == mismatch and service.settings.start_with_windows is False
    assert registry.write_count == registry.delete_count == 0

    write_error = StartupRegistrationError("write failed")
    failing_registry = _Registry()
    failing_registry.error = write_error
    with pytest.raises(StartupRegistrationError) as write_caught:
        StartupCoordinator(_Application(_SettingsService()), failing_registry, _launcher(tmp_path)).set_enabled(True, 1)
    assert write_caught.value is write_error
    assert failing_registry.value is None

    primary = RuntimeError("settings failed")
    generic_service, absent = _SettingsService(error=primary), _Registry()
    sibling = absent.siblings["UnrelatedApp"]
    with pytest.raises(RuntimeError) as generic_caught:
        StartupCoordinator(_Application(generic_service), absent, _launcher(tmp_path)).set_enabled(True, 1)
    assert generic_caught.value is primary
    assert absent.value is None and absent.restore_count == 1
    assert absent.siblings["UnrelatedApp"] == sibling


def test_disable_matrix_covers_absent_mismatches_repair_and_compensation_failure(
    tmp_path: Path,
) -> None:
    absent_service, absent_registry = _SettingsService(_Settings(start_with_windows=True)), _Registry()
    absent = StartupCoordinator(_Application(absent_service), absent_registry, _launcher(tmp_path))
    assert absent.set_enabled(False, 1).state is StartupState.DISABLED
    assert absent_service.settings.start_with_windows is False
    assert absent_registry.delete_count == 0

    for raw in (RegistryValue('"other.exe" --startup', REG_SZ), RegistryValue(b"foreign", 3)):
        service, registry = _SettingsService(_Settings(start_with_windows=True)), _Registry(raw)
        with pytest.raises(StartupRegistrationError):
            StartupCoordinator(_Application(service), registry, _launcher(tmp_path)).set_enabled(False, 1)
        assert registry.value == raw and registry.delete_count == 0
        assert service.settings.start_with_windows is True

    repaired_service, repaired_registry = _SettingsService(_Settings(start_with_windows=True)), _Registry(
        RegistryValue('"other.exe" --startup', REG_SZ)
    )
    sibling = repaired_registry.siblings["UnrelatedApp"]
    repaired = StartupCoordinator(_Application(repaired_service), repaired_registry, _launcher(tmp_path))
    assert repaired.set_enabled(False, 1, repair=True).state is StartupState.DISABLED
    assert repaired_registry.value is None and repaired_registry.siblings["UnrelatedApp"] == sibling
    assert repaired_service.settings.start_with_windows is False

    primary, compensation = StaleVersionError("stale"), OSError("restore failed")
    failed_service = _SettingsService(_Settings(start_with_windows=True), error=primary)
    failed_registry = _Registry(RegistryValue(b"foreign", 3))
    failed_registry.restore_error = compensation
    with pytest.raises(StartupRegistrationError) as caught:
        StartupCoordinator(_Application(failed_service), failed_registry, _launcher(tmp_path)).set_enabled(
            False, 1, repair=True
        )
    error = caught.value
    assert error.primary_error is primary and error.compensation_error is compensation
    assert error.operation == "disable" and error.drift_state == StartupState.DISABLED.value




