"""Turn detected module communities into a written ``.archguard.yml``.

Extracted from ``archguard.cli._init_contract``. The contract-building logic is
unchanged; what is gone is the CLI scaffolding around it -- the ``typer.Context``
parameter, ``rich`` printing, the ``.archguard-init-summary.md`` writer (the
website generates into a throwaway clone), and the interactive profile prompt,
which asked the user a question and then discarded both the answer and the
``contract`` dict it built from it.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from archguard.config import EMBEDDING_CACHE_FILE
from archguard.contract.validator import validate_contract
from archguard.contract.writer import _infer_path, _model_weights_version

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


def compute_module_embeddings(
    communities: dict[str, list[str]],
    repo_root: Path,
    python_files: list[str],
) -> dict[str, Any]:
    """Precompute per-module embedding centroids into the embedding cache.

    Optional: returns a zeroed summary when the ML extras are not installed.
    The website does not call this -- Layer 3 computes what it needs at analysis
    time -- but the capability is preserved for a worker that wants the cache
    warm before the first scan.
    """
    try:
        from sentence_transformers import SentenceTransformer  # lazy import
    except ImportError:
        logger.info(
            "sentence-transformers is not installed; skipping embedding precompute"
        )
        return {
            "modules_embedded": len(communities),
            "total_functions_embedded": 0,
            "model_name": "none",
        }

    import hashlib

    import numpy as np

    from archguard.cache.db import EmbeddingDB

    model = SentenceTransformer("all-MiniLM-L6-v2")
    db_path = repo_root / EMBEDDING_CACHE_FILE
    total_embedded = 0

    with EmbeddingDB(db_path) as db:
        for module_name, files in communities.items():
            texts: list[str] = []
            file_paths: list[str] = []

            for f in files:
                full_path = repo_root / f
                try:
                    texts.append(full_path.read_text(errors="replace"))
                    file_paths.append(f)
                except OSError:
                    continue

            if not texts:
                continue

            embeddings = model.encode(texts, batch_size=32)
            now_iso = datetime.now(UTC).isoformat()

            for i, (fp, text) in enumerate(zip(file_paths, texts, strict=True)):
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                emb_bytes = np.array(embeddings[i], dtype=np.float32).tobytes()
                db._conn.execute(
                    """INSERT OR REPLACE INTO embeddings
                       (file_path, function_name, embedding,
                        content_hash, model_name, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (fp, "__module__", emb_bytes, content_hash,
                     "all-MiniLM-L6-v2", now_iso),
                )

            centroid = np.mean(embeddings, axis=0)
            centroid_bytes = np.array(centroid, dtype=np.float32).tobytes()
            centroid_hash = hashlib.sha256(centroid_bytes).hexdigest()
            db._conn.execute(
                """INSERT OR REPLACE INTO module_centroids
                   (module_name, centroid, content_hash, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (module_name, centroid_bytes, centroid_hash, now_iso),
            )
            db._conn.commit()
            total_embedded += len(texts)

    return {
        "modules_embedded": len(communities),
        "total_functions_embedded": total_embedded,
        "model_name": "all-MiniLM-L6-v2",
    }


def contract_module_paths(communities: dict[str, list[str]]) -> dict[str, list[str]]:
    """The module->paths mapping the generated contract will actually carry.

    Each module is addressed by the single prefix ``_infer_path`` derives, which
    is exactly what ``_get_module_paths`` hands Layer 2 at analysis time.
    """
    return {name: [_infer_path(files)] for name, files in communities.items()}


def compute_fan_outs(
    communities: dict[str, list[str]],
    repo_root: Path,
) -> dict[str, int]:
    """Compute fan_out_at_init for each module using the import parser.

    Deliberately measures against the *contract's* module paths (one inferred
    prefix per module) and via ``parse_repo``, mirroring what Layer 2 does at
    analysis time. Measuring against the parent directories of each community's
    files instead -- the previous behaviour -- counted a different set of source
    files, so the ``fan_out_at_init`` recorded in the contract disagreed with the
    fan-out the module was then graded on by 2-3x on real repositories (httpie
    recorded 21, graded on 11). The recorded number is presented to users as
    evidence, so it has to be the number that was actually used.
    """
    from archguard.analysis.coupling import compute_fan_out
    from archguard.analysis.parser import ImportParser

    module_paths = contract_module_paths(communities)

    # parse_repo, not a hand-rolled walk over python_files: it applies the same
    # skip-list and failure handling the analysis-time parse does.
    parser = ImportParser()
    edges = parser.parse_repo(repo_root, module_paths, allow_partial=True).edges

    return {name: compute_fan_out(edges, name, module_paths) for name in communities}


def build_contract(
    communities: dict[str, list[str]],
    fan_outs: dict[str, int],
    fallback_used: bool = False,
    fallback_reason: str = "",
    threshold_profile: str | None = None,
) -> dict[str, Any]:
    """Build the contract dictionary.

    Thresholds come from one of two policies:

    ``threshold_profile=None``
        Budgets are derived from the fan-out measured during *this* run:
        ``max(3, ceil(fan_out * 1.5))``. This is a "do not get worse than today"
        baseline -- deliberately self-referential, because the team generating
        the contract would be enforcing it against their own future changes.

    ``threshold_profile="ci"`` (or another name from ``archguard.profiles``)
        Budgets come from fixed policy instead. Required for one-off analysis of
        a repository nobody is going to enforce this contract against: grading a
        repo against thresholds derived from its own current state is
        tautological, so it can only ever pass.
    """
    use_fixed_thresholds = threshold_profile is not None

    louvain_modules = []
    for name, files in communities.items():
        fan_out = fan_outs.get(name, 0)
        module: dict[str, Any] = {
            "name": name,
            "path": _infer_path(files),
            # Kept in both modes: this is a *measurement*, not a threshold, and
            # is useful context even when it is not what the module is graded on.
            "fan_out_at_init": fan_out,
        }
        if not use_fixed_thresholds:
            module["coupling_budget"] = max(3, math.ceil(fan_out * 1.5))
            module["semantic_drift_threshold"] = 0.25
        # else: left unset on purpose -- apply_profile() below fills these in,
        # and it only populates keys that are absent.
        louvain_modules.append(module)

    contract: dict[str, Any] = {
        "version": "3.0",
        "model_weights_version": _model_weights_version(),
        "generated_at": datetime.now(UTC).isoformat(),
        # NOTE: consumers detect the heuristic by testing for the substring
        # "fallback" -- keep it present.
        "generated_by": (
            "archguard init (directory heuristic fallback"
            + (f": {fallback_reason}" if fallback_reason else "")
            + ")"
        )
        if fallback_used
        else "archguard init",
        "modules": louvain_modules,
        "fail_threshold": 0.75,
        "warn_threshold": 0.50,
    }

    if use_fixed_thresholds:
        from archguard.profiles.defaults import apply_profile

        # Circular module dependencies are the one wrong-direction-import signal
        # that needs no human-authored policy: if lib imports extra and extra
        # imports lib, the cycle is a defect whatever the intended layering.
        #
        # Note what this deliberately does NOT do: synthesise `disallowed_imports`
        # entries. A cycle proves at least one edge in it is wrong, but not
        # *which* one -- naming a specific edge as forbidden would be a guess
        # presented as a rule, which is the failure mode this whole path exists
        # to avoid. The fitness function reports the cycle as a whole instead,
        # naming the real path it found.
        contract["fitness_functions"] = [
            {
                "name": "no_circular_deps",
                "rule": "graph.cycles == 0",
                "severity": "critical",
                "rationale": (
                    "Circular dependencies between modules make them impossible "
                    "to build, test, or reason about independently."
                ),
            }
        ]

        # Recorded so downstream consumers (and the dashboard) can state what the
        # score was actually graded against rather than presenting a bare number.
        contract["profile"] = threshold_profile
        # apply_profile also derives fail_threshold from the profile's
        # min_health_score; warn_threshold must stay strictly below it or every
        # WARN-band run would be reported as a pass.
        apply_profile(contract, str(threshold_profile))
        fail_t = cast(float, contract.get("fail_threshold", 0.75))
        contract["warn_threshold"] = round(fail_t / 2.0, 4)

    return contract


def write_contract(contract: dict[str, Any], output: Path) -> int:
    """Validate *contract* and write it atomically to *output*.

    Returns the number of modules written.
    """
    validate_contract(contract)

    dir_path = output.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".yml", dir=str(dir_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            yaml.dump(contract, tmp, default_flow_style=False, sort_keys=False)
        os.replace(tmp_name, str(output))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

    logger.info("Contract written to %s", output)
    return len(cast(list[Any], contract.get("modules", [])))
