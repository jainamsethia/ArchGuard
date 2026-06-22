def test_cloud_client_imports_at_module_level() -> None:
    """Confirm no ImportError when loading the cloud LLM module."""
    import importlib

    # Should not raise ImportError regardless of whether anthropic is installed
    mod = importlib.import_module("archguard.llm.cloud")
    assert hasattr(mod, "CloudLLMExplainer")
    assert hasattr(mod, "_ANTHROPIC_AVAILABLE")
