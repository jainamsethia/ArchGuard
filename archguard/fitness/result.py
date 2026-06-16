from dataclasses import dataclass
from typing import Optional

@dataclass
class FitnessFunctionResult:
    """Result of evaluating a single architectural fitness function rule."""
    rule: str
    passed: bool
    details: Optional[str] = None
    error: Optional[str] = None
