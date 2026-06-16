import os
import logging
import time
import threading
import uuid
from cachetools import TTLCache as RateLimitCache
from collections import deque
from datetime import datetime, timezone
from fastapi import Path as FastAPIPath
from fastapi import FastAPI, Depends, HTTPException, status, Request, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from archguard.audit.logger import AuditLogger
from archguard.config import AUDIT_LOG_FILENAME
from archguard.llm.advisor import ArchitectureAdvisor
from archguard.llm.openai_provider import OpenAIAdvisorProvider
from typing import Any, Generator
from pathlib import Path

app = FastAPI(title="ArchGuard Dashboard", version="0.2.0")

STATIC_DIR = Path(__file__).parent / "static"

security = HTTPBearer(auto_error=False)

RATE_LIMIT_WINDOW = 60.0
RATE_LIMIT_MAX_REQUESTS = 50

_RATE_LOCK = threading.Lock()
# In-memory cache with maxsize=10_000 evicts the oldest entry when full, providing OOM protection
RATE_LIMITS: RateLimitCache[str, deque[float]] = RateLimitCache(
    maxsize=10_000,
    ttl=RATE_LIMIT_WINDOW * 2
)

_LLM_MAX = 10
_LLM_LIMITS: RateLimitCache[str, deque[float]] = RateLimitCache(
    maxsize=10_000,
    ttl=RATE_LIMIT_WINDOW * 2
)
_LLM_RATE_LOCK = threading.Lock()

def rate_limiter(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    
    with _RATE_LOCK:
        if client_ip not in RATE_LIMITS:
            RATE_LIMITS[client_ip] = deque()
            
        history = RATE_LIMITS[client_ip]
        
        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()
            
        if len(history) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests"
            )
            
        history.append(now)

def _llm_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    
    with _LLM_RATE_LOCK:
        if client_ip not in _LLM_LIMITS:
            _LLM_LIMITS[client_ip] = deque()
            
        history = _LLM_LIMITS[client_ip]
        
        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()
            
        if len(history) >= _LLM_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many LLM requests"
            )
            
        history.append(now)

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
        if client_host not in ("127.0.0.1", "localhost", "::1", "testclient", "testserver"):
            allow_remote = os.environ.get("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "").lower() in ("1", "true")
            if not allow_remote:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Dashboard requires ARCHGUARD_DASHBOARD_TOKEN to be set for remote access",
                )
            else:
                logging.warning(f"Dashboard accessed from {client_host} without token authentication! Consider setting ARCHGUARD_DASHBOARD_TOKEN.")

def get_audit_path() -> Path:
    return Path.cwd() / AUDIT_LOG_FILENAME


@app.get("/api/runs", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_runs(limit: int = Query(default=50, ge=1, le=500), module: str | None = None) -> Any:
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    if module:
        runs = [r for r in runs if module in r.get("modules_analyzed", [])]
    return {"runs": runs, "total": len(runs)}


@app.get("/api/runs/latest", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_latest_run() -> Any:
    logger = AuditLogger(get_audit_path())
    return logger.read_last_run() or {}


@app.get("/api/modules", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_modules() -> Any:
    """Return all known modules and their latest scores."""
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=100)
    modules = {}
    for run in runs:
        for module, score in run.get("module_scores", {}).items():
            modules[module] = score  # latest score wins
    return {"modules": modules}



@app.get("/api/trends/{module}", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_module_trends(
    module: str = FastAPIPath(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.]+$"
    ),
    limit: int = Query(default=30, ge=1, le=500)
) -> Any:
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    trend = [
        {"timestamp": r["timestamp"], "score": r.get("module_scores", {}).get(module)}
        for r in runs
        if module in r.get("module_scores", {})
    ]
    return {"module": module, "trend": trend}


@app.get("/api/evolution/summary", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_evolution_summary(limit: int = Query(default=50, ge=1, le=500)) -> Any:
    """Return the complete evolution trend report."""
    from archguard.evolution.tracker import EvolutionTracker
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    tracker = EvolutionTracker(runs)
    report = tracker.generate_report()
    return report.model_dump() if hasattr(report, "model_dump") else report.dict()


@app.get("/api/evolution/history", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_evolution_history(limit: int = Query(default=50, ge=1, le=500)) -> Any:
    """Return the parsed evolution snapshots."""
    from archguard.evolution.tracker import EvolutionTracker
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    tracker = EvolutionTracker(runs)
    snapshots = [s.model_dump() if hasattr(s, "model_dump") else s.dict() for s in tracker.snapshots]
    return {"history": snapshots, "total": len(snapshots)}


@app.get("/api/evolution/trends", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_evolution_trends(limit: int = Query(default=50, ge=1, le=500)) -> Any:
    """Return just the calculated trends."""
    from archguard.evolution.tracker import EvolutionTracker
    logger = AuditLogger(get_audit_path())
    runs = logger.read_last_n_runs(n=limit)
    tracker = EvolutionTracker(runs)
    report = tracker.generate_report()
    
    def dump_trend(t: Any) -> Any:
        if not t: return None
        return t.model_dump() if hasattr(t, "model_dump") else t.dict()
        
    return {
        "health_trend": dump_trend(report.health_trend),
        "violation_trend": dump_trend(report.violation_trend),
        "debt_trend": dump_trend(report.debt_trend),
        "fitness_trend": dump_trend(report.fitness_trend),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Architecture Evolution (Git History) Endpoints
# ─────────────────────────────────────────────────────────────────────────────

_EVO_LOCK = threading.Lock()
_EVO_CACHE: dict[str, Any] = {}

class EvolutionAnalyzeRequest(BaseModel):
    max_commits: int = 5

@app.post("/api/evolution/analyze", dependencies=[Depends(check_token), Depends(rate_limiter)])
def start_evolution(body: EvolutionAnalyzeRequest) -> Any:
    """Run ArchitectureEvolutionTracker against git history."""
    from archguard.evolution.tracker import ArchitectureEvolutionTracker
    
    try:
        tracker = ArchitectureEvolutionTracker(Path.cwd())
        report = tracker.analyze_history(max_commits=body.max_commits)
        
        result = {
            "snapshots": [
                {
                    "sha": s.sha,
                    "committed_at": s.committed_at,
                    "health_score": s.health_score,
                    "violation_count": s.violation_count,
                    "author": s.author,
                    "message": s.message
                }
                for s in report.snapshots
            ],
            "debt_velocity": report.debt_velocity,
            "trend_direction": report.trend_direction,
            "score_range": {"min": report.score_range[0], "max": report.score_range[1]},
            "commits_analyzed": len(report.snapshots)
        }
        
        with _EVO_LOCK:
            _EVO_CACHE["latest"] = result
            
        return result
    except Exception as exc:
        logging.error("Evolution analysis failed: %s", exc)
        return {"error": str(exc), "snapshots": [], "commits_analyzed": 0}

@app.get("/api/evolution/latest", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_latest_evolution() -> Any:
    """Get the latest completed architecture evolution report."""
    with _EVO_LOCK:
        if "latest" in _EVO_CACHE:
            return _EVO_CACHE["latest"]
    return {"snapshots": [], "commits_analyzed": 0}


@app.get("/api/v1/deps", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_deps() -> Any:
    """Run dependency analysis and return the result."""
    from archguard.analysis.deps import analyze_dependencies
    try:
        result = analyze_dependencies(Path.cwd())
        
        return {
            "score": result.score,
            "vulnerable_packages": [
                {
                    "package": v.package,
                    "version": v.version,
                    "id": v.vulnerability_id,
                    "description": v.description
                }
                for v in result.vulnerabilities
            ],
            "scanned_packages": result.scanned_packages,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
            "error": result.error
        }
    except Exception as e:
        return {
            "score": 0.0,
            "vulnerable_packages": [],
            "scanned_packages": 0,
            "skipped": True,
            "skip_reason": "Exception during analysis",
            "error": str(e)
        }



# ─────────────────────────────────────────────────────────────────────────────
# Advisor session models
# ─────────────────────────────────────────────────────────────────────────────

class AdvisorMessageRequest(BaseModel):
    """Payload for a follow-up question in an existing advisor session."""
    message: str


class AdvisorRecommendationOut(BaseModel):
    title: str
    description: str
    severity: str
    expected_impact: str
    priority_score: int


class AdvisorSessionResponse(BaseModel):
    session_id: str
    created_at: str
    recommendations: list[AdvisorRecommendationOut]
    message: str


class AdvisorMessageResponse(BaseModel):
    session_id: str
    role: str   # "assistant"
    content: str
    history: list[dict[str, str]]


class AdvisorSessionHistoryResponse(BaseModel):
    session_id: str
    created_at: str
    history: list[dict[str, str]]
    recommendations: list[AdvisorRecommendationOut]


# ─────────────────────────────────────────────────────────────────────────────
# In-memory session store  (session_id → session dict)
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_LOCK = threading.Lock()
SESSION_STORE: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = int(os.environ.get("ARCHGUARD_SESSION_TTL", "3600"))  # 1 h default


def _purge_expired_sessions() -> None:
    """Remove sessions older than SESSION_TTL_SECONDS. Called opportunistically."""
    now = time.time()
    with _SESSION_LOCK:
        expired = [k for k, v in SESSION_STORE.items() if now - v["_ts"] > SESSION_TTL_SECONDS]
        for k in expired:
            del SESSION_STORE[k]


def _build_advisor() -> ArchitectureAdvisor:
    """Construct an ArchitectureAdvisor using the configured provider.

    Uses OpenAIAdvisorProvider for the session-based analysis endpoint (initial
    recommendations). The streaming chat endpoint /api/v1/advisor/ask uses
    ArchitectureAdvisor.ask_stream() directly via the Anthropic SDK.
    """
    provider = OpenAIAdvisorProvider()
    return ArchitectureAdvisor(provider)


# ─────────────────────────────────────────────────────────────────────────────
# Advisor endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/advisor/session", dependencies=[Depends(check_token), Depends(_llm_rate_limit)])
def create_advisor_session(limit: int = Query(default=20, ge=1, le=500)) -> Any:
    """Create a new advisor session by running analysis on recent audit data."""
    _purge_expired_sessions()

    audit = AuditLogger(get_audit_path())
    runs = audit.read_last_n_runs(n=limit)

    advisor = _build_advisor()
    try:
        recs = advisor.analyze(runs)
    except Exception as exc:
        logging.warning("Advisor analysis failed: %s", exc)
        recs = []

    session_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    # Seed conversation history with a system-level summary
    if recs:
        system_msg = (
            f"I have analysed {len(runs)} recent ArchGuard run(s) and found "
            f"{len(recs)} prioritised recommendation(s). The top recommendation is: "
            f"{recs[0].title}."
        )
    else:
        system_msg = (
            "I have analysed the ArchGuard history. "
            "No recommendations were generated — the codebase looks healthy, "
            "or there is not enough audit data yet."
        )

    recs_out = [
        AdvisorRecommendationOut(
            title=r.title,
            description=r.description,
            severity=r.severity,
            expected_impact=r.expected_impact,
            priority_score=r.priority_score,
        )
        for r in recs
    ]

    session: dict[str, Any] = {
        "_ts": time.time(),
        "created_at": created_at,
        "recommendations": [r.model_dump() for r in recs_out],
        "history": [{"role": "assistant", "content": system_msg}],
    }

    with _SESSION_LOCK:
        SESSION_STORE[session_id] = session

    return AdvisorSessionResponse(
        session_id=session_id,
        created_at=created_at,
        recommendations=recs_out,
        message=system_msg,
    )


@app.post("/api/advisor/session/{session_id}/message", dependencies=[Depends(check_token), Depends(_llm_rate_limit)])
def advisor_message(
    session_id: str = FastAPIPath(..., min_length=1, max_length=64),
    body: AdvisorMessageRequest = ...,  # type: ignore[assignment]
) -> Any:
    """Send a follow-up question inside an existing advisor session."""
    with _SESSION_LOCK:
        session = SESSION_STORE.get(session_id)

    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Build a textual conversation context for the provider
    history: list[dict[str, str]] = session["history"]
    user_msg = body.message.strip()
    if not user_msg:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message must not be empty")

    history_text = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in history)
    follow_up_context = (
        f"{history_text}\nUser: {user_msg}\n"
        "Please answer the above question with actionable architectural advice."
    )

    provider = OpenAIAdvisorProvider()
    advisor = ArchitectureAdvisor(provider)
    try:
        follow_up_recs = provider.generate_recommendations(follow_up_context)
        if follow_up_recs:
            reply = follow_up_recs[0].description
        else:
            reply = "I'm unable to generate advice for that question at this time."
    except Exception as exc:
        logging.warning("Advisor follow-up failed: %s", exc)
        reply = "Provider error — please try again later."

    new_history = history + [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": reply},
    ]

    with _SESSION_LOCK:
        if session_id in SESSION_STORE:
            SESSION_STORE[session_id]["history"] = new_history
            SESSION_STORE[session_id]["_ts"] = time.time()

    return AdvisorMessageResponse(
        session_id=session_id,
        role="assistant",
        content=reply,
        history=new_history,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Streaming Advisor endpoint (Phase 3 Step 11) — Anthropic active path
# ─────────────────────────────────────────────────────────────────────────────

class AdvisorAskRequest(BaseModel):
    """Payload for the streaming advisor ask endpoint."""
    question: str
    context: str = ""


@app.post("/api/v1/advisor/ask", dependencies=[Depends(check_token), Depends(_llm_rate_limit)])
def advisor_ask_stream(body: AdvisorAskRequest) -> StreamingResponse:
    """Stream an Anthropic Claude response to an architectural question.

    Returns a text/event-stream (SSE) response where each line is a raw text
    chunk yielded by ArchitectureAdvisor.ask_stream().

    Requires ANTHROPIC_API_KEY to be set; falls back to a single error chunk
    when the key is missing or the Anthropic SDK is unavailable.
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="question must not be empty",
        )

    advisor = ArchitectureAdvisor.__new__(ArchitectureAdvisor)
    # ask_stream() does not use self.provider — it talks to Anthropic directly.
    # We skip provider construction entirely to avoid coupling.

    def _sse_generator() -> Generator[str, None, None]:
        try:
            for chunk in advisor.ask_stream(question=question, context=body.context):
                # SSE format: each event is "data: <payload>\n\n"
                yield f"data: {chunk}\n\n"
        except Exception as exc:  # pragma: no cover
            logging.warning("advisor_ask_stream error: %s", exc)
            yield f"data: [error] {exc}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/advisor/session/{session_id}", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_advisor_session(
    session_id: str = FastAPIPath(..., min_length=1, max_length=64),
) -> Any:
    """Retrieve an existing advisor session including conversation history."""
    with _SESSION_LOCK:
        session = SESSION_STORE.get(session_id)

    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return AdvisorSessionHistoryResponse(
        session_id=session_id,
        created_at=session["created_at"],
        history=session["history"],
        recommendations=[
            AdvisorRecommendationOut(**r) for r in session["recommendations"]
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Remediation endpoint (Step 16)
# ─────────────────────────────────────────────────────────────────────────────

class RemediationRequest(BaseModel):
    violations: list[dict[str, Any]] = []


@app.post("/api/remediation/plan", dependencies=[Depends(check_token), Depends(_llm_rate_limit)])
async def remediation_plan(body: RemediationRequest) -> Any:
    """Generate a remediation plan from the provided violations."""
    import asyncio
    from archguard.llm.remediation import generate_remediation_plan

    try:
        result = await generate_remediation_plan(body.violations)
        return result
    except Exception as exc:
        logging.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": str(exc)}


@app.get("/api/remediation/plan", dependencies=[Depends(check_token), Depends(_llm_rate_limit)])
async def remediation_plan_from_audit(limit: int = Query(default=1, ge=1, le=10)) -> Any:
    """Generate a remediation plan from the latest audit run violations."""
    from archguard.llm.remediation import generate_remediation_plan

    audit = AuditLogger(get_audit_path())
    latest = audit.read_last_run() or {}
    violations = latest.get("violations", [])

    try:
        result = await generate_remediation_plan(violations)
        return result
    except Exception as exc:
        logging.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": str(exc)}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
