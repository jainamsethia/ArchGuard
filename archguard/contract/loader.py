"""YAML loading and multi-file merging for ArchGuard contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from archguard.config import ARCHGUARD_CONFIG_DIR, ARCHGUARD_CONFIG_FILE
from archguard.contract.validator import validate_contract
from archguard.utils.errors import ConfigError


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a single YAML file, raising ConfigError on parse failures."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data: Any = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Expected a mapping in {path}, got {type(data).__name__}")

    return data


def _merge_contracts(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple config dicts: modules are concatenated, top-level keys from first file win."""
    if not configs:
        raise ConfigError("No configuration files to merge")

    merged: dict[str, Any] = dict(configs[0])
    merged_modules: list[Any] = list(merged.get("modules", []))

    for extra in configs[1:]:
        merged_modules.extend(extra.get("modules", []))

    merged["modules"] = merged_modules
    return merged


def load_contract(repo_root: Path) -> dict[str, Any]:
    """Load .archguard.yml OR all .archguard/*.yml files.

    Priority: single file > directory glob.
    Merges multi-file configs by concatenating module lists; top-level keys
    from the first file win.

    Raises:
        ConfigError: If no config is found or YAML parsing fails.
        ContractError: If the merged config fails schema validation.
    """
    single_file = repo_root / ARCHGUARD_CONFIG_FILE
    config_dir = repo_root / ARCHGUARD_CONFIG_DIR

    if single_file.is_file():
        data = _load_yaml(single_file)
        validate_contract(data)
        return data

    if config_dir.is_dir():
        yml_files = sorted(config_dir.glob("*.yml"))
        if yml_files:
            configs = [_load_yaml(f) for f in yml_files]
            merged = _merge_contracts(configs)
            validate_contract(merged)
            return merged

    raise ConfigError(
        f"No ArchGuard configuration found. "
        f"Create {ARCHGUARD_CONFIG_FILE} or add .yml files to {ARCHGUARD_CONFIG_DIR}/."
    )
