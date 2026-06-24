#!/usr/bin/env bash
# scripts/smoke_test.sh
# Usage: ./scripts/smoke_test.sh [BASE_URL]
# Default: http://localhost:8000

set -euo pipefail

BASE="${1:-http://localhost:8000}"
FAIL=0

check() {
    local name="$1"; local expected="$2"; local actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "✅ $name"
    else
        echo "❌ $name: expected=$expected actual=$actual"
        FAIL=1
    fi
}

# Health check
STATUS=$(curl -sf "$BASE/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])" 2>/dev/null || echo "FAIL")
check "Health endpoint" "ok" "$STATUS"

# Frontend
CODE=$(curl -so /dev/null -w "%{http_code}" "$BASE/")
check "Frontend load" "200" "$CODE"

# Dashboard
CODE=$(curl -so /dev/null -w "%{http_code}" "$BASE/dashboard.html")
check "Dashboard load" "200" "$CODE"

# Validate endpoint (valid URL)
CODE=$(curl -so /dev/null -w "%{http_code}" -X POST "$BASE/api/jobs/validate" \
  -H "Content-Type: application/json" \
  -d '{"github_url":"https://github.com/psf/requests"}' 2>/dev/null)
check "Validate endpoint (valid)" "200" "$CODE"

# Validate endpoint (invalid URL)
CODE=$(curl -so /dev/null -w "%{http_code}" -X POST "$BASE/api/jobs/validate" \
  -H "Content-Type: application/json" \
  -d '{"github_url":"https://not-github.com/x/y"}' 2>/dev/null)
check "Validate endpoint (invalid)" "422" "$CODE"

# Runs endpoint
CODE=$(curl -so /dev/null -w "%{http_code}" "$BASE/api/runs")
check "Runs endpoint" "200" "$CODE"

if [ $FAIL -eq 0 ]; then
    echo ""
    echo "All smoke tests passed ✅"
else
    echo ""
    echo "One or more smoke tests failed ❌"
    exit 1
fi
