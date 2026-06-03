import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from archguard.analysis._orchestrator_run import _run_orchestrator
from archguard.analysis.layers import ViolationDetail

def test_run_orchestrator_no_py_files():
    orchestrator = MagicMock()
    orchestrator.repo_root = Path("/tmp")
    result = _run_orchestrator(orchestrator, [Path("/tmp/readme.md")], "sha")
    assert result.skipped is True
    assert result.skip_reason == "No Python files changed"

def test_run_orchestrator_fail_fast_layer1():
    orchestrator = MagicMock()
    orchestrator.repo_root = Path("/tmp")
    orchestrator.contract = {"fail_threshold": 0.5}
    orchestrator._audit = None
    
    # We just want to ensure it calls _build_partial_result correctly and returns it
    mock_partial_result = MagicMock()
    
    with patch("archguard.analysis._layer_runners._run_layer1") as m_l1, \
         patch("archguard.analysis._layer_runners._run_layer2") as m_l2, \
         patch("archguard.analysis._orchestrator_utils._get_affected_modules", return_value=[]), \
         patch("archguard.analysis._orchestrator_utils._build_partial_result", return_value=mock_partial_result) as mock_bpr:
        
        # layer 1 returns score 0.8 (exceeds 0.5 threshold)
        m_l1.return_value = (0.8, [ViolationDetail(1, "m", "msg", "file", "sha")])
        m_l2.return_value = (0.1, [])
        
        result = _run_orchestrator(orchestrator, [Path("/tmp/a.py")], "sha", fail_fast=True, quiet=True)
        assert result is mock_partial_result
        mock_bpr.assert_called_once()
        args = mock_bpr.call_args[0]
        # the first argument is repo_root, second is contract, third is filter_fn, fourth is layer1 score
        assert args[3] == 0.8

def test_run_orchestrator_layer3_ml_exception():
    orchestrator = MagicMock()
    orchestrator.repo_root = Path("/tmp")
    orchestrator.contract = {"fail_threshold": 0.9}
    orchestrator._audit = None
    
    with patch("archguard.analysis._layer_runners._run_layer1", return_value=(0.0, [])), \
         patch("archguard.analysis._layer_runners._run_layer2", return_value=(0.0, [])), \
         patch("archguard.analysis._orchestrator_utils._get_affected_modules", return_value=[]), \
         patch("archguard.analysis._layer_runners._run_layer3") as m_l3:
        
        m_l3.side_effect = RuntimeError("Missing ML dependencies")
        
        with pytest.raises(RuntimeError, match="ML dependencies"):
            _run_orchestrator(orchestrator, [Path("/tmp/a.py")], "sha", fail_fast=False, quiet=True)

def test_run_orchestrator_layer4_ml_exception():
    orchestrator = MagicMock()
    orchestrator.repo_root = Path("/tmp")
    orchestrator.contract = {"fail_threshold": 0.9, "skip_layers": []}
    orchestrator._audit = None
    
    with patch("archguard.analysis._layer_runners._run_layer1", return_value=(0.0, [])), \
         patch("archguard.analysis._layer_runners._run_layer2", return_value=(0.0, [])), \
         patch("archguard.analysis._layer_runners._run_layer3", return_value=(0.0, {}, [])), \
         patch("archguard.analysis._reinference._run_reinference"), \
         patch("archguard.analysis._orchestrator_utils._get_affected_modules", return_value=[]), \
         patch("archguard.analysis._suppression_filter._filter_suppressed", return_value=[]), \
         patch("archguard.analysis._layer_runners._run_layer4") as m_l4:
        
        m_l4.side_effect = RuntimeError("Missing ML dependencies")
        
        with pytest.raises(RuntimeError, match="ML dependencies"):
            _run_orchestrator(orchestrator, [Path("/tmp/a.py")], "sha", fail_fast=False, quiet=True)
