"""ArchGuard dashboard FastAPI application."""

# ruff: noqa: E402 - load_dotenv() must run before any archguard import: several
# modules (archguard.config, _cookie_auth) read os.environ at import time, so
# hoisting these imports above it would silently ignore the operator's .env.

import asyncio
import importlib.metadata
import logging
import os
import secrets
import sys
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from archguard.dashboard._auth import _real_client_ip

# Re-exported: they used to be defined here, and moving them out of the import
# cycle should not make every caller change its import line.
from archguard.dashboard._workspace_paths import (  # noqa: F401
    JobIdQuery,
    get_target_path,
)
from archguard.observability.logger import configure_logging, correlation_id_var

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def _installed_version() -> str:
    try:
        return importlib.metadata.version("archguard")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

_startup_logger = logging.getLogger("archguard.startup")


async def _periodic_workspace_cleanup() -> None:
    """Remove stale workspaces every 15 minutes as defense-in-depth for crash scenarios."""
    import asyncio as _asyncio

    from archguard.dashboard.workspace import cleanup_stale_workspaces
    while True:
        await _asyncio.sleep(900)  # 15 minutes
        try:
            # Workspaces of jobs that have not finished are exempt. From the
            # database, not from process memory: the analysis runs in a worker
            # now, so this process has no list of what is in flight -- and
            # sweeping a running job's clone out from under it is worse than
            # keeping a stale directory for another fifteen minutes.
            from archguard.db.session import session_scope
            from archguard.db.store import running_job_ids

            async with session_scope() as session:
                active = await running_job_ids(session)
            removed = await cleanup_stale_workspaces(
                max_age_seconds=900, active_job_ids=active
            )
            if removed:
                _startup_logger.info(
                    "Periodic cleanup: removed %d stale workspace(s)", removed
                )
        except Exception as exc:
            _startup_logger.warning(
                "Periodic workspace cleanup error (non-fatal): %s", exc
            )



def _verify_llm_models() -> None:
    """Ask the API whether the configured models exist, if asked to.

    A wrong model id is a quiet failure: every AI call returns an error that
    reads like a bad credential, so an operator checks their key and finds
    nothing wrong with it. One request at boot turns that into a line naming
    the id.

    Off unless ARCHGUARD_VERIFY_LLM_ON_BOOT is set -- it costs a network round
    trip, and a deployment with no AI configured should not pay for it. Never
    raises: the AI features are optional, and a probe that could stop the
    application from starting would be worse than the problem it reports.
    """
    from archguard.dashboard import _capabilities
    from archguard.llm import gemini

    if not gemini.should_verify_on_boot():
        return

    result = gemini.verify_configured_models()
    _capabilities.record_model_check(result)

    if not result.checked:
        _startup_logger.info("LLM model check skipped: %s", result.detail)
    elif result.ok:
        _startup_logger.info(
            "LLM models verified: %s, %s", gemini.primary_model(), gemini.fallback_model()
        )
    else:
        _startup_logger.warning(
            "LLM model check FAILED - %s The AI Advisor and remediation plans "
            "will report themselves unavailable.",
            result.detail,
        )


@asynccontextmanager
async def _lifespan(app_instance: FastAPI) -> Any:
    # Must be the first statement here. Until this ran, uvicorn configured only
    # its own loggers, so every archguard.* INFO record -- including the access
    # log below -- was dropped by a root logger sitting at WARNING with no
    # handler, and WARNING/ERROR reached stderr through logging.lastResort with
    # no timestamp, level or logger name. Nothing in this package logs at module
    # scope, so the lifespan is early enough to catch the whole process.
    configure_logging()

    # Immediately after logging, so an exception during the config check below
    # is reported rather than only written to stdout.
    from archguard.observability.errors import configure_error_reporting

    configure_error_reporting()

    _startup_logger.info("ArchGuard Dashboard starting up...")

    # Before anything serves traffic, and after logging so the reason is
    # visible. Raising here fails the deploy, which is the point: every problem
    # it catches is one that otherwise produces no error at all, only quietly
    # weaker behaviour.
    from archguard.dashboard._config_check import validate_configuration

    validate_configuration()

    recommended = {
        # Names only what the key actually powers. It used to lead with "L4 LLM
        # explanations", which the website has never produced: the parameter
        # that was supposed to control them was read by nothing (C6), and the
        # code that generated them lived in the CLI. An operator reading this
        # would have gone looking for a feature to re-enable.
        "GEMINI_API_KEY": "the AI Advisor and AI remediation plans will be disabled",
        "GITHUB_TOKEN": "GitHub API limited to 60 req/hr (unauthenticated)",
    }
    for var, consequence in recommended.items():
        if not os.environ.get(var):
            _startup_logger.warning("Optional env var %s not set - %s", var, consequence)

    _verify_llm_models()

    if not os.environ.get("ARCHGUARD_DASHBOARD_TOKEN"):
        _startup_logger.warning(
            "ARCHGUARD_DASHBOARD_TOKEN is not set. "
            "Authentication relies on IP-based allowlisting (localhost only). "
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_hex(32))\""
        )

    proxy_ips = os.environ.get("ARCHGUARD_TRUSTED_PROXY_IPS", "").strip()
    if not proxy_ips:
        _startup_logger.warning(
            "ARCHGUARD_TRUSTED_PROXY_IPS is not set. If running behind a proxy "
            "(like Railway or Render), rate limiting will be broken because all "
            "users will share the proxy's IP. Set to '*' to trust all X-Forwarded-For "
            "headers, or a specific CIDR."
        )

    task = None
    try:
        from archguard.dashboard.workspace import cleanup_stale_workspaces
        removed = await cleanup_stale_workspaces(max_age_seconds=3600)
        if removed:
            _startup_logger.info("Removed %d stale workspace(s) on startup", removed)
        import asyncio as _asyncio
        task = _asyncio.create_task(_periodic_workspace_cleanup())
    except Exception as exc:
        _startup_logger.warning("Startup workspace cleanup failed (non fatal): %s", exc)

    # State that is still process-local, named precisely. A blanket warning
    # that stayed constant while the facts changed under it is worse than none:
    # rate limits and the evolution cache now live in Redis when it is
    # configured, and saying otherwise trains operators to ignore this line.
    from archguard.redis_client import is_configured as _redis_configured

    still_local = ["analysis jobs", "login sessions"]
    if not _redis_configured():
        still_local += ["rate limits", "evolution cache"]
        _startup_logger.warning(
            "REDIS_URL is not set. %s are kept in this process only: they are "
            "lost on restart and not shared between instances.",
            ", ".join(still_local).capitalize(),
        )
    else:
        _startup_logger.warning(
            "%s are still held in this process and are lost on restart. "
            "Running more than one instance will not share them.",
            ", ".join(still_local).capitalize(),
        )

    _startup_logger.info("Dashboard ready.")
    yield
    if task:
        task.cancel()

    # Release the connection pools before the loop closes. Without this the
    # engine's sockets are torn down by garbage collection at interpreter exit,
    # which surfaces as "Event loop is closed" noise on every restart.
    from archguard.db.session import dispose_engine
    from archguard.redis_client import close_redis
    from archguard.worker.queue import cancel_inline_tasks

    # Only the development path has anything to cancel. A real worker's jobs
    # stay on the queue when the web process stops, which is the entire point
    # of having split them apart -- a deploy used to kill every running
    # analysis.
    cancelled = await cancel_inline_tasks(timeout=5.0)
    if cancelled:
        _startup_logger.warning(
            "Cancelled %d in-process analysis job(s) during shutdown. They were "
            "running here because no queue is configured; with REDIS_URL set "
            "they would have survived on the queue.",
            cancelled,
        )

    try:
        await dispose_engine()
        close_redis()
    except Exception as exc:
        _startup_logger.warning("Error releasing datastore connections: %s", exc)

    _startup_logger.info("ArchGuard Dashboard shutting down.")

app = FastAPI(
    title="ArchGuard Dashboard",
    version=_installed_version(),
    lifespan=_lifespan,
)

# Compression. 1001.9 KB of static payload was going out uncompressed, and a
# client asking for gzip got byte-identical responses because nothing was
# installed to answer. minimum_size keeps it off the small JSON replies, where
# the CPU and the header cost more than the saving.
app.add_middleware(GZipMiddleware, minimum_size=1000)

_MAX_BODY = 1 * 1024 * 1024  # 1 MB - sufficient for all documented payloads


@app.middleware("http")
async def _limit_body_size(request: Request, call_next: Any) -> Any:
    """Reject requests whose Content-Length or actual body size exceeds 1 MB."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY:
        return JSONResponse(
            status_code=413,
            content={"error": "Request body too large (max 1 MB)"},
        )

    # A lying or absent Content-Length must not get a free pass, so also cap at
    # read time. Stop feeding the body and flag it once the cap is crossed: the
    # route's own body parsing turns the truncated stream into its own error
    # (422/400), which would otherwise mask the real reason, so the flag is
    # re-checked after call_next and wins over whatever the route returned.
    receive = request._receive
    state = {"size": 0, "exceeded": False}

    async def wrapped_receive() -> Any:
        message = await receive()
        if message["type"] == "http.request":
            state["size"] += len(message.get("body", b""))
            if state["size"] > _MAX_BODY:
                state["exceeded"] = True
                return {"type": "http.disconnect"}
        return message

    request._receive = wrapped_receive

    _too_large = JSONResponse(
        status_code=413,
        content={"error": "Request body too large (max 1 MB)"},
    )
    try:
        response = await call_next(request)
    except Exception:
        if state["exceeded"]:
            return _too_large
        raise
    return _too_large if state["exceeded"] else response

# Reusable validated job_id type for all route query parameters.
# UUIDs are 36 chars; allow up to 64 for flexibility. Only hex + hyphens.

_allowed_origins: list[str] = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["Content-Type"],
)

_request_logger = logging.getLogger("archguard.http")

@app.middleware("http")
async def _log_requests(request: Request, call_next: Any) -> Any:
    correlation_id = str(uuid.uuid4())[:8]
    # Bound before call_next so the downstream task inherits it: a task copies
    # the context at creation, which is how every record emitted while handling
    # this request -- not just the line below -- carries the same id.
    token = correlation_id_var.set(correlation_id)
    try:
        client_ip = _real_client_ip(request)
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000)
        _request_logger.info(
            "%s %s -> %d (%dms) ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client_ip,
        )
        response.headers["X-Correlation-ID"] = correlation_id
        return response
    finally:
        # Reset rather than leave it bound: this coroutine runs in the server's
        # context, so an unreset value would be visible to whatever the worker
        # handles next.
        correlation_id_var.reset(token)

@app.middleware("http")
async def _security_headers(request: Request, call_next: Any) -> Any:
    """Attach security headers to every response, including a per-request CSP nonce."""
    nonce = secrets.token_hex(16)
    request.state.csp_nonce = nonce

    response = await call_next(request)

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "connect-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        # 'unsafe-inline' is still required: roughly thirty inline style=
        # attributes are written by dashboard.js. Removing it is a frontend
        # change, not a header change, and claiming otherwise would be worse
        # than the honest directive.
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: https://avatars.githubusercontent.com; "
        # The four D8 additions. default-src does not cover any of them:
        # object-src and base-uri fall back to it in modern browsers but not
        # everywhere, and frame-ancestors and form-action have no fallback at
        # all -- so without these, the page can be framed and a form on it can
        # post anywhere.
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if os.environ.get("ENVIRONMENT") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response

_exc_logger = logging.getLogger("archguard.exceptions")

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # exc_info=exc, not .exception(): this handler runs outside the `except`
    # block that caught the error, so sys.exc_info() is not guaranteed to still
    # hold it -- and a 500 logged without its traceback is close to useless.
    _exc_logger.error(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "type": type(exc).__name__,
        },
    )

_APP_START_TIME = time.time()
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Import routes AFTER app is defined to avoid circular dependencies
# API Versioning Policy (established 2026-06-25):
# All new routes MUST use the /api/v1/ prefix.
# Existing /api/ routes are maintained for backward compatibility.
# A future migration to /api/v1/ for all routes will include redirect aliases.
from fastapi.templating import Jinja2Templates

from archguard.dashboard.routes import (
    advisor,
    auth,
    evolution,
    jobs,
    meta,
    remediation,
    risk,
    runs,
    suppression,
    watch,
)

# Mounted explicitly, in order, rather than registered as a side effect of
# importing each module. The old arrangement had every route module decorate
# this `app` object at import time, which made registration order depend on
# import order -- and a submodule imported before app.py finished would register
# its routes *after* the static mount, where they are shadowed into 404s. The
# 18-line comment in routes/__init__.py existed to hold that hazard at bay.
app.include_router(runs.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(jobs.stream_router, prefix="/api/v1")
app.include_router(evolution.router, prefix="/api/v1")
app.include_router(risk.router, prefix="/api/v1")
app.include_router(suppression.router, prefix="/api/v1")
app.include_router(advisor.router, prefix="/api/v1")
app.include_router(remediation.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(watch.router, prefix="/api/v1")

# Unprefixed and unauthenticated: /health, /ready and /metrics are polled by
# the platform and by a scraper, neither of which has a session; and the OAuth
# callback URL is registered with GitHub.
app.include_router(meta.router)
app.include_router(auth.oauth_router)

def _asset_fingerprint(path: Path) -> str:
    """A short content hash, so an asset's URL changes when its bytes do.

    Content rather than mtime: a redeploy rewrites mtimes without changing a
    byte, which would bust every cache on every release for no reason, and a
    file restored from a backup keeps an old mtime while its contents differ.
    """
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    except OSError:
        # An asset that cannot be read is a deployment problem, not a reason to
        # fail the page: fall back to an unfingerprinted URL, which is served
        # with the short cache policy.
        return ""


def asset_url(name: str) -> str:
    """`/dashboard.js` -> `/dashboard.js?v=<hash>`, for use in templates."""
    fingerprint = _asset_fingerprint(STATIC_DIR / name.lstrip("/"))
    return f"/{name.lstrip('/')}?v={fingerprint}" if fingerprint else f"/{name.lstrip('/')}"


_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Templates ask for assets by name and get a fingerprinted URL back, which is
# what makes the year-long cache policy safe.
_templates.env.globals["asset"] = asset_url

@app.get("/")
async def serve_index(request: Request) -> Response:
    return _templates.TemplateResponse(
        request,
        "index.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )

#: The example report, read once at import. It is a real run -- ArchGuard
#: analysing its own repository -- captured to a committed file rather than
#: queried, so the landing page never depends on the database being reachable
#: or on a particular row still existing.
_EXAMPLE_RUN_PATH = Path(__file__).parent / "example_run.json"


def _example_run() -> dict[str, Any]:
    import json

    try:
        with _EXAMPLE_RUN_PATH.open(encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)
            return data
    except (OSError, ValueError):
        _startup_logger.exception("Could not read the example run")
        return {
            "repo_url": "",
            "score": 0.0,
            "band": "",
            "module_scores": {},
            "layer_results": [],
            "violations": [],
        }


@app.get("/example", include_in_schema=False)
async def serve_example(request: Request) -> Response:
    """A worked example, so the product demonstrates itself.

    The most common question a visitor has is "what do I actually get?", and
    answering it should not require handing over a repository URL first.
    """
    return _templates.TemplateResponse(
        request,
        "example.html",
        {
            "csp_nonce": getattr(request.state, "csp_nonce", ""),
            "run": _example_run(),
        },
    )


@app.get("/privacy", include_in_schema=False)
async def serve_privacy(request: Request) -> Response:
    return _templates.TemplateResponse(
        request, "privacy.html", {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )


@app.get("/terms", include_in_schema=False)
async def serve_terms(request: Request) -> Response:
    return _templates.TemplateResponse(
        request, "terms.html", {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )


@app.get("/dashboard.html")
async def serve_dashboard(request: Request) -> Response:
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )

# Catch-all for unrecognised /api/ paths — must be registered AFTER all real
# API routes but BEFORE the static-file mount so they return a proper 404
# instead of falling through to StaticFiles (which returns 405 for wrong
# methods on paths it matched).
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def _api_404_catch_all() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

@app.exception_handler(404)
async def _not_found(request: Request, exc: Any) -> Response:
    """A page for people, JSON for programs.

    A mistyped URL used to return `{"detail":"Not Found"}` with content-type
    application/json. That is the right answer for an API client and the wrong
    one for someone who fat-fingered an address, so the two are told apart by
    what the caller said it accepts. Anything under /api is always JSON,
    regardless: a browser following an API link is still an API caller.
    """
    # The route's own message, when it had one. Routes raise 404 with detail
    # worth reading -- "No run found for job_id ...", "GitHub API rate limit
    # exceeded" -- and an earlier version of this handler replaced all of them
    # with a flat "Not Found", which turned every actionable API error into a
    # blank one. Caught by test_validate_endpoint_rate_limit.
    detail = getattr(exc, "detail", None) or "Not Found"

    wants_html = "text/html" in request.headers.get("accept", "")
    if request.url.path.startswith("/api/") or not wants_html:
        return JSONResponse(status_code=404, content={"detail": detail})
    return _templates.TemplateResponse(
        request,
        "404.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")},
        status_code=404,
    )


class _CachedStatic(StaticFiles):
    """StaticFiles with a cache policy that depends on the URL.

    Assets were served with no Cache-Control at all, so a returning visitor
    re-downloaded a megabyte of unchanged JavaScript on every page view.

    The policy is split because only one half is safe. A URL carrying a `?v=`
    fingerprint changes whenever the bytes change, so it can be cached for a
    year and never revalidated. A bare URL has nothing to invalidate it, so a
    long immutable policy there would strand a deploy in every browser that had
    loaded the old file -- those get a few minutes, enough to help a page load
    without outliving a release.
    """

    def file_response(
        self,
        full_path: Any,
        stat_result: Any,
        scope: Any,
        status_code: int = 200,
    ) -> Response:
        response: Response = super().file_response(
            full_path, stat_result, scope, status_code
        )
        query = (scope or {}).get("query_string", b"") or b""
        if b"v=" in query:
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
            )
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
        return response


app.mount("/", _CachedStatic(directory=str(STATIC_DIR), html=True), name="static")

__all__ = ["app"]
