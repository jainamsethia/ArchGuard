import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from archguard.analysis._reinference import _run_reinference
from archguard.utils.errors import AnalysisError

def test_run_reinference_drift_ml_exception():
    with patch("archguard.contract.reinference.ReinferenceEngine.check_staleness"), \
         patch("archguard.analysis.semantic.SemanticAnalyzer.compute_drift") as mock_compute:
         
        mock_compute.side_effect = RuntimeError("Missing ML dependencies")
        
        # Should continue without raising
        _run_reinference(
            Path("/tmp"), MagicMock(), MagicMock(), {},
            {"m1": [Path("/tmp/a.py")]}, "sha", drift_results=None
        )

def test_run_reinference_drift_other_exception():
    with patch("archguard.contract.reinference.ReinferenceEngine.check_staleness"), \
         patch("archguard.analysis.semantic.SemanticAnalyzer.compute_drift") as mock_compute:
         
        mock_compute.side_effect = ValueError("Some error")
        
        with pytest.raises(AnalysisError, match="Reinference check failed"):
            _run_reinference(
                Path("/tmp"), MagicMock(), MagicMock(), {},
                {"m1": [Path("/tmp/a.py")]}, "sha", drift_results=None
            )

def test_run_reinference_propose_exception():
    with patch("archguard.contract.reinference.ReinferenceEngine.check_staleness"), \
         patch("archguard.contract.reinference.ReinferenceEngine.should_propose") as mock_propose:
         
        mock_propose.side_effect = ValueError("Some propose error")
        
        with pytest.raises(AnalysisError, match="Reinference check failed"):
            _run_reinference(
                Path("/tmp"), MagicMock(), MagicMock(), {},
                {"m1": [Path("/tmp/a.py")]}, "sha", drift_results={"m1": 0.9}
            )

def test_run_reinference_outer_exception():
    with patch("archguard.contract.reinference.ReinferenceEngine.check_staleness") as mock_check:
         
        mock_check.side_effect = ValueError("Outer error")
        
        with pytest.raises(AnalysisError, match="Reinference check failed"):
            _run_reinference(
                Path("/tmp"), MagicMock(), MagicMock(), {},
                {"m1": [Path("/tmp/a.py")]}, "sha", drift_results=None
            )
