# Changelog

All notable changes to archguard are documented here.

## [0.3.0] - 2026-06-10

### Added
- Architecture Fitness Functions
- AI Architecture Advisor
- Architecture Evolution Tracking
- AI Remediation Plans
- PR Risk Analysis
- Dependency Health Score
- fitness CLI support
- history CLI enhancements

## [0.2.0] - 2026-06-03
### Fixed
- C-7: Unified audit event name — restores trends, history, incremental mode
- C-1: compute_archdebt no longer requires ML deps
- C-8: Profile threshold logic corrected
- C-9: _phase4_embeddings NameError fixed
- C-5: .archguard.yml schema fixed
- C-6: Class-body imports moved to module level
- C-4: Hardcoded model string replaced with constant
- C-2: Docker container now runs as non-root

### Added
- Database migration system
- Path traversal validation
- End-to-end CI self-test
- Inline GitHub code annotations (Checks API)

### Performance
- SentenceTransformer model cached per instance (5–10× Layer 3 speedup)
- Streaming embedding batches (prevents OOM on large repos)
- Layer 1+2 run concurrently

## [Unreleased]

### Changed
- **Packaging fix**: `poetry install --with ml/cloud/dashboard` (group syntax) is replaced by `poetry install --extras ml/cloud/dashboard` for local dev. The `ml`, `cloud`, and `dashboard` dependencies have been moved from group dependencies to optional core dependencies.

### Fixed
- Unified audit event name to restore trends and history functionality.
  **Migration Note**: Existing `.archguard-cache/audit.jsonl` files containing `analysis_complete` events will not be read by the new constant. Old cache data is lost.

## [0.1.0] - 2026-05-27

### Added
- Core CLI skeleton: archguard init, analyze, suppress, contract, status
- Layer 1: Import boundary violation detection (tree-sitter-python)
- Layer 2: Coupling delta analysis with configurable budget
- Layer 3: Semantic drift detection via MiniLM-L6-v2 + FAISS
- Layer 3: Duplication detection via cosine similarity indexing
- Layer 4: LLM explanation via Anthropic Claude (primary) + ollama (fallback)
- archguard init: 5-phase onboarding with PyDriller + Louvain community detection
- archguard analyze: full pipeline with GitHub PR comment posting
- archguard suppress: violation suppression store with audit trail
- archguard contract: re-inference proposal system
- archguard status: contract health display
- JSON Schema v3.0 contract validation
- SQLite WAL embedding cache with staleness detection
- Secret redaction pre-filter for LLM inputs
- Audit logger (JSONL, 10MB rotation)
- GitHub Action wrapper (action.yml + entrypoint.sh)
- pytest-benchmark suite with latency threshold enforcement
- Docker multi-stage image (non-root, python:3.11-slim)
