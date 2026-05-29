def test_cloud_client_imports_at_module_level():
    """Confirm no ImportError when loading the module."""
    import archguard.llm.cloud  # should not raise
    import archguard.github.client  # should not raise
