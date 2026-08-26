"""Unit tests for archguard.contract.validator."""

from __future__ import annotations

from typing import Any

import pytest

from archguard.contract.validator import validate_contract
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

    def test_duplication_threshold_valid(self) -> None:
        """duplication_threshold on a module is valid."""
        data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"name": "core", "path": "src/", "duplication_threshold": 0.1}],
        }
        validate_contract(data)  # Should not raise
