import pytest
from archguard.utils.errors import (
    ArchGuardError,
    ConfigError,
    InternalError,
    ContractError,
    AnalysisError,
    LLMError,
)
from archguard.contract.validator import validate_contract
from archguard.llm.local import LocalLLMExplainer
from archguard.analysis.semantic import SemanticAnalyzer
from archguard.cache.embeddings import EmbeddingCache
from archguard.cache.db import EmbeddingDB

def test_exception_hierarchy():
    assert issubclass(ConfigError, ArchGuardError)
    assert issubclass(InternalError, ArchGuardError)
    assert issubclass(ContractError, ArchGuardError)
    assert issubclass(AnalysisError, ArchGuardError)
    assert issubclass(LLMError, ArchGuardError)

def test_contract_error_raised_on_invalid_yaml():
    invalid_contract = {
        "modules": [
            {"name": "test"} # missing paths/module_names
        ]
    }
    with pytest.raises(ContractError) as exc_info:
        validate_contract(invalid_contract)
    assert "Contract validation failed" in str(exc_info.value)
    assert exc_info.value.exit_code == 3

def test_llm_error_raised_on_api_failure(monkeypatch):
    explainer = LocalLLMExplainer(base_url="http://invalid-url-that-does-not-exist:9999")
    
    # We monkeypatch with_retry to just call once so we don't wait
    import archguard.utils.retry
    monkeypatch.setattr(archguard.utils.retry, "with_retry", lambda **kwargs: lambda func: func)
    
    # Reload local explainer to re-apply the mocked decorator? 
    # Actually, the decorator is applied at import time, so we just monkeypatch httpx.post
    import httpx
    def mock_post(*args, **kwargs):
        raise Exception("Mocked unexpected failure")
    monkeypatch.setattr(httpx, "post", mock_post)

    # _call_api should raise LLMError
    with pytest.raises(LLMError) as exc_info:
        explainer._call_api("url", "prompt")
    
    assert "Unexpected error connecting to ollama" in str(exc_info.value)
    assert exc_info.value.exit_code == 5
    
def test_analysis_error_raised_on_empty_embeddings(tmp_path):
    db = EmbeddingDB(tmp_path / "test.db")
    cache = EmbeddingCache(db)
    analyzer = SemanticAnalyzer(cache)
    
    with pytest.raises(AnalysisError) as exc_info:
        analyzer.compute_centroid({})
    
    assert "Cannot compute centroid of empty embeddings" in str(exc_info.value)
    assert exc_info.value.exit_code == 4
