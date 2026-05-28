import os

for d, _, fs in os.walk('.'):
    for f in fs:
        if f.endswith('.py'):
            try:
                content = open(os.path.join(d, f), encoding='utf8', errors='ignore').read()
                if '_heuristic_layer_name' in content:
                    print(f"FOUND heuristic in {os.path.join(d, f)}")
                if '"/" in m' in content or "'/' in m" in content or 'r"\\" in m' in content or '"\\\\" in m' in content:
                    print(f"FOUND slash in {os.path.join(d, f)}")
                if 'community_members' in content:
                    print(f"FOUND community_members in {os.path.join(d, f)}")
            except Exception as e:
                print(f"Error {e} on {f}")
