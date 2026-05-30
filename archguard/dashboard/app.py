import os
import logging
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from archguard.audit.logger import AuditLogger
from archguard.config import AUDIT_LOG_FILENAME
from typing import Any
from pathlib import Path

app = FastAPI(title="ArchGuard Dashboard", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"

security = HTTPBearer(auto_error=False)

def check_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    token = os.environ.get("ARCHGUARD_DASHBOARD_TOKEN")
    if token:
        if not credentials or credentials.credentials != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        client_host = request.client.host if request.client else "unknown"
        if client_host not in ("127.0.0.1", "localhost", "::1"):
            logging.warning(f"Dashboard accessed from {client_host} without token authentication! Consider setting ARCHGUARD_DASHBOARD_TOKEN.")

def get_audit_path() -> Path:
    return Path.cwd() / AUDIT_LOG_FILENAME


@app.get("/api/runs", dependencies=[Depends(check_token)])
def get_runs(limit: int = 50, module: str | None = None) -> Any:
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    if module:
        runs = [r for r in runs if module in r.get("modules_analyzed", [])]
    return {"runs": runs, "total": len(runs)}


@app.get("/api/runs/latest", dependencies=[Depends(check_token)])
def get_latest_run() -> Any:
    logger = AuditLogger(get_audit_path())
    return logger.read_last_run() or {}


@app.get("/api/modules", dependencies=[Depends(check_token)])
def get_modules() -> Any:
    """Return all known modules and their latest scores."""
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=100)
    modules = {}
    for run in runs:
        for module, score in run.get("module_scores", {}).items():
            modules[module] = score  # latest score wins
    return {"modules": modules}


@app.get("/api/trends/{module}", dependencies=[Depends(check_token)])
def get_module_trends(module: str, limit: int = 30) -> Any:
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    trend = [
        {"timestamp": r["timestamp"], "score": r.get("module_scores", {}).get(module)}
        for r in runs
        if module in r.get("module_scores", {})
    ]
    return {"module": module, "trend": trend}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
