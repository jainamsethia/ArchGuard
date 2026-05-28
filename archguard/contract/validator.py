"""JSON Schema Draft 7 validation for ArchGuard contracts."""

from __future__ import annotations

from jsonschema import Draft7Validator

from archguard.contract.schema import ARCHGUARD_SCHEMA


class ContractValidationError(Exception):
    """Raised when a contract fails schema validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Contract validation failed with {len(errors)} error(s)")


def validate_contract(data: dict[str, object]) -> None:
    """Validate a contract dict against the ArchGuard JSON Schema.

    Raises ContractValidationError with all error messages if invalid.
    """
    validator = Draft7Validator(ARCHGUARD_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        messages = [
            f"{'.'.join(str(p) for p in e.path) or 'root'}: {e.message}"
            for e in errors
        ]
        from archguard.utils.errors import ContractError
        raise ContractError(f"Contract validation failed: {messages}")
