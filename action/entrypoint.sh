#!/bin/bash
set -euo pipefail

# Fix workspace permissions so archguard can write cache and summary files
# GitHub Actions mounts /github/workspace as the host runner UID
if [ -d "/github/workspace" ]; then
  chown -R "$(id -u):$(id -g)" /github/workspace 2>/dev/null || true
  chmod -R u+rw /github/workspace 2>/dev/null || true
fi
# Ensure cache directory exists and is writable
mkdir -p /github/workspace/.archguard-cache
chmod 777 /github/workspace/.archguard-cache 2>/dev/null || true

REPO_ROOT="${INPUT_REPO_ROOT:-.}"
PR_NUMBER="${INPUT_PR_NUMBER:-}"
REPO_SLUG="${GITHUB_REPOSITORY:-}"
SKIP_EXPLANATION="${INPUT_SKIP_EXPLANATION:-false}"
FAIL_ON_WARN="${INPUT_FAIL_ON_WARN:-false}"
FAIL_FAST="${INPUT_FAIL_FAST:-false}"
DRY_RUN="${INPUT_DRY_RUN:-false}"
EXTRA_ARGS="${INPUT_EXTRA_ARGS:-}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "::error::GITHUB_TOKEN is required."
  exit 1
fi

# Security fix: Use array CMD to prevent shell injection from inputs
if [[ "${GITHUB_EVENT_NAME:-}" == "issue_comment" ]]; then
  CMD=("archguard" "github-sync" "--repo" "${REPO_ROOT}")
else
  CMD=("archguard" "analyze" "--repo" "${REPO_ROOT}" "--out-file" "/tmp/archguard-result.json")

  [[ -n "${PR_NUMBER}" ]] && CMD+=("--pr" "${PR_NUMBER}")
  [[ -n "${GITHUB_EVENT_NUMBER:-}" ]] && CMD+=("--pr-number" "${GITHUB_EVENT_NUMBER}")
  [[ -n "${REPO_SLUG}" ]] && CMD+=("--repo-slug" "${REPO_SLUG}")
  [[ "${SKIP_EXPLANATION}" == "true" ]] && CMD+=("--skip-explanation")
  [[ "${FAIL_ON_WARN}" == "true" ]] && CMD+=("--fail-on-warn")
  [[ "${FAIL_FAST}" == "true" ]] && CMD+=("--fail-fast")
  [[ "${DRY_RUN}" == "true" ]] && CMD+=("--dry-run")

  if [[ -n "${EXTRA_ARGS}" ]]; then
    read -ra EXTRA <<< "$INPUT_EXTRA_ARGS"
    CMD+=("${EXTRA[@]}")
  fi
fi

echo "::group::ArchGuard Analysis"
echo "Running: ${CMD[@]}"

set +e
"${CMD[@]}"
EXIT_CODE=$?
set -e

echo "::endgroup::"

if [ -f /tmp/archguard-result.json ]; then
  SCORE=$(python3 -c "import json,sys; d=json.load(open('/tmp/archguard-result.json')); print(d.get('score', 0))")
  BAND=$(python3 -c "import json,sys; d=json.load(open('/tmp/archguard-result.json')); print(d.get('band', 'UNKNOWN'))")
else
  echo "::warning::archguard-result.json not found. Score defaulting to 0."
  SCORE=0
  BAND="UNKNOWN"
fi
echo "archdebt-score=${SCORE}" >> "${GITHUB_OUTPUT}"
echo "archdebt-band=${BAND}" >> "${GITHUB_OUTPUT}"

exit ${EXIT_CODE}
