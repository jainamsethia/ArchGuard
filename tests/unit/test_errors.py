import pytest

np = pytest.importorskip("numpy", reason="ML extras not installed")


@pytest.fixture(autouse=True)
def patch_ml_available(monkeypatch):
    monkeypatch.setattr("archguard.analysis.semantic._ML_AVAILABLE", True)


from archguard.analysis.semantic import SemanticAnalyzer
from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache
from archguard.contract.validator import validate_contract
from archguard.utils.errors import (
    AnalysisError,
    ArchGuardError,
    ConfigError,
    ContractError,
    InternalError,
    LLMError,
)


def test_exception_hierarchy():
    assert issubclass(ConfigError, ArchGuardError)
    assert issubclass(InternalError, ArchGuardError)
    assert issubclass(ContractError, ArchGuardError)
    assert issubclass(AnalysisError, ArchGuardError)
    assert issubclass(LLMError, ArchGuardError)


def test_contract_error_raised_on_invalid_yaml():
    invalid_contract = {
        "modules": [
            {"name": "test"}  # missing paths/module_names
        ]
    }
    with pytest.raises(ContractError) as exc_info:
        validate_contract(invalid_contract)
    assert "Contract validation failed" in str(exc_info.value)
    assert exc_info.value.exit_code == 2


def test_analysis_error_raised_on_empty_embeddings(tmp_path):
    db = EmbeddingDB(tmp_path / "test.db")
    cache = EmbeddingCache(db)
    analyzer = SemanticAnalyzer(cache)

    with pytest.raises(AnalysisError) as exc_info:
        analyzer.compute_centroid({})

    assert "Cannot compute centroid of empty embeddings" in str(exc_info.value)
    assert exc_info.value.exit_code == 3


def test_config_error_exit_code():
    e = ConfigError("bad config")
    assert e.exit_code == 2


def test_contract_error_exit_code():
    e = ContractError("bad contract")
    assert e.exit_code == 2


def test_analysis_error_exit_code():
    e = AnalysisError("analysis failed")
    assert e.exit_code == 3


def test_llm_error_exit_code():
    e = LLMError("llm failed")
    assert e.exit_code == 6
