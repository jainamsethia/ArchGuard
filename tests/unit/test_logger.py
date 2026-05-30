import json
from pathlib import Path
from unittest.mock import patch
from archguard.audit.logger import AuditLogger, read_last_run
import archguard.audit.logger as logger_module


def test_audit_logger_truncation(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"

    # Patch constants in the module where they are used
    with (
        patch.object(logger_module, "AUDIT_LOG_MAX_BYTES", 10),
        patch.object(logger_module, "AUDIT_LOG_MAX_ENTRIES", 1000),
    ):
        logger = AuditLogger(log_file)
        for i in range(1001):
            logger.log("test_event", index=i)

    # Read the file
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 1000
    # The oldest (index 0) should be trimmed, so the first one is index 1
    first_event = json.loads(lines[0])
    assert first_event["index"] == 1
    last_event = json.loads(lines[-1])
    assert last_event["index"] == 1000


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
