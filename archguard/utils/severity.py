"""Severity enumeration for architectural violations."""

from enum import Enum


class Severity(str, Enum):
    """Classification of architectural violations by impact."""

    CRITICAL = "critical"  # Layer violations (architectural integrity)
    HIGH = "high"  # High coupling, cycles
    MEDIUM = "medium"  # Duplication above threshold
    LOW = "low"  # Semantic cohesion warnings
