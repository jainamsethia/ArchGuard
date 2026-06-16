
from archguard.analysis.deps import analyze_dependencies
from pathlib import Path
import tempfile
with tempfile.TemporaryDirectory() as d:
    result = analyze_dependencies(Path(d))
assert result.skipped
print(f'PASS: graceful skip')
