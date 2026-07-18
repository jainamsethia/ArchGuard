import subprocess
import json
import os
import sys

env = os.environ.copy()
env["ARCHGUARD_SKIP_LLM"] = "1"
result = subprocess.run(
    [sys.executable, "-m", "archguard.cli.main", "analyze", "--repo", "tests/fixtures/layer1_forbidden", "--output", "json"],
    capture_output=True,
    text=True,
    env=env
)

try:
    data = json.loads(result.stdout)
    if "violations" in data and len(data["violations"]) > 0:
        severity = data["violations"][0].get("severity")
        print(f"SEVERITY: {severity}")
    else:
        print("No violations found.")
except Exception as e:
    print(f"Failed to parse JSON: {e}")
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
