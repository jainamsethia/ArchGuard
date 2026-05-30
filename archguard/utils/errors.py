"""Exit codes and error message helpers for ArchGuard."""

from typing import Any
from archguard.config import EXIT_CONFIG_ERROR, EXIT_ANALYSIS_ERROR, EXIT_LLM_ERROR


class ArchGuardError(Exception):
    """Base exception for all ArchGuard errors."""

    def __init__(
        self, message: str, exit_code: int = 1, cause: Exception | None = None
    ) -> None:
        self.message = message
        self.exit_code = exit_code
        self.cause = cause
        super().__init__(message)


class ConfigError(ArchGuardError):
    """Raised when configuration is invalid or missing."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message=message, exit_code=EXIT_CONFIG_ERROR, cause=cause)


class InternalError(ArchGuardError):
    """Raised on unexpected internal failures."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message=message, exit_code=EXIT_ANALYSIS_ERROR, cause=cause)


class ContractError(ArchGuardError):
    """Raised when contract validation or loading fails."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message=message, exit_code=EXIT_CONFIG_ERROR, cause=cause)


class AnalysisError(ArchGuardError):
    """Raised when analysis operations fail."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message=message, exit_code=EXIT_ANALYSIS_ERROR, cause=cause)


class AnalysisPartialError(ArchGuardError):
    """Raised when analysis could only complete partially."""

    def __init__(
        self,
        message: str,
        failures: list[Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.failures = failures or []
        super().__init__(message=message, exit_code=EXIT_ANALYSIS_ERROR, cause=cause)


class LLMError(ArchGuardError):
    """Raised when LLM operations fail."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message=message, exit_code=EXIT_LLM_ERROR, cause=cause)


def format_error(msg: str) -> str:
    """Format an error message with Rich markup."""
    return f"[red]Error:[/red] {msg}"


def format_warning(msg: str) -> str:
    """Format a warning message with Rich markup."""
    return f"[yellow]Warning:[/yellow] {msg}"
