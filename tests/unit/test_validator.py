"""Unit tests for archguard.contract.validator."""

from __future__ import annotations

from typing import Any

import pytest

from archguard.contract.validator import ContractValidationError, validate_contract


class TestValidateContract:
    """Tests for validate_contract()."""

    def test_valid_minimal_contract(self, minimal_contract: dict[str, Any]) -> None:
        """Valid minimal contract (schema_version + 1 module) raises no error."""
        validate_contract(minimal_contract)  # Should not raise

    def test_missing_schema_version(self) -> None:
        """Missing schema_version raises ContractValidationError."""
        data: dict[str, Any] = {
            "modules": [{"name": "core", "paths": ["src/"]}],
        }
        with pytest.raises(ContractValidationError) as exc_info:
            validate_contract(data)
        assert any("schema_version" in e for e in exc_info.value.errors)

    def test_fail_threshold_exclusive_max(self) -> None:
        """fail_threshold = 1.0 is invalid (exclusive max < 1.0)."""
        data: dict[str, Any] = {
            "schema_version": "3.0",
            "modules": [{"name": "core", "paths": ["src/"]}],
            "fail_threshold": 1.0,
        }
        with pytest.raises(ContractValidationError) as exc_info:
            validate_contract(data)
        assert any("fail_threshold" in e for e in exc_info.value.errors)

    def test_fail_threshold_valid(self) -> None:
        """fail_threshold = 0.8 is valid."""
        data: dict[str, Any] = {
            "schema_version": "3.0",
            "modules": [{"name": "core", "paths": ["src/"]}],
            "fail_threshold": 0.8,
        }
        validate_contract(data)  # Should not raise

    def test_empty_modules_array(self) -> None:
        """Empty modules array raises ContractValidationError."""
        data: dict[str, Any] = {
            "schema_version": "3.0",
            "modules": [],
        }
        with pytest.raises(ContractValidationError) as exc_info:
            validate_contract(data)
        assert any("modules" in e for e in exc_info.value.errors)

    def test_module_missing_name(self) -> None:
        """Module without a name raises ContractValidationError."""
        data: dict[str, Any] = {
            "schema_version": "3.0",
            "modules": [{"paths": ["src/"]}],
        }
        with pytest.raises(ContractValidationError) as exc_info:
            validate_contract(data)
        assert any("name" in e for e in exc_info.value.errors)
