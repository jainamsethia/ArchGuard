#!/bin/sh
# ArchGuard GitHub Action entrypoint.
#
# Translates the INPUT_* environment variables that action.yml declares into
# `archguard analyze` flags, then publishes the score and band as step outputs.
#
# This previously read `exec archguard "$@"`. A `docker` action with no `args:`
# in action.yml passes no arguments, so that invoked the bare CLI, which prints
# its help text and exits 0 -- every declared input was ignored and both
# declared outputs were never set. The contract restored here is the one
# asserted by tests/test_entrypoint_security.sh.
set -eu

# git refuses to operate on a workspace owned by another uid.
git config --global --add safe.directory "${GITHUB_WORKSPACE:-/github/workspace}"

RESULT_FILE="/tmp/archguard-result.json"
REPO_ROOT="${INPUT_REPO_ROOT:-.}"

set -- analyze --repo "$REPO_ROOT" --out-file "$RESULT_FILE"

if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    set -- "$@" --repo-slug "$GITHUB_REPOSITORY"
fi

# Prefer the explicit input; fall back to the env var the CLI already detects.
PR_NUMBER="${INPUT_PR_NUMBER:-}"
if [ -n "$PR_NUMBER" ]; then
    set -- "$@" --pr "$PR_NUMBER"
fi

is_true() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1 | true | yes) return 0 ;;
        *) return 1 ;;
    esac
}

is_true "${INPUT_SKIP_EXPLANATION:-}" && set -- "$@" --skip-explanation
is_true "${INPUT_FAIL_ON_WARN:-}" && set -- "$@" --fail-on-warn
is_true "${INPUT_FAIL_FAST:-}" && set -- "$@" --fail-fast
is_true "${INPUT_DRY_RUN:-}" && set -- "$@" --dry-run

# extra-args is free-form text from a workflow file. Word-split it, then keep
# only tokens that look like a CLI flag: `--name` or `--name=value` over a
# restricted value charset. Everything else -- shell metacharacters, bare
# paths, command substitution -- is dropped with a visible warning rather than
# silently, so a typo does not look like a working configuration.
for arg in ${INPUT_EXTRA_ARGS:-}; do
    if printf '%s' "$arg" | grep -Eq '^--[A-Za-z0-9][A-Za-z0-9-]*(=[A-Za-z0-9_/.,:@-]+)?$'; then
        set -- "$@" "$arg"
    else
        echo "Ignoring potentially unsafe argument: $arg" >&2
    fi
done

if [ -n "${INPUT_SLACK_WEBHOOK:-}" ]; then
    export ARCHGUARD_SLACK_WEBHOOK="$INPUT_SLACK_WEBHOOK"
fi

# The check run must be attached to the PR's head commit. On a pull_request
# event GITHUB_SHA is the ephemeral merge commit, which is not what reviewers
# see, so action.yml defaults this input to pull_request.head.sha.
if [ -n "${INPUT_GITHUB_SHA:-}" ]; then
    export GITHUB_SHA="$INPUT_GITHUB_SHA"
fi

# The analyze exit code is the action's verdict (1 = violations), but the
# outputs and the fitness check must still run, so capture it rather than
# letting `set -e` abort here.
set +e
archguard "$@"
ANALYZE_EXIT=$?
set -e

# -- Publish outputs ------------------------------------------------------
if [ -n "${GITHUB_OUTPUT:-}" ] && [ -f "$RESULT_FILE" ]; then
    python3 - "$RESULT_FILE" >>"$GITHUB_OUTPUT" <<'PY' || true
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    raise SystemExit(0)

print(f"archdebt-score={data.get('score', '')}")
print(f"archdebt-band={data.get('band', '')}")
print(f"health-grade={data.get('health_grade', '')}")
print(f"skipped={str(bool(data.get('skipped', False))).lower()}")
PY
fi

# -- Fitness functions ----------------------------------------------------
FITNESS_EXIT=0
if is_true "${INPUT_RUN_FITNESS_CHECK:-true}"; then
    set +e
    archguard fitness check --repo "$REPO_ROOT"
    FITNESS_EXIT=$?
    set -e
    if [ "$FITNESS_EXIT" -ne 0 ] && is_true "${INPUT_FITNESS_AS_WARNING:-}"; then
        echo "::warning::Fitness function checks failed (fitness-as-warning is on)."
        FITNESS_EXIT=0
    fi
fi

if [ "$ANALYZE_EXIT" -ne 0 ]; then
    exit "$ANALYZE_EXIT"
fi
exit "$FITNESS_EXIT"
