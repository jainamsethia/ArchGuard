from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from archguard.audit.logger import AuditLogger
from archguard.config import AUDIT_LOG_FILENAME
import os
from pathlib import Path

app = FastAPI(title="ArchGuard Dashboard", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"

def get_audit_path() -> Path:
    return Path.cwd() / AUDIT_LOG_FILENAME

@app.get("/api/runs")
def get_runs(limit: int = 50, module: str | None = None):
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    if module:
        runs = [r for r in runs if module in r.get("modules_analyzed", [])]
    return {"runs": runs, "total": len(runs)}

@app.get("/api/runs/latest")
def get_latest_run():
    logger = AuditLogger(get_audit_path())
    return logger.read_last_run() or {}

@app.get("/api/modules")
def get_modules():
    """Return all known modules and their latest scores."""
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=100)
    modules = {}
    for run in runs:
        for module, score in run.get("module_scores", {}).items():
            modules[module] = score  # latest score wins
    return {"modules": modules}

@app.get("/api/trends/{module}")
def get_module_trends(module: str, limit: int = 30):
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    trend = [
        {"timestamp": r["timestamp"], "score": r.get("module_scores", {}).get(module)}
        for r in runs if module in r.get("module_scores", {})
    ]
    return {"module": module, "trend": trend}

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
