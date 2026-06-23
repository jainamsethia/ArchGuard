import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from archguard.dashboard.pipeline_adapter import run_analysis_on_repo

def _mock_analysis_result():
    """Build a minimal mock AnalysisResult."""
    from archguard.analysis.scoring import ArchDebtResult, LayerScores, ArchDebtBand
    from archguard.analysis._models import AnalysisResult

    scores = LayerScores(0.1, 0.2, 0.1, 0.05)
    debt = ArchDebtResult(
        composite_score=0.1125,
        band=ArchDebtBand.HEALTHY,
        layer_scores=scores,
        weights=(0.25, 0.25, 0.25, 0.25),
        per_component_breach=False,
        composite_breach=False,
        should_fail_ci=False,
    )
    return AnalysisResult(
        archdebt=debt,
        violations=[],
        layer_scores=scores,
        modules_analyzed=3,
        skipped_layers_names=[],
        skip_reason="",
    )

@pytest.mark.asyncio
async def test_run_analysis_auto_generates_contract(tmp_path):
    """When .archguard.yml is absent, _generate_contract_sync should be called."""
    # No .archguard.yml in tmp_path
    (tmp_path / "module.py").write_text("# empty")

    with patch("archguard.dashboard.pipeline_adapter._generate_contract_sync") as mock_gen, \
         patch("archguard.dashboard.pipeline_adapter._run_analysis_sync", return_value=_mock_analysis_result()):
        result = await run_analysis_on_repo(
            repo_path=tmp_path, job_id="test-1", repo_url="https://github.com/x/y"
        )
        mock_gen.assert_called_once_with(tmp_path)
        assert result.contract_auto_generated is True
        assert result.error is None

@pytest.mark.asyncio
async def test_run_analysis_skips_contract_generation_if_exists(tmp_path):
    """When .archguard.yml exists, _generate_contract_sync should NOT be called."""
    (tmp_path / ".archguard.yml").write_text("version: '3.0'\nmodules: []\n")
    (tmp_path / "module.py").write_text("# empty")

    with patch("archguard.dashboard.pipeline_adapter._generate_contract_sync") as mock_gen, \
         patch("archguard.dashboard.pipeline_adapter._run_analysis_sync", return_value=_mock_analysis_result()):
        result = await run_analysis_on_repo(
            repo_path=tmp_path, job_id="test-2", repo_url="https://github.com/x/y"
        )
        mock_gen.assert_not_called()
        assert result.contract_auto_generated is False

@pytest.mark.asyncio
async def test_run_analysis_handles_orchestrator_exception(tmp_path):
    """Orchestrator exception → result.error is set; no exception propagates."""
    (tmp_path / ".archguard.yml").write_text("version: '3.0'\nmodules: []\n")
    (tmp_path / "module.py").write_text("# empty")

    with patch("archguard.dashboard.pipeline_adapter._run_analysis_sync", side_effect=RuntimeError("analysis exploded")):
        result = await run_analysis_on_repo(
            repo_path=tmp_path, job_id="test-3", repo_url="https://github.com/x/y"
        )
        assert result.error == "analysis exploded"
        assert result.health_score == 0.0
        assert result.health_grade == "F"

@pytest.mark.asyncio
async def test_run_analysis_no_python_files(tmp_path):
    """Repo with no .py files → skipped result with skip_reason."""
    (tmp_path / ".archguard.yml").write_text("version: '3.0'\nmodules: []\n")
    # No .py files
    result = await run_analysis_on_repo(
        repo_path=tmp_path, job_id="test-4", repo_url="https://github.com/x/y"
    )
    assert result.skipped is True
    assert "No Python files" in result.skip_reason
