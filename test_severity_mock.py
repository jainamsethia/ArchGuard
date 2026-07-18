import json
import sys
from pathlib import Path
from enum import Enum
from archguard.cli._analyze_output import _write_json_output
from archguard.analysis.layers import AnalysisResult

class MockBand(Enum):
    HEALTHY = "Healthy"
    WARN = "Warn"
    FAIL = "Fail"

class MockLayerScores:
    layer1_violation = 0
    layer2_coupling = 0
    layer3_package = 0
    layer4_framework = 0

class MockArchDebt:
    def __init__(self):
        self.band = MockBand.FAIL
        self.health_score = 42
        self.health_grade = "F"
        self.layer_scores = MockLayerScores()
        self.coupling_violations = 0
        self.package_violations = 0
        self.framework_violations = 0

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    LOW = "low"

class MockViolation:
    def __init__(self):
        self.layer = 1
        self.file_path = "test.py"
        self.module_name = "test"
        self.message = "bad"
        self.severity = Severity.CRITICAL
        self.suppressed = False
        self.explanation = "no"

class MockResult(AnalysisResult):
    def __init__(self):
        self.violations = [MockViolation()]
        self.archdebt = MockArchDebt()
        self.commit_sha = "abcd"
        self.changed_files = ["test.py"]

class MockOpts:
    out_file = Path("mock_out.json")

result = MockResult()
opts = MockOpts()

try:
    _write_json_output(result, opts)
except Exception as e:
    import traceback
    traceback.print_exc()

with open("mock_out.json") as f:
    data = json.load(f)
    print("SEVERITY:", data["violations"][0]["severity"])
