import asyncio
from pathlib import Path
import pytest
from unittest.mock import patch
from archguard.dashboard.pipeline_adapter import run_analysis_on_repo, AnalysisJobResult

@pytest.mark.asyncio
async def test_analysis_timeout(tmp_path):
    # Mock _run_analysis_sync to sleep longer than the timeout
    def mock_run_analysis_sync(*args, **kwargs):
        import time
        time.sleep(2)
        return None

    # Patch the timeout value to be very short
    with patch("archguard.dashboard.pipeline_adapter.ANALYSIS_TIMEOUT_SECONDS", 0.5):
        with patch("archguard.dashboard.pipeline_adapter._run_analysis_sync", side_effect=mock_run_analysis_sync):
            repo_path = tmp_path
            (repo_path / "dummy.py").touch()
            # Run the analysis
            result = await run_analysis_on_repo(repo_path, "test_job", "http://dummy")
            
            assert isinstance(result, AnalysisJobResult)
            # Should have an error because of the timeout
            assert result.error is not None
            assert "Timeout" in str(result.error) or result.error != ""
