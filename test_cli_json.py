import subprocess
import os

repo_dir = "test_repo6"
os.chdir(repo_dir)

env = os.environ.copy()
env["ARCHGUARD_SKIP_LLM"] = "1"
print("Running archguard analyze...")
process = subprocess.Popen(["archguard", "analyze", "--output", "json", "--repo", "."], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

try:
    stdout, stderr = process.communicate(timeout=5)
except subprocess.TimeoutExpired:
    process.kill()
    stdout, stderr = process.communicate()
    print("Process killed due to timeout.")

print("=== STDOUT ===")
print(stdout)
print("=== STDERR ===")
print(stderr)
