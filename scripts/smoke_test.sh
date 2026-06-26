#!/bin/bash
# scripts/smoke_test.sh — Production smoke test for ArchGuard
# Usage: BASE_URL=http://localhost:8000 ./scripts/smoke_test.sh
# Exit 0 = all checks passed. Exit 1 = one or more checks failed.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
PASS=0
FAIL=0

check() {
    local name="$1"
    local actual="$2"
    local expected="$3"
    if [[ "$actual" == *"$expected"* ]]; then
        echo "✅ $name"
        ((PASS++)) || true
    else
        echo "❌ $name"
        echo "   Expected to contain: $expected"
        echo "   Got: $actual"
        ((FAIL++)) || true
    fi
}

check_status() {
    local name="$1"
    local url="$2"
    local expected_code="$3"
    local actual_code
    actual_code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [[ "$actual_code" == "$expected_code" ]]; then
        echo "✅ $name (HTTP $actual_code)"
        ((PASS++)) || true
    else
        echo "❌ $name — expected HTTP $expected_code, got HTTP $actual_code"
        ((FAIL++)) || true
    fi
}

check_header() {
    local name="$1"
    local url="$2"
    local header_name="$3"
    local expected_fragment="$4"
    local header_value
    header_value=$(curl -sI "$url" 2>/dev/null | grep -i "^${header_name}:" | head -1 || echo "")
    if [[ "$header_value" == *"$expected_fragment"* ]]; then
        echo "✅ $name"
        ((PASS++)) || true
    else
        echo "❌ $name — header ${header_name} does not contain: $expected_fragment"
        echo "   Got: $header_value"
        ((FAIL++)) || true
    fi
}

echo "Running smoke tests against: $BASE_URL"
echo "────────────────────────────────────────"

# [1] Health endpoint returns ok
HEALTH=$(curl -sf "$BASE_URL/health" 2>/dev/null || echo "CONNECTION_FAILED")
check "Health endpoint returns ok" "$HEALTH" '"status":"ok"'

# [2] Health endpoint returns a version (not "unknown")
check "Health endpoint returns version from importlib.metadata" "$HEALTH" '"version":'
# Verify it's not the old "unknown" fallback
if [[ "$HEALTH" == *'"version":"unknown"'* ]]; then
    echo "❌ Health endpoint returns version=unknown (LOW-008 fix not applied)"
    ((FAIL++)) || true
else
    echo "✅ Health endpoint version is not 'unknown'"
    ((PASS++)) || true
fi

# [3] Frontend main page loads
check_status "Frontend main page" "$BASE_URL/" "200"

# [4] Security headers present on all responses
check_header "CSP header present" "$BASE_URL/health" "content-security-policy" "default-src 'self'"
check_header "X-Content-Type-Options nosniff" "$BASE_URL/health" "x-content-type-options" "nosniff"
check_header "X-Frame-Options DENY" "$BASE_URL/health" "x-frame-options" "DENY"
check_header "Referrer-Policy set" "$BASE_URL/health" "referrer-policy" "strict-origin-when-cross-origin"

# [5] ENH-003: CSP script-src contains a nonce and no unsafe-inline
CSP_HEADER=$(curl -sI "$BASE_URL/" 2>/dev/null | grep -i "^content-security-policy:" | head -1 || echo "")
if [[ "$CSP_HEADER" == *"nonce-"* ]]; then
    echo "✅ CSP script-src contains nonce (ENH-003)"
    ((PASS++)) || true
else
    echo "❌ CSP script-src missing nonce — ENH-003 not applied"
    ((FAIL++)) || true
fi
if [[ "$CSP_HEADER" == *"'unsafe-inline'"* ]]; then
    SCRIPT_SECTION=$(echo "$CSP_HEADER" | grep -oP "script-src[^;]+" || echo "")
    if [[ "$SCRIPT_SECTION" == *"'unsafe-inline'"* ]]; then
        echo "❌ script-src still contains 'unsafe-inline' — ENH-003 incomplete"
        ((FAIL++)) || true
    else
        echo "✅ unsafe-inline appears only in style-src (not script-src)"
        ((PASS++)) || true
    fi
else
    echo "✅ CSP does not contain unsafe-inline in script-src"
    ((PASS++)) || true
fi

# [6] Submit job — invalid URL returns 422
check_status "POST /api/jobs — invalid URL rejected" \
    -X POST "$BASE_URL/api/jobs" \
    -H "Content-Type: application/json" \
    -d '{"github_url":"not-a-url"}' \
    "422"

# [7] Submit job — path traversal URL rejected (CRIT-001 fix)
check_status "POST /api/jobs — path traversal URL rejected" \
    -X POST "$BASE_URL/api/jobs" \
    -H "Content-Type: application/json" \
    -d '{"github_url":"https://github.com/owner/repo/../../../etc/passwd"}' \
    "422"

# [8] Advisor ask — payload too large rejected (MED-004 fix)
LONG_QUESTION=$(python3 -c "print('A' * 2001)")
check_status "POST /api/v1/advisor/ask — oversized question rejected" \
    -X POST "$BASE_URL/api/v1/advisor/ask" \
    -H "Content-Type: application/json" \
    -d "{\"question\":\"${LONG_QUESTION}\",\"context\":\"\"}" \
    "422"

# [9] Validate endpoint — valid URL returns 200
check_status "GET /api/jobs/validate — valid URL" \
    "$BASE_URL/api/jobs/validate?url=https%3A%2F%2Fgithub.com%2Fowner%2Frepo" \
    "200"

# (Old tests)
# Dashboard
check_status "Dashboard load" "$BASE_URL/dashboard.html" "200"

# Validate endpoint (invalid URL - POST)
check_status "Validate endpoint (invalid POST)" \
  -X POST "$BASE_URL/api/jobs/validate" \
  -H "Content-Type: application/json" \
  -d '{"github_url":"https://not-github.com/x/y"}' \
  "422"

# Runs endpoint
check_status "Runs endpoint" "$BASE_URL/api/runs" "200"

echo "────────────────────────────────────────"
echo "Results: $PASS passed, $FAIL failed"
echo ""

if [[ $FAIL -eq 0 ]]; then
    echo "✅ All smoke tests passed. Ready for deployment."
    exit 0
else
    echo "❌ $FAIL smoke test(s) failed. Do not deploy."
    exit 1
fi
