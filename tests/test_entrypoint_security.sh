#!/bin/bash
set -euo pipefail

ENTRYPOINT="./action/entrypoint.sh"

echo "Running test_entrypoint_security.sh..."

# Mock the archguard command
mkdir -p /tmp/mock_bin
cat << 'EOF' > /tmp/mock_bin/archguard
#!/bin/bash
echo "MOCK_ARCHGUARD: $@"
EOF
chmod +x /tmp/mock_bin/archguard
export PATH="/tmp/mock_bin:$PATH"

export GITHUB_TOKEN="fake_token"
export INPUT_REPO_ROOT="."
export GITHUB_REPOSITORY="test/repo"
export GITHUB_OUTPUT="/dev/null"

# Test 1: Empty input (no crash)
echo "Test 1: Empty EXTRA_ARGS"
INPUT_EXTRA_ARGS="" $ENTRYPOINT | grep "MOCK_ARCHGUARD:" > /tmp/out1
grep "MOCK_ARCHGUARD: analyze --repo ." /tmp/out1
echo "PASS: Empty input"

# Test 2: Valid allowlisted flags
echo "Test 2: Valid allowlisted flags"
INPUT_EXTRA_ARGS="--dry-run --json --output-dir=/github/workspace/out" $ENTRYPOINT | grep "MOCK_ARCHGUARD:" > /tmp/out2
grep "MOCK_ARCHGUARD: analyze --repo . --out-file /tmp/archguard-result.json --repo-slug test/repo --dry-run --json --output-dir=/github/workspace/out" /tmp/out2
echo "PASS: Valid allowlisted flags"

# Test 3: Injection blocked
echo "Test 3: Shell injection blocked"
INPUT_EXTRA_ARGS="--dry-run \$(cat /etc/passwd) ; rm -rf /" $ENTRYPOINT > /tmp/out3 2>&1 || true
grep "Ignoring potentially unsafe argument: \$(cat" /tmp/out3
grep "Ignoring potentially unsafe argument: /etc/passwd)" /tmp/out3
grep "Ignoring potentially unsafe argument: ;" /tmp/out3
grep "Ignoring potentially unsafe argument: rm" /tmp/out3
grep "Ignoring potentially unsafe argument: -rf" /tmp/out3
grep "Ignoring potentially unsafe argument: /" /tmp/out3
echo "PASS: Shell injection blocked"

echo "All tests passed!"
