#!/usr/bin/env bash
set -e

# 1. Builds the Docker image:
docker build -t archguard-smoke-test . 

# 2. Creates a minimal fixture repo in /tmp:
FIXTURE=/tmp/archguard-smoke-fixture
rm -rf $FIXTURE && mkdir -p $FIXTURE/src/api $FIXTURE/src/payments

echo "from src.payments import process" > $FIXTURE/src/api/__init__.py
echo "def process(): pass" > $FIXTURE/src/payments/__init__.py

cat > $FIXTURE/.archguard.yml << 'EOF'
schema_version: "3.0"
modules:
  - name: api
    paths: [src/api/]
  - name: payments
    paths: [src/payments/]
fail_threshold: 0.75
EOF

cd $FIXTURE
git init && git add . && git commit -m "init" --author="test <test@test.com>"

# 3. Runs the Docker container against the fixture:
docker run --rm \
  -v $FIXTURE:/github/workspace \
  -e GITHUB_OUTPUT=/tmp/gha-output \
  -e INPUT_PR_NUMBER="" \
  -e INPUT_SKIP_EXPLANATION="true" \
  archguard-smoke-test

# 4. Validates outputs:
echo "=== Smoke test complete ==="
if [ -f /tmp/archguard-smoke-fixture/.archguard-cache/*.db 2>/dev/null ] || \
   [ -f /tmp/archguard-result.json ]; then
  echo "✓ PASS: Output files created"
else
  echo "✗ FAIL: No output files found"
  exit 1
fi
