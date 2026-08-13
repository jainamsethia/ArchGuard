"""Bundled JSON Schema for .archguard.yml configuration."""

from typing import Any

ARCHGUARD_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ArchGuard Configuration",
    "description": "Schema for .archguard.yml configuration files (v3.0).",
    "type": "object",
    "required": ["version", "modules"],
    "additionalProperties": False,
    "properties": {
        "version": {
            "type": "string",
            "const": "3.0",
        },
        "modules": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name"],
                "anyOf": [{"required": ["path"]}, {"required": ["module_names"]}],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "path": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        ]
                    },
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "allowed_imports": {"type": "array", "items": {"type": "string"}},
                    "disallowed_imports": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "coupling_budget": {"type": "integer", "minimum": 0},
                    "semantic_drift_threshold": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "duplication_threshold": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Maximum allowed duplication ratio within the module.",
                    },
                    "fan_out_at_init": {"type": "integer", "minimum": 0},
                },
            },
        },
        "skip_layers": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["semantic", "duplication", "coupling", "boundary"],
            },
        },
        "fail_threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "warn_threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "model_weights_version": {"type": "string"},
        "generated_at": {"type": "string"},
        "generated_by": {"type": "string"},
        "profile": {
            "type": "string",
            "description": (
                "Name of the threshold profile the module budgets came from "
                "(see archguard.profiles.defaults). Present when thresholds are "
                "fixed policy rather than derived from the repository's own "
                "measurements at generation time."
            ),
        },
        "weights": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "layer1": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "layer2": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "layer3": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "layer4": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "fitness_functions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "rule"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "rule": {"type": "string", "minLength": 1},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "warn", "info"],
                    },
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}
