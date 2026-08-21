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

check_post() {
    # check_status only ever issued a GET: it takes (name, url, expected) and
    # ignored everything else. The three POST assertions below were calling it
    # with curl arguments, so "$2" became "-X", curl got no URL, and every one
    # of them reported HTTP 000 -- three security checks that printed a result
    # and could never pass. They test rejection of a malformed repository URL,
    # of a path-traversal URL, and of an oversized advisor payload.
    local name="$1"
    local url="$2"
    local body="$3"
    local expected_code="$4"
    local actual_code
    actual_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "$body" 2>/dev/null || echo "000")
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
    # POSIX ERE, not -P: GNU grep refuses -P outside a unibyte or UTF-8
    # locale, which prints a warning on every run in some shells.
    SCRIPT_SECTION=$(echo "$CSP_HEADER" | grep -oE "script-src[^;]+" || echo "")
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

# [5b] D8: directives default-src does not stand in for.
# frame-ancestors and form-action have no fallback at all, so without them the
# page can be framed and a form on it can post to any origin.
for directive in "object-src 'none'" "base-uri 'self'" "frame-ancestors 'none'" "form-action 'self'"; do
    if [[ "$CSP_HEADER" == *"$directive"* ]]; then
        echo "✅ CSP declares $directive"
        ((PASS++)) || true
    else
        echo "❌ CSP is missing $directive (D8)"
        ((FAIL++)) || true
    fi
done

# [6] Submit job — invalid URL returns 422
check_post "POST /api/v1/jobs — invalid URL rejected" \
    "$BASE_URL/api/v1/jobs" \
    '{"github_url":"not-a-url"}' \
    "422"

# [7] Submit job — path traversal URL rejected (CRIT-001 fix)
check_post "POST /api/v1/jobs — path traversal URL rejected" \
    "$BASE_URL/api/v1/jobs" \
    '{"github_url":"https://github.com/owner/repo/../../../etc/passwd"}' \
    "422"

# [8] Advisor ask — payload too large rejected (MED-004 fix)
# printf rather than python3: this script runs from CI, from a container and
# from a developer's shell, and python3 is not on PATH in all three.
LONG_QUESTION=$(printf 'A%.0s' $(seq 2001))
check_post "POST /api/v1/advisor/ask — oversized question rejected" \
    "$BASE_URL/api/v1/advisor/ask" \
    "{\"question\":\"${LONG_QUESTION}\",\"context\":\"\"}" \
    "422"

# [9] There was a "GET /api/jobs/validate — valid URL" check here expecting 200.
# The route is POST-only, so it got 404 and had been failing for as long as it
# had existed. Dropped rather than converted: the POST case below covers the
# route, and a valid-URL check reaches out to the GitHub API, which would make
# the smoke test fail on someone else's rate limit.

# Dashboard
check_status "Dashboard load" "$BASE_URL/dashboard.html" "200"

# Validate endpoint (invalid URL - POST)
check_post "Validate endpoint (invalid POST)" \
  "$BASE_URL/api/v1/jobs/validate" \
  '{"github_url":"https://not-github.com/x/y"}' \
  "422"

# Runs endpoint
check_status "Runs endpoint" "$BASE_URL/api/v1/runs" "200"

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
