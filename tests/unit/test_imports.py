def test_cloud_client_imports_at_module_level() -> None:
    """Confirm no ImportError when loading the cloud LLM module."""
    import importlib

    # Gemini is reached over plain HTTP (httpx, a core dependency), so this
    # module no longer guards an optional vendor SDK -- it must simply import.
    mod = importlib.import_module("archguard.llm.cloud")
    assert hasattr(mod, "CloudLLMExplainer")
    assert hasattr(mod, "PRIMARY_MODEL")
    assert hasattr(mod, "FALLBACK_MODEL")
