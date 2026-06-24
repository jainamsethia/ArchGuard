import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from archguard.dashboard.pipeline_adapter import run_analysis_on_repo



@pytest.fixture
def mock_repo_dir(tmp_path):
    repo_dir = tmp_path / "fake-repo"
    repo_dir.mkdir()
    (repo_dir / "dummy.py").write_text("print('hello')")
    yield repo_dir

@pytest.mark.asyncio
@patch("archguard.dashboard.pipeline_adapter._run_analysis_sync")
@patch("archguard.audit.logger.AuditLogger")
async def test_audit_log_written_after_successful_analysis(mock_audit_logger_cls, mock_run_sync, mock_repo_dir):
    mock_run_sync.return_value = MagicMock(
        violations=[],
        archdebt=MagicMock(health_score=95.0, health_grade="A", composite_score=0.5),
        layer_scores=MagicMock(layer1_violation=0, layer2_coupling=0, layer3_drift=0, layer4_duplication=0),
        modules_analyzed=1,
        skipped=False,
        skip_reason="",
        commit_sha="abcd123"
    )
    
    mock_audit_instance = MagicMock()
    mock_audit_logger_cls.return_value = mock_audit_instance
    
    async def _mock_emit(msg):
        pass
        
    await run_analysis_on_repo(
        repo_url="https://github.com/test/test",
        repo_path=mock_repo_dir,
        job_id="job123",
        progress_callback=_mock_emit,
        skip_explanation=True
    )
    
    mock_audit_instance.log_run.assert_called_once()
    kwargs = mock_audit_instance.log_run.call_args.kwargs
    assert kwargs["repo_url"] == "https://github.com/test/test"
    assert kwargs["job_id"] == "job123"

@pytest.mark.asyncio
@patch("archguard.dashboard.pipeline_adapter._run_analysis_sync")
@patch("archguard.audit.logger.AuditLogger")
async def test_audit_log_not_written_on_analysis_failure(mock_audit_logger_cls, mock_run_sync, mock_repo_dir):
    mock_run_sync.side_effect = Exception("Analysis failed")
    
    mock_audit_instance = MagicMock()
    mock_audit_logger_cls.return_value = mock_audit_instance
    
    async def _mock_emit(msg):
        pass
        
    await run_analysis_on_repo(
        repo_url="https://github.com/test/test",
        repo_path=mock_repo_dir,
        job_id="job123",
        progress_callback=_mock_emit,
        skip_explanation=True
    )
    
    mock_audit_instance.log_run.assert_not_called()

@pytest.mark.asyncio
@patch("archguard.dashboard.pipeline_adapter._run_analysis_sync")
@patch("archguard.audit.logger.AuditLogger")
async def test_audit_log_path_uses_cwd(mock_audit_logger_cls, mock_run_sync, mock_repo_dir):
    mock_run_sync.return_value = MagicMock(
        violations=[],
        archdebt=MagicMock(health_score=95.0, health_grade="A", composite_score=0.5),
        layer_scores=MagicMock(layer1_violation=0, layer2_coupling=0, layer3_drift=0, layer4_duplication=0),
        modules_analyzed=1,
        skipped=False,
        skip_reason="",
        commit_sha="abcd123"
    )
    
    async def _mock_emit(msg):
        pass
        
    await run_analysis_on_repo(
        repo_url="https://github.com/test/test",
        repo_path=mock_repo_dir,
        job_id="job123",
        progress_callback=_mock_emit,
        skip_explanation=True
    )
    
    mock_audit_logger_cls.assert_called_once()
    log_path = mock_audit_logger_cls.call_args.kwargs.get("log_path")
    assert log_path is not None
    
    from archguard.config import AUDIT_LOG_FILENAME
    expected_path = Path.cwd() / ".archguard-cache" / AUDIT_LOG_FILENAME
    assert str(log_path) == str(expected_path)
