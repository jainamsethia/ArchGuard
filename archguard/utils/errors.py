"""Exit codes and error message helpers for ArchGuard."""


class ArchGuardError(Exception):
    """Base exception for all ArchGuard errors."""

    def __init__(self, message: str, exit_code: int) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


class ConfigError(ArchGuardError):
    """Raised when configuration is invalid or missing."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, exit_code=1)


class InternalError(ArchGuardError):
    """Raised on unexpected internal failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, exit_code=2)


def format_error(msg: str) -> str:
    """Format an error message with Rich markup."""
    return f"[red]Error:[/red] {msg}"


def format_warning(msg: str) -> str:
    """Format a warning message with Rich markup."""
    return f"[yellow]Warning:[/yellow] {msg}"
