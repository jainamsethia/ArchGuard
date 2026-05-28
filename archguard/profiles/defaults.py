"""Default configuration profiles for ArchGuard."""

from typing import Any

PROFILES = {
    "strict": {
        "thresholds": {
            "max_instability": 0.6,
            "max_coupling": 5,
            "max_duplication": 0.05,
            "min_cohesion": 0.8,
            "min_health_score": 85,
        },
        "fail_on_violation": True,
        "description": "Production-grade enforcement. Suitable for mature codebases.",
    },
    "lenient": {
        "thresholds": {
            "max_instability": 0.9,
            "max_coupling": 15,
            "max_duplication": 0.20,
            "min_cohesion": 0.5,
            "min_health_score": 60,
        },
        "fail_on_violation": False,
        "description": "Minimal enforcement. Good for greenfield or legacy projects.",
    },
    "ci": {
        "thresholds": {
            "max_instability": 0.75,
            "max_coupling": 10,
            "max_duplication": 0.10,
            "min_cohesion": 0.65,
            "min_health_score": 75,
        },
        "fail_on_violation": True,
        "description": "Balanced enforcement for CI pipelines.",
    },
}

def apply_profile(contract: dict[str, Any], profile_name: str) -> dict[str, Any]:
    """Apply a named profile's thresholds to the given contract."""
    if profile_name not in PROFILES:
        return contract

    profile = PROFILES[profile_name]
    thresholds = profile["thresholds"]

    # Global thresholds
    if "min_health_score" in thresholds:
        contract["fail_threshold"] = thresholds["min_health_score"] / 100.0

    # Module thresholds
    if "modules" in contract:
        for module in contract["modules"]:
            if "max_coupling" in thresholds and "coupling_budget" not in module:
                module["coupling_budget"] = thresholds["max_coupling"]
            if "max_duplication" in thresholds and "duplication_threshold" not in module:
                module["duplication_threshold"] = thresholds["max_duplication"]
            if "min_cohesion" in thresholds and "semantic_drift_threshold" not in module:
                module["semantic_drift_threshold"] = 1.0 - thresholds["min_cohesion"]
                
    return contract
