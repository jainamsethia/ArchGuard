"""Job-id validation and workspace paths, importable without importing the app.

These lived in ``app.py``, which every route module then had to import -- while
``app.py`` imports every route module. The cycle is what the import-ordering
workaround in ``routes/__init__.py`` was holding at bay, and it is also why
mypy could not determine the type of four routers: it gives up resolving names
through a cycle.

Nothing here depends on the FastAPI app, so nothing here needs to be there.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException, Query

#: A job id in a query string. Validated at the edge as well as in
#: ``get_target_path``: this one keeps a malformed value out of the handler,
#: that one keeps it out of a filesystem path.
JobIdQuery = Annotated[
    str | None,
    Query(pattern=r"^[a-f0-9\-]{36,64}$", max_length=64),
]

_JOB_ID_RE = re.compile(r"[a-f0-9\-]{36,64}")


def get_target_path(job_id: str | None = None) -> Path:
    """The clone directory for a job, or the cwd when no job is named.

    The id is re-validated here even though the query annotation already
    checked it, because this function also serves callers that did not come
    through a query parameter -- and it builds a filesystem path, which is not
    somewhere to find out that the validation was somebody else's job.
    """
    if not job_id:
        return Path.cwd()

    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id format")

    tmp = Path(tempfile.gettempdir())
    path = (tmp / f"archguard-{job_id}" / "repo").resolve()
    # Resolve first, then confirm containment: a traversal in the id would
    # otherwise escape the workspace root.
    expected_prefix = (tmp / f"archguard-{job_id}").resolve()
    if not str(path).startswith(str(expected_prefix)):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    if path.exists():
        return path

    # 410 rather than falling back to the cwd, which would silently analyse the
    # server's own working directory and report it as the user's repository.
    raise HTTPException(
        status_code=410,
        detail="Analysis workspace expired. Results are available from the stored run.",
    )
