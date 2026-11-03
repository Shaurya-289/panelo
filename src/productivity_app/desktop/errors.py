"""Errors owned by the optional desktop shell."""

from productivity_app.core.exceptions import IntegrationError


class ShellIntegrationError(IntegrationError):
    """A local desktop shell operation could not be completed."""

    code = "shell_integration_error"


class SingleInstanceError(ShellIntegrationError):
    """A primary instance exists but cannot safely be activated."""

    code = "single_instance_error"


class PlatformNotSupportedError(ShellIntegrationError):
    """A Windows-only shell adapter was invoked on another platform."""

    code = "platform_not_supported"


class StartupRegistrationError(ShellIntegrationError):
    """The exact per-user Windows startup entry could not be coordinated."""

    code = "startup_registration_error"

    def __init__(
        self,
        message: str,
        *,
        primary_error: Exception | None = None,
        compensation_error: Exception | None = None,
        drift_state: str | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.primary_error = primary_error
        self.compensation_error = compensation_error
        self.drift_state = drift_state
        self.operation = operation
