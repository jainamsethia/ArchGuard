"""The fixed set of violation kinds the analysis pipeline can produce.

Every violation carries a ``kind`` plus a ``metrics`` dict of the raw numbers
behind it. Two consumers depend on those numbers being real fields rather than
text inside ``message``:

* ranking, which orders violations within a severity tier by each kind's own
  natural metric; and
* the plain-language templates, which interpolate the numbers into a
  jargon-free sentence.

Both would otherwise have to parse the formatted message back apart.
"""

from __future__ import annotations

from typing import Final

# Layer 1 -- an import the contract forbids (or omits from allowed_imports).
IMPORT_BOUNDARY: Final = "import_boundary"
# Layer 2 -- a module depending on more other things than its budget allows.
FAN_OUT: Final = "fan_out"
# Layer 3 -- a module's meaning drifting from its recorded baseline.
SEMANTIC_DRIFT: Final = "semantic_drift"
# Layer 4 -- the same code appearing in more than one module.
DUPLICATION: Final = "duplication"
# Not a layer violation: a failed critical fitness gate (currently the
# dependency-cycle check). Ranked above the layers -- see ranking.py.
DEPENDENCY_CYCLE: Final = "dependency_cycle"

ALL_KINDS: Final[tuple[str, ...]] = (
    IMPORT_BOUNDARY,
    FAN_OUT,
    SEMANTIC_DRIFT,
    DUPLICATION,
    DEPENDENCY_CYCLE,
)


def over_budget_ratio(metrics: dict[str, float]) -> float:
    """How far a fan-out violation exceeds its budget, as a fraction.

    ``fan_out=11, budget=10`` -> ``0.1`` (10% over). This is the measurement the
    violation was raised on, not a score derived for ranking: Layer 2 computes
    the identical quantity as its coupling delta.
    """
    budget = metrics.get("budget", 0.0)
    fan_out = metrics.get("fan_out", 0.0)
    if budget <= 0:
        return fan_out
    return (fan_out - budget) / budget
