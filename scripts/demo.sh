#!/bin/bash
# ArchGuard Demo Script
# Run this to showcase ArchGuard in a terminal recording

set -e

# Setup
DEMO_REPO=$(mktemp -d)
cd "$DEMO_REPO"
git init

# Create a realistic Python project structure
mkdir -p payments orders notifications

cat > payments/__init__.py << 'EOF'
"""Payment processing module."""
def process_payment(amount: float) -> bool:
    return amount > 0
EOF

cat > orders/__init__.py << 'EOF'
"""Order management — INTENTIONALLY imports from payments (boundary violation)."""
from payments import process_payment  # ← This will be flagged!

def create_order(item_id: str, amount: float) -> dict:
    paid = process_payment(amount)
    return {"item_id": item_id, "paid": paid}
EOF

# Create contract
cat > .archguard.yml << 'EOF'
schema_version: "3.0"
modules:
  - name: payments
    paths: ["payments/"]
    coupling_budget: 5
  - name: orders
    paths: ["orders/"]
    coupling_budget: 8
    disallowed_imports: ["payments"]
fail_threshold: 0.75
EOF

git add . && git commit -m "Add sample project with architectural violation"

echo ""
echo "=== Running ArchGuard Analysis ==="
archguard analyze --repo . --verbose

echo ""
echo "=== Checking History ==="
archguard history

echo ""
echo "=== Generating HTML Report ==="
archguard report --output archguard-report.html
echo "Report saved to: $DEMO_REPO/archguard-report.html"
