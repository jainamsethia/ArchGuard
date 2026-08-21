import json
from pathlib import Path
from unittest.mock import patch

import archguard.audit.logger as logger_module
from archguard.audit.logger import AuditLogger, read_last_run


def test_rotation_archives_rather_than_deleting(tmp_path: Path) -> None:
    """Past the size cap the log rolls over; the old entries are not destroyed.

    This used to truncate in place, keeping the last 999 lines and deleting the
    rest. For a tamper-evident security trail that inverts the priority -- the
    entries an investigation wants are the oldest ones. One generation is kept,
    so assert both halves: the pre-rotation entries are still readable in the
    archive, and the active file carries on from there.
    """
    log_file = tmp_path / "audit.jsonl"
    archive = tmp_path / "audit.jsonl.1"
    logger = AuditLogger(log_file)

    # Write the first half under a cap far above anything they will reach, so
    # the rotation point is decided here rather than by entry size.
    with patch.object(logger_module, "AUDIT_LOG_MAX_BYTES", 10_000_000):
        for i in range(10):
            logger.log("test_event", index=i)
    size_at_ten = log_file.stat().st_size
    assert not archive.exists(), "nothing should rotate below the cap"

    # Now a cap the file has already passed: the next write rotates, and only
    # that one does -- the fresh file is far below the cap again.
    with patch.object(logger_module, "AUDIT_LOG_MAX_BYTES", size_at_ten - 1):
        logger.log("test_event", index=10)

    assert archive.exists(), "rotation must keep the previous generation"
    archived = [json.loads(x) for x in archive.read_text(encoding="utf-8").splitlines()]
    current = [json.loads(x) for x in log_file.read_text(encoding="utf-8").splitlines()]

    assert [e["index"] for e in archived] == list(range(10)), "no entry lost"
    assert [e["index"] for e in current] == [10], "the active file carries on"


def test_read_last_run(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)
    logger.log("other_event", data="a")
    logger.log("analysis_run", data="b")
    logger.log("other_event", data="c")

    last_run = read_last_run(log_file)
    assert last_run is not None
    assert last_run["event"] == "analysis_run"
    assert last_run["data"] == "b"


def test_read_last_n_runs_backward_reading(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)
    for i in range(10):
        logger.log("analysis_run", data=i)

    runs = logger.read_last_n_runs(3)
    assert len(runs) == 3
    assert runs[0]["data"] == 7
    assert runs[1]["data"] == 8
    assert runs[2]["data"] == 9
