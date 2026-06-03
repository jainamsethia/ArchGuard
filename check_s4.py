import os
import re

def search_files(directory, patterns):
    results = {p: [] for p in patterns}
    for root, _, files in os.walk(directory):
        for f in files:
            if not f.endswith(".py"): continue
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                lines = file.readlines()
                for i, line in enumerate(lines):
                    for p in patterns:
                        if re.search(p, line):
                            results[p].append(f"{path}:{i+1}:{line.strip()}")
    return results

print("=== time.sleep ===")
time_sleep = search_files("archguard/github", ["time\.sleep"])
print(time_sleep)

print("=== module=unknown ===")
module_unknown = search_files("archguard", ['module="unknown"'])
print(module_unknown)

print("=== import requests ===")
import_req = search_files("archguard", ["import requests"])
print(import_req)

import anthropic
print("=== anthropic ===")
print(anthropic.__version__)
