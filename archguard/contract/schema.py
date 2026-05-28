"""Bundled JSON Schema for .archguard.yml configuration."""

from typing import Any

ARCHGUARD_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ArchGuard Configuration",
    "description": "Schema for .archguard.yml configuration files (v3.0).",
    "type": "object",
    "required": ["schema_version", "modules"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {
            "type": "string",
            "const": "3.0",
            "description": "Must be exactly '3.0'.",
        },
        "modules": {
            "type": "array",
            "minItems": 1,
            "description": "List of architectural modules to enforce.",
            "items": {
                "type": "object",
                "required": ["name"],
                "anyOf": [
                    {"required": ["paths"]},
                    {"required": ["module_names"]}
                ],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Human-readable module name.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Glob patterns for files belonging to this module.",
                    },
                    "module_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Dotted module names belonging to this module.",
                    },
                    "allowed_imports": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Module names this module is allowed to import from.",
                    },
                    "disallowed_imports": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Module names this module must not import from.",
                    },
                    "coupling_budget": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Maximum number of allowed cross-module couplings.",
                    },
                    "semantic_drift_threshold": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Cosine-similarity threshold for semantic drift detection.",
                    },
                    "fan_out_at_init": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Fan-out count at initialization time.",
                    },
                },
            },
        },
        "skip_layers": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["semantic", "duplication", "coupling", "boundary"]
            }
        },
        "fail_threshold": {
            "type": "number",
            "minimum": 0.0,
            "exclusiveMaximum": 1.0,
            "description": "Overall score below which the build fails. Must be < 1.0.",
        },
        "warn_threshold": {
            "type": "number",
            "description": "Overall score below which a warning is emitted.",
        },
        "model_weights_version": {
            "type": "string",
            "description": "Version identifier for model weights.",
        },
        "generated_at": {
            "type": "string",
            "description": "ISO8601 timestamp of contract generation.",
        },
        "generated_by": {
            "type": "string",
            "description": "Tool that generated the contract.",
        },
        "weights": {
            "type": "object",
            "additionalProperties": False,
            "description": "Scoring weights for each analysis layer.",
            "properties": {
                "layer1": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "layer2": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "layer3": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "layer4": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
        },
    },
}
