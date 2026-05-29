import os

"""Constants, exit codes, and event names for ArchGuard."""

# Exit codes
EXIT_OK: int = 0
EXIT_CONFIG_ERROR: int = 1
EXIT_VIOLATION: int = 1
EXIT_INTERNAL_ERROR: int = 2

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
