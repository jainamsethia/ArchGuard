from datetime import UTC


def test_read_last_run_finds_written_event():
    """read_last_run must find events written by analyze_cmd."""
    import os
    import tempfile
    from pathlib import Path

    from archguard.audit.logger import AuditLogger
    from archguard.config import AUDIT_EVENT_ANALYSIS

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "audit.jsonl")
        logger = AuditLogger(Path(log_path))
        logger.log(event=AUDIT_EVENT_ANALYSIS, score=0.3, violations=2)

        result = logger.read_last_run()
        assert result is not None, "read_last_run must return the event we just wrote"
        assert result.get("event") == AUDIT_EVENT_ANALYSIS


def test_analysis_orchestrator_logs_parse_failure(tmp_path):
    from unittest.mock import patch

    from archguard.analysis.layers import AnalysisOrchestrator
    from archguard.audit.logger import AuditLogger

    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)

    bad_py = tmp_path / "bad.py"
    bad_py.write_text("def f(:\n    pass\n")

    # Create dummy config so load_contract doesn't fail
    config_yml = tmp_path / ".archguard.yml"
    config_yml.write_text("modules:\n  - name: dummy\n    path: .\n")

    orchestrator = AnalysisOrchestrator(repo_root=tmp_path)
    orchestrator._audit = logger

    class MockFailure:
        file_path = "bad.py"
        error_type = "SyntaxError"
        error_message = "unexpected EOF"
        is_critical = True

    def mock_l1(*args, **kwargs):
        parse_failures = args[-1]
        parse_failures.append(MockFailure())
        return 0.0, []

    with (
        patch("archguard.analysis._layer_runners._run_layer1", side_effect=mock_l1),
        # Three values: Layer 2 reports whether it measured anything now, like
        # the other three layers. An empty reason means it did.
        patch("archguard.analysis._layer_runners._run_layer2", return_value=(0.0, [], "")),
        patch("archguard.analysis._layer_runners._run_layer4", return_value=(0.0, [], "")),
    ):
        result = orchestrator.run([bad_py], commit_sha="1234567", quiet=True)

    assert log_file.exists(), "Audit log should have been created."

    import json

    logs = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    parse_failures = [log for log in logs if log.get("event") == "parse_failure"]

    assert len(parse_failures) == 1
    assert "error_type" in parse_failures[0]
    assert "bad.py" in parse_failures[0]["file"]


def test_key_file_generated_on_first_run(tmp_path, monkeypatch, caplog):
    """Test that a random key is generated on first run and persisted with 0600."""
    import logging
    import os
    import stat

    from archguard.audit.logger import AuditLogger

    monkeypatch.delenv("ARCHGUARD_AUDIT_STRICT", raising=False)
    monkeypatch.delenv("ARCHGUARD_AUDIT_SECRET", raising=False)

    log_file = tmp_path / "audit.jsonl"
    key_file = tmp_path / "audit.key"
    logger = AuditLogger(log_file)

    with caplog.at_level(logging.INFO):
        logger.log(event="test_event")

    assert log_file.exists()
    assert key_file.exists()
    assert "Generated new audit HMAC key" in caplog.text

    # Check permissions 0600 on non-windows if supported, but let's just check standard stat
    st_mode = key_file.stat().st_mode
    if os.name != "nt":
        assert stat.S_IMODE(st_mode) == 0o600


def test_same_key_loaded_on_second_run(tmp_path, monkeypatch):
    """Test that the same key is loaded on the second run."""
    import json

    from archguard.audit.logger import AuditLogger

    monkeypatch.delenv("ARCHGUARD_AUDIT_STRICT", raising=False)
    monkeypatch.delenv("ARCHGUARD_AUDIT_SECRET", raising=False)

    log_file = tmp_path / "audit.jsonl"
    key_file = tmp_path / "audit.key"

    # Run 1
    logger1 = AuditLogger(log_file)
    logger1.log(event="event1")
    key1 = key_file.read_text(encoding="utf-8").strip()

    # Run 2
    logger2 = AuditLogger(log_file)
    logger2.log(event="event2")
    key2 = key_file.read_text(encoding="utf-8").strip()

    assert key1 == key2

    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    entry1 = json.loads(lines[0])
    entry2 = json.loads(lines[1])
    assert "hmac" in entry1
    assert "hmac" in entry2


def test_strict_mode_fails_without_key_file(tmp_path, monkeypatch):
    """Test that strict mode raises ConfigError when key file doesn't exist."""
    from archguard.audit.logger import AuditLogger
    from archguard.utils.errors import ConfigError

    monkeypatch.setenv("ARCHGUARD_AUDIT_STRICT", "1")
    monkeypatch.delenv("ARCHGUARD_AUDIT_SECRET", raising=False)

    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)

    import pytest

    with pytest.raises(ConfigError, match="ARCHGUARD_AUDIT_STRICT is enabled"):
        logger.log(event="test_event")


def test_strict_mode_passes_with_custom_secret(tmp_path, monkeypatch):
    """Test that strict mode works when custom secret is set."""
    from archguard.audit.logger import AuditLogger

    monkeypatch.setenv("ARCHGUARD_AUDIT_STRICT", "1")
    monkeypatch.setenv("ARCHGUARD_AUDIT_SECRET", "custom-secret-key")

    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)

    # Should not raise
    logger.log(event="test_event")

    assert log_file.exists()
    assert "test_event" in log_file.read_text()


def test_signature_verifies_and_detects_tampering(tmp_path, monkeypatch):
    """The recorded HMAC must actually verify against what was written.

    Regression: the signature was computed over ``json.dumps(entry,
    sort_keys=True)`` while the line was written with ``json.dumps(entry,
    default=str)`` -- different bytes, so no entry could ever be verified, and
    any non-JSON-native value (a datetime, a Path) raised TypeError during
    signing and dropped the whole entry with only a warning.
    """
    import json

    from archguard.audit.logger import AuditLogger, verify_entry

    monkeypatch.setenv("ARCHGUARD_AUDIT_SECRET", "unit-test-secret")

    log_path = tmp_path / ".archguard-cache" / "audit.jsonl"
    AuditLogger(log_path=log_path).log(
        "analysis_run", score=91.5, violations=[], modules=["a", "b"]
    )

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert verify_entry(entry, "unit-test-secret")
    assert not verify_entry(entry, "a-different-secret")

    tampered = {**entry, "score": 100.0}
    assert not verify_entry(tampered, "unit-test-secret")


def test_non_json_native_values_are_still_logged(tmp_path, monkeypatch):
    """A Path or datetime in kwargs must not silently discard the entry."""
    import json
    from datetime import datetime

    from archguard.audit.logger import AuditLogger, verify_entry

    monkeypatch.setenv("ARCHGUARD_AUDIT_SECRET", "unit-test-secret")

    log_path = tmp_path / ".archguard-cache" / "audit.jsonl"
    AuditLogger(log_path=log_path).log(
        "analysis_run", repo=tmp_path, started=datetime.now(UTC)
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, "entry was dropped instead of being serialised"
    assert verify_entry(json.loads(lines[0]), "unit-test-secret")


def test_failure_before_secret_resolution_is_non_fatal(tmp_path, monkeypatch):
    """A failure early in log() must warn, not raise UnboundLocalError.

    Regression: the non-strict error handler called ``os.getenv`` where ``os``
    was imported *inside* the try block, so any exception raised before that
    import turned into UnboundLocalError from the handler itself -- crashing
    the CLI that the handler exists to protect.
    """
    from archguard.audit.logger import AuditLogger

    monkeypatch.delenv("ARCHGUARD_AUDIT_STRICT", raising=False)
    logger = AuditLogger(log_path=tmp_path / ".archguard-cache" / "audit.jsonl")
    monkeypatch.setattr(
        type(logger), "_maybe_rotate",
        lambda self, p: (_ for _ in ()).throw(OSError("disk on fire")),
    )

    logger.log("analysis_run", score=1.0)  # must not raise
