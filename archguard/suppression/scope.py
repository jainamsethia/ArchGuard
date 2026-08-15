"""Where a repository's suppressions live, for callers with no persistent checkout.

The CLI keys suppressions by the checkout you are standing in:
``<repo_root>/.archguard-cache/suppressions.jsonl``. That works because the
checkout *is* the durable thing -- it is still there next time you run
``archguard analyze``.

The dashboard has no such anchor. Every scan clones into a fresh temp directory
that is deleted when the job ends, so a suppression stored relative to the
analysed tree cannot survive to the next scan. Storing it per ``job_id`` does not
help either: a re-scan of the same repository gets a new job id, so last time's
suppressions would never be found. Since re-suppressing the same finding on every
scan is exactly what makes the feature useless, the durable key has to be the
repository itself.

``owner/repo`` is that key. ``parse_github_url`` already collapses the
https/ssh/``.git``/trailing-slash spellings of one repository onto a single
pair, so the same project submitted in different URL forms resolves to one store.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Mirrors the character class parse_github_url accepts, minus anything that
# could escape a directory. Applied again here rather than trusted, because this
# value becomes a filename.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")

SUPPRESSIONS_DIRNAME = "suppressions"


def _safe_segment(value: str) -> str | None:
    """Return *value* if it is safe to use as part of a filename, else None."""
    value = value.strip()
    if not value or not _SAFE_SEGMENT.match(value):
        return None
    # "." and ".." are matched by the pattern but must never reach a path.
    if set(value) <= {"."}:
        return None
    return value.lower()


def repo_slug(repo_url: str) -> str:
    """A stable, filesystem-safe identifier for a repository.

    ``https://github.com/Owner/Repo.git`` and ``git@github.com:owner/repo`` both
    yield ``owner__repo``. Anything unparseable falls back to a hash of the URL,
    which is still stable for that exact string -- better than colliding two
    different repositories onto one store.
    """
    from archguard.dashboard.routes.jobs import parse_github_url

    try:
        owner, name = parse_github_url(repo_url)
    except ValueError:
        owner = name = ""

    safe_owner = _safe_segment(owner) if owner else None
    safe_name = _safe_segment(name) if name else None
    if safe_owner and safe_name:
        return f"{safe_owner}__{safe_name}"

    digest = hashlib.sha256(repo_url.strip().encode("utf-8")).hexdigest()[:16]
    return f"url-{digest}"


def suppression_path_for_repo(base_dir: Path, repo_url: str) -> Path:
    """Path to the suppression file for *repo_url* under *base_dir*."""
    return base_dir / SUPPRESSIONS_DIRNAME / f"{repo_slug(repo_url)}.jsonl"
