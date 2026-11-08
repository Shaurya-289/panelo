"""Shell-level startup registration coordination and drift inspection."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from panelo.desktop.errors import StartupRegistrationError
from panelo.windows.startup import REG_SZ, RegistryValue, StartupRegistry

_LOGGER = logging.getLogger("panelo.desktop.startup")


class StartupState(StrEnum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"
    MISMATCHED = "MISMATCHED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StartupStatus:
    state: StartupState
    expected_command: str | None
    actual_value: RegistryValue | None


class _Settings(Protocol):
    start_with_windows: bool
    version: int


class _SettingsService(Protocol):
    def get_settings(self) -> _Settings: ...
    def update_settings(self, patch: object) -> object: ...


class _Application(Protocol):
    @property
    def settings(self) -> _SettingsService: ...


def quote_windows_argument(value: str) -> str:
    """Quote exactly one argv item using CommandLineToArgvW-compatible rules."""
    output = ['"']
    slashes = 0
    for char in value:
        if char == "\\":
            slashes += 1
        elif char == '"':
            output.append("\\" * (slashes * 2 + 1))
            output.append('"')
            slashes = 0
        else:
            output.append("\\" * slashes)
            output.append(char)
            slashes = 0
    output.append("\\" * (slashes * 2))
    output.append('"')
    return "".join(output)


def build_startup_command(launcher: Path) -> str:
    if not launcher.is_absolute():
        raise StartupRegistrationError("A dedicated desktop launcher path must be absolute.")
    resolved = launcher.expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise StartupRegistrationError("A verified dedicated desktop launcher is required.")
    command = f"{quote_windows_argument(str(resolved))} --startup"
    if len(command) > 260:
        raise StartupRegistrationError("Startup command is too long for the Run registry value.")
    return command


GUI_SCRIPT = "panelo.exe"


def default_launcher() -> Path | None:
    """The program Windows should start at sign-in: the frozen executable, or the installed
    ``panelo`` GUI script next to the interpreter.  ``None`` when there is neither."""
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve()
    else:
        candidate = Path(sys.executable).resolve().parent / GUI_SCRIPT
    return candidate if candidate.is_file() else None


class StartupCoordinator:
    def __init__(
        self, application: _Application, registry: StartupRegistry, launcher: Path | None
    ) -> None:
        self._application, self._registry, self._launcher = application, registry, launcher

    def inspect(self) -> StartupStatus:
        if self._launcher is None:
            return StartupStatus(StartupState.UNAVAILABLE, None, None)
        expected = build_startup_command(self._launcher)
        actual = self._registry.inspect()
        if actual is None:
            state = StartupState.DISABLED
        elif (
            actual.value_type == REG_SZ
            and isinstance(actual.value, str)
            and actual.value == expected
        ):
            state = StartupState.ENABLED
        else:
            state = StartupState.MISMATCHED
        return StartupStatus(state, expected, actual)

    def set_enabled(
        self, enabled: bool, expected_version: int, *, repair: bool = False
    ) -> StartupStatus:
        status = self.inspect()
        if status.state is StartupState.UNAVAILABLE:
            raise StartupRegistrationError(
                "Start with Windows is unavailable without a dedicated launcher."
            )
        if status.state is StartupState.MISMATCHED and not repair:
            raise StartupRegistrationError(
                "Existing Windows startup registration differs; explicit repair is required."
            )
        previous = status.actual_value
        changed_registry = False
        try:
            if enabled:
                if status.state is not StartupState.ENABLED:
                    assert status.expected_command is not None
                    self._registry.write(status.expected_command)
                    changed_registry = True
            elif previous is not None:
                self._registry.delete()
                changed_registry = True
            self._persist(enabled, expected_version)
        except Exception as primary:
            if changed_registry:
                try:
                    self._registry.restore(previous)
                except Exception as compensation:
                    error = StartupRegistrationError(
                        "Settings failed and startup-registry compensation also failed.",
                        primary_error=primary,
                        compensation_error=compensation,
                        drift_state=self._drift_state_after_compensation_failure(),
                        operation="enable" if enabled else "disable",
                    )
                    raise error from compensation
            raise
        return self.inspect()

    def _drift_state_after_compensation_failure(self) -> str:
        try:
            return self.inspect().state.value
        except Exception:
            return "UNKNOWN"

    @staticmethod
    def _patch(expected_version: int, enabled: bool) -> object:
        from panelo.schemas.settings import SettingsPatch

        return SettingsPatch(expected_version=expected_version, start_with_windows=enabled)

    def _persist(self, enabled: bool, expected_version: int) -> None:
        current = self._application.settings.get_settings()
        if current.version != expected_version:
            from panelo.core.exceptions import StaleVersionError

            raise StaleVersionError(
                "Settings were changed by another operation. Refresh and retry."
            )
        if current.start_with_windows != enabled:
            self._application.settings.update_settings(self._patch(expected_version, enabled))


def migrate_legacy_startup_entry(
    legacy_registry: StartupRegistry, registry: StartupRegistry, launcher: Path | None
) -> bool:
    """Swap the old ``ProductivityApp`` Run value for Panelo's own, pointing at ``launcher``.

    Returns whether anything changed.  Never raises: a failure is logged and the old entry is
    left in place (it is retried at the next start).
    """
    try:
        if legacy_registry.inspect() is None:
            return False
        if launcher is None:
            _LOGGER.warning(
                "An old ProductivityApp startup entry exists, but there is no Panelo launcher to "
                "point a new one at; leaving it as it is."
            )
            return False
        if registry.inspect() is None:
            registry.write(build_startup_command(launcher))
        legacy_registry.delete()
        _LOGGER.info("Replaced the old ProductivityApp startup entry with Panelo's.")
        return True
    except Exception:
        _LOGGER.exception("Could not replace the old ProductivityApp startup entry.")
        return False
