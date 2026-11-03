"""Thin HKCU Run adapter used by the optional desktop shell."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Protocol

from productivity_app.desktop.errors import PlatformNotSupportedError, StartupRegistrationError

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "ProductivityApp"
REG_SZ = 1


@dataclass(frozen=True, slots=True)
class RegistryValue:
    """The unmodified value and type read from the exact Run entry."""

    value: object
    value_type: int


class StartupRegistry(Protocol):
    def inspect(self) -> RegistryValue | None: ...

    def write(self, command: str) -> None: ...

    def restore(self, value: RegistryValue | None) -> None: ...

    def delete(self) -> None: ...


class WindowsStartupRegistry:
    """Touches only this application's per-user Run value."""

    def __init__(self, *, value_name: str = RUN_VALUE_NAME) -> None:
        self._value_name = value_name

    @staticmethod
    def _winreg() -> Any:
        if sys.platform != "win32":
            raise PlatformNotSupportedError("Windows startup registration requires Windows.")
        import winreg

        return winreg

    def inspect(self) -> RegistryValue | None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, value_type = winreg.QueryValueEx(key, self._value_name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StartupRegistrationError("Could not inspect Windows startup registration.") from exc
        return RegistryValue(value, value_type)

    def write(self, command: str) -> None:
        self._write_value(RegistryValue(command, REG_SZ))

    def restore(self, value: RegistryValue | None) -> None:
        if value is None:
            self.delete()
        else:
            self._write_value(value)

    def _write_value(self, value: RegistryValue) -> None:
        winreg = self._winreg()
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, self._value_name, 0, value.value_type, value.value)
        except OSError as exc:
            raise StartupRegistrationError("Could not write Windows startup registration.") from exc

    def delete(self) -> None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, self._value_name)
                except FileNotFoundError:
                    return
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StartupRegistrationError("Could not remove Windows startup registration.") from exc
