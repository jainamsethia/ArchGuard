def test_the_llm_client_imports_at_module_level() -> None:
    """Confirm no ImportError when loading the LLM client.

    Repointed from `archguard.llm.cloud`, which was removed: its only consumer
    was `contract.llm_inference`, which nothing outside the tests imported.
    `llm.gemini` is what the Advisor and remediation -- both live website
    features -- actually talk to.
    """
    import importlib

    # Gemini is reached over plain HTTP (httpx, a core dependency), so this
    # module guards no optional vendor SDK -- it must simply import.
    mod = importlib.import_module("archguard.llm.gemini")
    assert hasattr(mod, "DEFAULT_PRIMARY_MODEL")
    assert hasattr(mod, "DEFAULT_FALLBACK_MODEL")
