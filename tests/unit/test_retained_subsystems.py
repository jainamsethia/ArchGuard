"""Guards for subsystems kept deliberately, not by accident.

After the CLI was removed these modules have no production caller. That makes
them look exactly like dead code to anyone reading an import graph, and the
obvious next step is to delete them. They are retained on purpose:

* ``archguard.cache.incremental`` -- content-hash change detection, the basis
  for incremental re-analysis. Without it every re-scan of a watched repository
  recomputes everything, which is what decides whether scheduled scans are
  affordable at all.
* ``archguard.alerting`` -- trend detection and alert delivery for watched
  repositories.
* ``archguard.utils.url_validator`` -- the SSRF guard that stands between a
  user-supplied webhook URL and the internal network. Required by the above.

If a future change genuinely drops those features, delete this file in the same
commit and say so. Do not delete these modules to make an unused-code report
quieter.
"""

from __future__ import annotations

import importlib

import pytest

RETAINED = [
    "archguard.cache.incremental",
    "archguard.alerting.trend_detector",
    "archguard.alerting.webhooks",
    "archguard.utils.url_validator",
]


@pytest.mark.parametrize("module_name", RETAINED)
def test_retained_module_still_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


def test_incremental_analysis_still_partitions_by_content_hash(tmp_path) -> None:
    """The behaviour that makes it worth keeping, not merely its importability."""
    from archguard.cache.incremental import (
        FileRecord,
        compute_hash,
        get_changed_files,
        save_cache,
    )

    stable = tmp_path / "stable.py"
    edited = tmp_path / "edited.py"
    stable.write_text("import os\n")
    edited.write_text("import os\n")

    save_cache(
        tmp_path,
        {
            "stable.py": FileRecord("stable.py", compute_hash(stable), "2020-01-01"),
            "edited.py": FileRecord("edited.py", compute_hash(edited), "2020-01-01"),
        },
    )
    edited.write_text("import os\nimport sys\n")

    changed, unchanged = get_changed_files([stable, edited], tmp_path)
    assert [p.name for p in changed] == ["edited.py"]
    assert [p.name for p in unchanged] == ["stable.py"]
