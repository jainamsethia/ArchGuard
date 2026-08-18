"""Unit tests for archguard.contract.validator."""

from __future__ import annotations

from typing import Any

import pytest

from archguard.contract.validator import validate_contract
from archguard.llm.cloud import CloudLLMExplainer
from archguard.utils.errors import ContractError


class TestValidateContract:
    """Tests for validate_contract()."""

    def test_valid_minimal_contract(self, minimal_contract: dict[str, Any]) -> None:
        """Valid minimal contract (version + 1 module) raises no error."""
        validate_contract(minimal_contract)  # Should not raise

    def test_missing_version(self) -> None:
        """Missing version raises ContractValidationError."""
        data: dict[str, Any] = {
            "modules": [{"name": "core", "path": "src/"}],
        }
        with pytest.raises(ContractError) as exc_info:
            validate_contract(data)
        assert "version" in str(exc_info.value)

    def test_fail_threshold_exclusive_max(self) -> None:
        """fail_threshold > 1.0 is invalid."""
        data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"name": "core", "path": "src/"}],
            "fail_threshold": 1.1,
        }
        with pytest.raises(ContractError) as exc_info:
            validate_contract(data)
        assert "fail_threshold" in str(exc_info.value)

    def test_fail_threshold_valid(self) -> None:
        """fail_threshold = 0.8 is valid."""
        data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"name": "core", "path": "src/"}],
            "fail_threshold": 0.8,
        }
        validate_contract(data)  # Should not raise

    def test_empty_modules_array(self) -> None:
        """Empty modules array raises ContractValidationError."""
        data: dict[str, Any] = {
            "version": "3.0",
            "modules": [],
        }
        with pytest.raises(ContractError) as exc_info:
            validate_contract(data)
        assert "modules" in str(exc_info.value)

    def test_module_missing_name(self) -> None:
        """Module without a name raises ContractValidationError."""
        data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"path": "src/"}],
        }
        with pytest.raises(ContractError) as exc_info:
            validate_contract(data)
        assert "name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_llm_generation_validates_contract_success(self) -> None:
        """LLM generated contract should be validated and succeed if it has 'version': '3.0'."""
        import json
        from unittest.mock import patch
        from archguard.contract.llm_inference import generate_contract_from_llm
        from pathlib import Path

        valid_contract = {
            "version": "3.0",
            "modules": [
                {
                    "name": "core",
                    "path": "src/",
                    "coupling_budget": 10,
                    "semantic_drift_threshold": 0.2,
                    "allowed_imports": [],
                    "disallowed_imports": [],
                }
            ],
            "fail_threshold": 0.75,
            "warn_threshold": 0.50,
        }

        # See the note in the failure-path test below: patch the _call_api
        # seam rather than a fabricated SDK module.
        with patch.object(
            CloudLLMExplainer,
            "_call_api",
            autospec=True,
            return_value=(json.dumps(valid_contract), "stop"),
        ):
            with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
                result = await generate_contract_from_llm(Path("."))
                assert result["version"] == "3.0"

    @pytest.mark.asyncio
    async def test_llm_generation_validates_contract_fails(self) -> None:
        """LLM generated contract should fail validation if it returns 'schema_version': '3.0'."""
        import json
        from unittest.mock import patch
        from archguard.contract.llm_inference import generate_contract_from_llm
        from pathlib import Path

        invalid_contract = {
            "schema_version": "3.0",
            "modules": [
                {
                    "name": "core",
                    "paths": ["src/"],
                    "description": "core",
                    "coupling_budget": 10,
                    "semantic_drift_threshold": 0.2,
                    "allowed_imports": [],
                    "disallowed_imports": [],
                }
            ],
            "fail_threshold": 0.75,
            "warn_threshold": 0.50,
        }

        # generate_contract_from_llm calls CloudLLMExplainer._call_api, so
        # patching that is both simpler and closer to the seam than faking a
        # whole vendor SDK module.
        with patch.object(
            CloudLLMExplainer,
            "_call_api",
            autospec=True,
            return_value=(json.dumps(invalid_contract), "stop"),
        ):
            with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
                with pytest.raises(
                    ValueError, match="LLM generated an invalid contract"
                ):
                    await generate_contract_from_llm(Path("."))

    def test_duplication_threshold_valid(self) -> None:
        """duplication_threshold on a module is valid."""
        data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"name": "core", "path": "src/", "duplication_threshold": 0.1}],
        }
        validate_contract(data)  # Should not raise
