import asyncio, uuid, os
from pathlib import Path
import sys

sys.path.insert(0, "d:\\study\\capstone\\archgurad_final\\archguard")
from archguard.dashboard.pipeline_adapter import run_analysis_on_repo

os.makedirs('empty_repo2', exist_ok=True)
with open('empty_repo2/test.txt', 'w') as f:
    f.write('test')

job = str(uuid.uuid4())
res = asyncio.run(run_analysis_on_repo(Path('empty_repo2'), job, 'http://repo'))
print(f'JOB: {job}, SKIPPED: {res.skipped}')

import json
try:
    with open('empty_repo2/.archguard-cache/audit.jsonl', 'r') as f:
        runs = [json.loads(line) for line in f]
    print(f'Found runs in audit log: {runs}')
except FileNotFoundError:
    print('No audit log found!')
