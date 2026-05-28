# ArchGuard

[![CI](https://github.com/jainam-b/archguard/actions/workflows/ci.yml/badge.svg)](https://github.com/jainam-b/archguard/actions/workflows/ci.yml)

Architectural drift detector for Python codebases.
Inspects every pull request and flags when code violates defined module boundaries.

## What it does

ArchGuard runs 4 analysis layers on changed Python files:

| Layer | Analysis | Signal |
|-------|----------|--------|
| L1 | Import boundary violations | Forbidden cross-module imports |
| L2 | Coupling delta | Fan-out exceeds defined budget |
| L3 | Semantic drift | Embedding centroid shift via MiniLM |
| L3 | Duplication | Cross-module function similarity via FAISS |
| L4 | LLM explanation | Claude/ollama explains violations |

Produces a composite **ArchDebt score** (0.0–1.0) and posts a PR comment with CI pass/fail.

## Quickstart

```bash
pip install archguard

# Initialize contract from commit history
cd your-repo
archguard init --confirm-all

# Analyze current changes
archguard analyze --dry-run

# Check contract status
archguard status
```

## Configuration

`.archguard.yml` example:

```yaml
schema_version: "3.0"
modules:
  - name: payments
    paths: [src/payments/]
    coupling_budget: 8
    semantic_drift_threshold: 0.25
  - name: orders
    paths: [src/orders/]
fail_threshold: 0.75
warn_threshold: 0.50
```

## GitHub Action

```yaml
- uses: ./
  with:
    pr-number: ${{ github.event.pull_request.number }}
    skip-explanation: 'false'
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

## Commands

| Command | Description |
|---------|-------------|
| `archguard init` | Auto-generate contract from commit history |
| `archguard analyze` | Run full analysis pipeline |
| `archguard status` | Display contract and audit log status |
| `archguard suppress add` | Suppress a known violation |
| `archguard suppress list` | List active suppressions |
| `archguard contract list-pending` | Show pending re-inference proposals |
| `archguard contract accept` | Accept a re-inferred contract |

## Architecture

```
archguard/
  cli/           # Typer CLI commands (thin orchestration)
  analysis/      # Pure analysis logic (parser, coupling, semantic, duplication, scoring)
  contract/      # YAML contract: schema, validation, loading, re-inference
  cache/         # SQLite WAL embedding cache
  github/        # GitHub API client and PR comment manager
  llm/           # Anthropic + ollama LLM clients
  suppression/   # Append-only suppression JSONL store
  audit/         # JSONL audit event logger
  utils/         # TTY detection, error helpers, secret redaction
```

## Development

```bash
git clone https://github.com/jainam-b/archguard
cd archguard
poetry install --with dev
poetry run pytest tests/unit -v
make benchmark
```

## License

MIT
