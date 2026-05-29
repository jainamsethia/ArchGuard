# Changelog

All notable changes to archguard are documented here.

## [Unreleased]

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
