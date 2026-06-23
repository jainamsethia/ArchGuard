#!/usr/bin/env bash
# ArchGuard local development setup
# Usage: bash scripts/setup.sh
set -euo pipefail

echo "🔧  ArchGuard — Local Setup"
echo "================================"

# ── Check required tools ─────────────────────────────────────────
check_tool() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "❌  $1 is required but not installed."
    echo "    $2"
    exit 1
  }
  echo "✅  $1 found: $($1 --version 2>&1 | head -1)"
}

check_tool python3  "Install Python 3.11+ from https://python.org"
check_tool poetry   "Install Poetry: pip install poetry  or  pipx install poetry"
check_tool git      "Install git from https://git-scm.com"
check_tool docker   "Install Docker Desktop: https://docs.docker.com/get-docker/"

echo ""

# ── Install Python dependencies ──────────────────────────────────
echo "📦  Installing Python dependencies..."
poetry install --with dev
echo ""

# ── Set up .env ──────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo "📋  Created .env from .env.example"
  echo "    ✏   Open .env and set ANTHROPIC_API_KEY and GITHUB_TOKEN for full functionality"
else
  echo "📋  .env already exists — skipping"
fi

echo ""

# ── Summary ──────────────────────────────────────────────────────
echo "✅  Setup complete!"
echo ""
echo "Next steps:"
echo "  make dev         — Start the dashboard (http://localhost:8000)"
echo "  make test        — Run unit tests"
echo "  make docker-up   — Start with Docker Compose"
echo ""
