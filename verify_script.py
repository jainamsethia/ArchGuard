import subprocess
import json
import os

env = os.environ.copy()
env["ARCHGUARD_SKIP_LLM"] = "1"
result = subprocess.run(
    ["python", "-m", "archguard.cli.main", "analyze", "--repo", "tests/fixtures/layer1_forbidden", "--output", "json"],
    capture_output=True,
    text=True,
    env=env
)

print("Return code:", result.returncode)
print("STDOUT length:", len(result.stdout))
print("STDERR length:", len(result.stderr))
if len(result.stdout) > 0:
    try:
        data = json.loads(result.stdout)
        print("SEVERITY:", data["violations"][0]["severity"])
    except Exception as e:
        print("JSON Error:", e)
        print("First 100 chars of stdout:", result.stdout[:100])
