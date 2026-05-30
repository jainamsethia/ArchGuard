def test_read_last_run_finds_written_event():
    """read_last_run must find events written by analyze_cmd."""
    import tempfile
    import os
    from pathlib import Path
    from archguard.audit.logger import AuditLogger
    from archguard.config import AUDIT_EVENT_ANALYSIS

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "audit.jsonl")
        logger = AuditLogger(Path(log_path))
        logger.log(event=AUDIT_EVENT_ANALYSIS, score=0.3, violations=2)

        result = logger.read_last_run()
        assert result is not None, "read_last_run must return the event we just wrote"
        assert result.get("event") == AUDIT_EVENT_ANALYSIS


def test_analysis_orchestrator_logs_parse_failure(tmp_path):
    from pathlib import Path
    from unittest.mock import patch
    from archguard.audit.logger import AuditLogger
    from archguard.analysis.layers import AnalysisOrchestrator

    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)

    bad_py = tmp_path / "bad.py"
    bad_py.write_text("def f(:\n    pass\n")

    # Create dummy config so load_contract doesn't fail
    config_yml = tmp_path / ".archguard.yml"
    config_yml.write_text("modules:\n  - name: dummy\n    path: .\n")

    orchestrator = AnalysisOrchestrator(repo_root=tmp_path)
    orchestrator._audit = logger

    class MockFailure:
        file_path = "bad.py"
        error_type = "SyntaxError"
        error_message = "unexpected EOF"
        is_critical = True

    def mock_l1(py_files, affected, commit_sha, parse_failures):
        parse_failures.append(MockFailure())
        return 0.0, []

    with patch("archguard.analysis.layers.AnalysisOrchestrator._run_layer1", side_effect=mock_l1), \
         patch("archguard.analysis.layers.AnalysisOrchestrator._run_layer2", return_value=(0.0, [])), \
         patch("archguard.analysis.layers.AnalysisOrchestrator._run_layer4", return_value=(0.0, [])):
        result = orchestrator.run([bad_py], commit_sha="1234567", quiet=True)

    assert log_file.exists(), "Audit log should have been created."
    
    import json
    logs = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    parse_failures = [log for log in logs if log.get("event") == "parse_failure"]
    
    assert len(parse_failures) == 1
    assert "error_type" in parse_failures[0]
    assert "bad.py" in parse_failures[0]["file"]


def test_strict_mode_raises_error_with_default_secret(tmp_path, monkeypatch):
    """Test that strict mode raises ConfigError when secret is the default."""
    from archguard.audit.logger import AuditLogger
    from archguard.utils.errors import ConfigError
    
    monkeypatch.setenv("ARCHGUARD_AUDIT_STRICT", "1")
    monkeypatch.delenv("ARCHGUARD_AUDIT_SECRET", raising=False)
    
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)
    
    import pytest
    with pytest.raises(ConfigError, match="You must provide a secure ARCHGUARD_AUDIT_SECRET in strict mode."):
        logger.log(event="test_event")

def test_strict_mode_passes_with_custom_secret(tmp_path, monkeypatch):
    """Test that strict mode works when custom secret is set."""
    from archguard.audit.logger import AuditLogger
    
    monkeypatch.setenv("ARCHGUARD_AUDIT_STRICT", "1")
    monkeypatch.setenv("ARCHGUARD_AUDIT_SECRET", "custom-secret-key")
    
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)
    
    # Should not raise
    logger.log(event="test_event")
    
    assert log_file.exists()
    assert "test_event" in log_file.read_text()

def test_non_strict_mode_warns_only(tmp_path, monkeypatch, caplog):
    """Test that non-strict mode only logs a warning with default secret."""
    from archguard.audit.logger import AuditLogger
    import logging
    
    monkeypatch.delenv("ARCHGUARD_AUDIT_STRICT", raising=False)
    monkeypatch.delenv("ARCHGUARD_AUDIT_SECRET", raising=False)
    
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)
    
    with caplog.at_level(logging.DEBUG):
        # Should not raise
        logger.log(event="test_event")
        
    assert log_file.exists()
    assert "ARCHGUARD_AUDIT_SECRET is not set." in caplog.text
