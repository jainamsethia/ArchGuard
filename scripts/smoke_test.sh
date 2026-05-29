#!/bin/bash
set -e

echo "=== ArchGuard Smoke Test ==="
cd /tmp && mkdir -p smoke_test_repo && cd smoke_test_repo
git init
echo "def hello(): pass" > main.py
git add . && git commit -m "init"

archguard init --confirm-all --repo .
archguard status
archguard analyze --dry-run
archguard history

echo "=== All smoke tests passed ==="
