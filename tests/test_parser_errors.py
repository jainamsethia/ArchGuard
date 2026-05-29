import pytest
from pathlib import Path
from archguard.analysis.parser import ImportParser
from archguard.utils.errors import AnalysisPartialError

def test_syntax_error_file_is_collected_not_raised(tmp_path):
    # write temp file with syntax error
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bad_file = repo_root / "bad.py"
    bad_file.write_text("def class )()()-- syntax error\n")

    parser = ImportParser()
    module_paths = {"core": ["."]}
    
    # assert parser returns ParseResult with failures
    result = parser.parse_repo(repo_root, module_paths)
    
    # assert result.is_partial == True
    assert result.is_partial is True
    assert len(result.failures) == 1
    
    failure = result.failures[0]
    assert failure.file_path == bad_file
    assert failure.error_type == "SyntaxError"
    assert not failure.is_critical
    
    # assert edges list is empty for that file
    assert len(result.edges) == 0

def test_valid_files_not_affected_by_one_bad_file(tmp_path):
    # parse mix of valid and invalid files
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bad_file = repo_root / "bad.py"
    bad_file.write_text("def class )()()-- syntax error\n")
    
    good_file = repo_root / "good.py"
    good_file.write_text("import os\n")

    parser = ImportParser()
    module_paths = {"core": ["."]}
    result = parser.parse_repo(repo_root, module_paths)
    
    # assert valid files' edges are returned correctly
    assert result.is_partial is True
    assert len(result.failures) == 1
    assert len(result.edges) == 1
    assert result.edges[0].imported_module == "os"
    
def test_critical_failure_raises_with_allow_partial_false(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    good_file = repo_root / "good.py"
    good_file.write_text("import os\n")
    
    parser = ImportParser()
    module_paths = {"core": ["."]}
    
    # mock tree-sitter to raise RuntimeError
    def mock_parse(*args, **kwargs):
        raise RuntimeError("Infrastructure failure")
        
    monkeypatch.setattr(parser._parser, "parse", mock_parse)
    
    # assert AnalysisPartialError is raised
    with pytest.raises(AnalysisPartialError, match="Critical parse failures in 1 files"):
        parser.parse_repo(repo_root, module_paths, allow_partial=False)

def test_parse_failures_written_to_audit_log(tmp_path):
    # Run parser through orchestrator and assert audit log contains failure records
    from archguard.analysis.layers import AnalysisOrchestrator
    from archguard.audit.logger import AuditLogger
    
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    # Set up basic contract
    contract_file = repo_root / ".archguard.yml"
    contract_file.write_text("version: '3.0'\nmodules: [{name: core, path: .}]\n")
    
    bad_file = repo_root / "bad.py"
    bad_file.write_text("def class )()()-- syntax error\n")
    
    orchestrator = AnalysisOrchestrator(repo_root)
    orchestrator._audit = AuditLogger(repo_root / "audit.jsonl")
    
    # run orchestrator
    result = orchestrator.run([bad_file], "commit123")
    assert result.partial_analysis is True
    assert len(result.parse_failures) == 1
    
    # Check audit log
    audit_data = (repo_root / "audit.jsonl").read_text()
    assert "parse_failure" in audit_data
    assert "SyntaxError" in audit_data
