import sys
sys.path.insert(0, '.')
from archguard.cli._analyze_output import _write_audit_log
from archguard.analysis.scoring import LayerScores, ArchDebt
class MockV: pass
class MockResult:
    violations=[]
    layer_scores=LayerScores(1,2,3,4)
    skipped_layers_names=['Layer 3']
    archdebt=ArchDebt()
result=MockResult()
result.archdebt.health_score=100
result.archdebt.band=type('Band', (), {'name':'HEALTHY'})()
class Opts:
    repo=__import__('pathlib').Path('.')
_write_audit_log(result, Opts())
