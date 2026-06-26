"""Basic coverage tests for LocalLLMExplainer (LOW-004, LOW-002)."""

def test_local_llm_explainer_module_imports_without_error() -> None:
    """
    Regression test for LOW-004.
    Verifies: local.py imports without error even though it has no call sites.
    """
    import archguard.llm.local as local_module
    assert hasattr(local_module, "LocalLLMExplainer")

def test_local_llm_explainer_class_has_expected_interface() -> None:
    """
    Verifies: LocalLLMExplainer has the expected method interface.
    """
    from archguard.llm.local import LocalLLMExplainer

    # Verify core interface exists without instantiating (no running Ollama needed)
    assert callable(getattr(LocalLLMExplainer, "explain", None))
