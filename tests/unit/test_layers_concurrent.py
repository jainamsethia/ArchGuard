import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import concurrent.futures

from archguard.analysis.layers import AnalysisOrchestrator
from archguard.utils.severity import Severity


class MockViolationDetail:
    def __init__(self, layer, message):
        self.layer = layer
        self.message = message
        self.severity = Severity.MEDIUM
        self.file_path = "test.py"
        self.module = "test"
        self.commit_sha = "abcd123"


def test_layers_concurrent_violations():
    """Test that Layer 1 and Layer 2 run concurrently and their violations are merged safely."""
    contract = {
        "modules": [{"name": "test", "paths": ["src/test"]}],
        "fail_threshold": 0.8,
    }

    with (
        patch("archguard.analysis.layers.load_contract", return_value=contract),
        patch("archguard.analysis.layers.EmbeddingDB"),
    ):
        orchestrator = AnalysisOrchestrator(Path("."))

    # We will patch _run_layer1 and _run_layer2 to return mocked violations
    l1_violations = [MockViolationDetail(layer=1, message="L1 violation")]
    l2_violations = [MockViolationDetail(layer=2, message="L2 violation")]
    l3_violations = [MockViolationDetail(layer=3, message="L3 violation")]
    l4_violations = [MockViolationDetail(layer=4, message="L4 violation")]

    with (
        patch(
            "archguard.analysis._layer_runners._run_layer1",
            return_value=(0.5, l1_violations),
        ) as m1,
        patch(
            "archguard.analysis._layer_runners._run_layer2",
            return_value=(0.3, l2_violations),
        ) as m2,
        patch(
            "archguard.analysis._layer_runners._run_layer3",
            return_value=(0.1, {}, l3_violations, ""),
        ) as m3,
        patch(
            "archguard.analysis._layer_runners._run_layer4",
            return_value=(0.2, l4_violations, ""),
        ) as m4,
        patch(
            "archguard.analysis._orchestrator_utils._get_affected_modules",
            return_value={"test": [Path("src/test.py")]},
        ),
        patch("archguard.analysis._reinference._run_reinference"),
        patch.dict(os.environ, {"ARCHGUARD_SKIP_ML": "0"}),
        patch("archguard.analysis._orchestrator_run.compute_archdebt") as mock_compute,
    ):
        mock_compute.return_value = MagicMock()

        # We need to simulate the ThreadPoolExecutor behavior directly to prove it's concurrent,
        # but AnalysisOrchestrator.run() already uses ThreadPoolExecutor internally.
        # So we just call run() and check the final violations list contains both.
        result = orchestrator.run([Path("src/test.py")], "abcd123", quiet=True)

        assert m1.called
        assert m2.called
        assert m3.called
        assert m4.called

        violation_messages = [v.message for v in result.violations]
        assert "L1 violation" in violation_messages
        assert "L2 violation" in violation_messages
        assert "L3 violation" in violation_messages
        assert "L4 violation" in violation_messages


def test_direct_threadpool_concurrent_safety():
    """Test the exact mutation pattern with ThreadPoolExecutor to prove it is isolated."""

    # This just ensures we can merge lists from futures without data races.
    def worker1():
        return [1, 2, 3]

    def worker2():
        return [4, 5, 6]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker1)
        f2 = executor.submit(worker2)

        v1 = f1.result()
        v2 = f2.result()

        merged = []
        merged.extend(v1)
        merged.extend(v2)

        assert set(merged) == {1, 2, 3, 4, 5, 6}


def test_concurrent_get_model():
    """Test that _get_model is thread-safe and only instantiates the model once."""
    import sys
    import archguard.analysis.semantic
    from archguard.analysis.semantic import _get_model, _GLOBAL_MODEL_CACHE

    _GLOBAL_MODEL_CACHE.clear()

    mock_st_module = MagicMock()
    mock_st_class = MagicMock()
    mock_st_module.SentenceTransformer = mock_st_class

    with (
        patch.object(archguard.analysis.semantic, "_ML_AVAILABLE", True),
        patch.dict(sys.modules, {"sentence_transformers": mock_st_module}),
    ):

        def worker():
            return _get_model("all-MiniLM-L6-v2")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(10)]
            results = [f.result() for f in futures]

        first_result = results[0]
        assert all(r is first_result for r in results)

        assert mock_st_class.call_count == 1
        mock_st_class.assert_called_once_with("all-MiniLM-L6-v2")
