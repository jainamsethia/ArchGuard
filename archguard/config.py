"""Constants, exit codes, and event names for ArchGuard."""

import os
from dataclasses import dataclass
from typing import Any

# Exit codes
EXIT_SUCCESS: int = 0
EXIT_VIOLATION: int = 1  # Architectural violations detected
EXIT_CONFIG_ERROR: int = 2  # Contract missing, invalid schema, etc.
EXIT_ANALYSIS_ERROR: int = 3  # Analysis pipeline failure (parse error, etc.)
EXIT_AUTH_ERROR: int = 4  # GitHub token missing, API auth failure
EXIT_TIMEOUT: int = 5  # Analysis timed out
EXIT_LLM_ERROR: int = 6  # LLM API failure or unavailability

# Audit event names (string constants)
AUDIT_EVENT_ANALYSIS: str = "analysis_run"
EVENT_INIT_STARTED: str = "INIT_STARTED"
EVENT_INIT_COMPLETED: str = "INIT_COMPLETED"
EVENT_ANALYZE_STARTED: str = "ANALYZE_STARTED"
EVENT_ANALYZE_COMPLETED: str = "ANALYZE_COMPLETED"
EVENT_CONTRACT_VIOLATION: str = "CONTRACT_VIOLATION"
EVENT_SUPPRESSION_CREATED: str = "SUPPRESSION_CREATED"
EVENT_CONTRACT_PROPOSAL_EXPIRED: str = "CONTRACT_PROPOSAL_EXPIRED"
EVENT_LOCAL_LLM_FAILURE: str = "LOCAL_LLM_FAILURE"
EVENT_TRUNCATED_EXPLANATION: str = "TRUNCATED_EXPLANATION"
EVENT_DUPLICATION_SKIPPED: str = "DUPLICATION_SKIPPED"

# Schema
SCHEMA_VERSION: str = "3.0"
ARCHGUARD_CONFIG_FILE: str = ".archguard.yml"
ARCHGUARD_CONFIG_DIR: str = ".archguard"

# Audit log
AUDIT_LOG_MAX_SIZE_MB: int = int(os.environ.get("ARCHGUARD_AUDIT_MAX_SIZE_MB", "10"))
AUDIT_LOG_MAX_BYTES: int = AUDIT_LOG_MAX_SIZE_MB * 1024 * 1024
AUDIT_LOG_MAX_ENTRIES: int = int(os.environ.get("ARCHGUARD_AUDIT_MAX_ENTRIES", "1000"))
AUDIT_LOG_FILENAME: str = ".archguard-cache/audit.jsonl"

# Cache
EMBEDDING_CACHE_FILE: str = ".archguard-cache/embeddings.db"
EMBEDDING_BATCH_SIZE: int = int(os.environ.get("ARCHGUARD_EMBEDDING_BATCH_SIZE", "500"))
SUPPRESSION_FILE: str = ".archguard-cache/suppressions.jsonl"
PENDING_CONTRACTS_DIR: str = ".archguard-pending-contracts"
CHECKPOINTS_DIR: str = ".archguard-checkpoints"

# Fitness function severity levels
VALID_FITNESS_SEVERITIES: frozenset[str] = frozenset({"critical", "warn", "info"})


# ---------------------------------------------------------------------------
# Fitness function configuration
# ---------------------------------------------------------------------------




class FitnessFunctionConfigError(ValueError):
    """Raised when a FitnessFunctionConfig has invalid fields."""


@dataclass
class FitnessFunctionConfig:
    """Configuration for a single architectural fitness function rule.

    Fields:
        name: Human-readable identifier for the fitness function.
        rule: The rule expression to evaluate (must be non-empty).
        severity: One of 'critical', 'warn', or 'info'.
        rationale: Optional explanation of why this rule exists.
    """

    name: str
    rule: str
    severity: str = "warn"
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.rule or not self.rule.strip():
            raise FitnessFunctionConfigError(
                f"Fitness function '{self.name}': rule must be non-empty."
            )
        if self.severity not in VALID_FITNESS_SEVERITIES:
            raise FitnessFunctionConfigError(
                f"Fitness function '{self.name}': severity must be one of "
                f"{sorted(VALID_FITNESS_SEVERITIES)}, got '{self.severity}'."
            )


def parse_fitness_functions(
    contract: dict[str, Any],
) -> list[FitnessFunctionConfig]:
    """Parse the 'fitness_functions' section from a loaded contract dict.

    Returns an empty list if the section is missing or empty.
    """
    raw: list[dict[str, Any]] = contract.get("fitness_functions", [])
    configs: list[FitnessFunctionConfig] = []
    for entry in raw:
        configs.append(
            FitnessFunctionConfig(
                name=entry.get("name", ""),
                rule=entry.get("rule", ""),
                severity=entry.get("severity", "warn"),
                rationale=entry.get("rationale", ""),
            )
        )
    return configs


@dataclass
class ArchGuardConfig:
    """Root configuration object."""

    fail_on_critical_risk: bool = False
