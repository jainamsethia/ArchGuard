def test_read_last_run_finds_written_event():
    """read_last_run must find events written by analyze_cmd."""
    import tempfile, json, os
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
