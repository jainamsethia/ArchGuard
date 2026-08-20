from dataclasses import dataclass


@dataclass
class FitnessFunctionResult:
    """Result of evaluating a single architectural fitness function rule."""

    rule: str
    passed: bool
    details: str | None = None
    error: str | None = None
