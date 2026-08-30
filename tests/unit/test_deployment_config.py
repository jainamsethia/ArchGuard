"""The deployment configuration must actually deploy a worker.

Every submitted job is enqueued whenever REDIS_URL is set -- `queue_available`
in archguard/worker/queue.py checks nothing else. So a deployment with Redis
and no worker process does not degrade: it accepts jobs, returns a job id,
streams a progress bar, and never analyses anything. The queue simply grows.

That is what shipped. docker-compose.yml declared a worker, so local
development worked and the gap was invisible; render.yaml declared one web
service and railway.toml one uvicorn command, and `arq` appeared in neither.

These tests read the committed configuration files. They can prove the files
say the right thing -- a worker service exists, it runs the right command, it
is built from the image that carries the ML extras, it is not given an HTTP
health check it cannot answer. They cannot prove a provider honours them; that
needs a real deployment, and docs/DEPLOYMENT.md records which parts are which.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

#: What the worker is started with, everywhere. arq reads the queue Redis and
#: the analysis task from this settings class; anything else is a second worker
#: implementation by accident.
WORKER_COMMAND = "arq archguard.worker.main.WorkerSettings"


def _read(name: str) -> str:
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} is not present in this checkout")
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ Dockerfile


def test_the_dockerfile_builds_both_images():
    """Two targets, and the worker's default command is the arq worker."""
    text = _read("Dockerfile")
    assert "AS web" in text, "no web image target"
    assert "AS worker" in text, "no worker image target"
    assert "arq" in text and "archguard.worker.main.WorkerSettings" in text, (
        "the worker image does not start the arq worker"
    )


def test_only_the_worker_image_carries_the_ml_extras():
    """The whole reason there are two images.

    torch is larger than everything else combined and the web process never
    loads a model, so an extras list that leaks into the web build is a
    multi-gigabyte regression that nothing else here would catch.
    """
    text = _read("Dockerfile")
    web_export = 'requirements-web.txt --without-hashes'
    assert web_export in text, "the web requirements export moved; update this test"
    web_line = next(line for line in text.splitlines() if web_export in line)
    assert "--extras" not in web_line, (
        f"the web image installs extras: {web_line.strip()}"
    )
    assert '--extras "worker"' in text, "the worker image installs no extras"


# -------------------------------------------------------------- docker compose


def test_compose_runs_a_web_service_and_a_worker():
    compose = yaml.safe_load(_read("docker-compose.yml"))
    services = compose.get("services") or {}
    assert "worker" in services, "docker-compose declares no worker service"

    worker = services["worker"]
    assert worker.get("build", {}).get("target") == "worker", (
        "the compose worker is not built from the worker image target"
    )
    assert services.get("app", {}).get("build", {}).get("target") == "web", (
        "the web service is built from something other than the web target"
    )

    env = " ".join(worker.get("environment") or [])
    assert "REDIS_URL" in env, "the worker cannot reach the queue"
    assert "DATABASE_URL" in env, "the worker cannot record the run it performs"


# --------------------------------------------------------------------- Render


def test_render_declares_a_worker_service():
    render = yaml.safe_load(_read("render.yaml"))
    services = render.get("services") or []
    workers = [s for s in services if s.get("type") == "worker"]
    assert workers, (
        "render.yaml declares no worker: every submitted job would be enqueued "
        "and nothing would consume it"
    )

    worker = workers[0]
    command = str(worker.get("dockerCommand") or "")
    assert WORKER_COMMAND in command, (
        f"the Render worker does not run the arq worker, it runs: {command!r}"
    )
    assert "healthCheckPath" not in worker, (
        "the worker serves no HTTP port; an HTTP health check would fail it "
        "into a restart loop"
    )


def test_the_render_worker_has_the_database_and_the_queue():
    """A worker that cannot reach Redis has nothing to do, and one that cannot
    reach PostgreSQL cannot record the analysis it just performed."""
    render = yaml.safe_load(_read("render.yaml"))
    worker = next(s for s in render["services"] if s.get("type") == "worker")
    keys = {e.get("key") for e in (worker.get("envVars") or [])}
    for required in ("DATABASE_URL", "REDIS_URL"):
        assert required in keys, f"the Render worker has no {required}"


def test_the_render_web_service_is_not_the_worker_image():
    render = yaml.safe_load(_read("render.yaml"))
    web = next(s for s in render["services"] if s.get("type") == "web")
    assert WORKER_COMMAND not in str(web.get("dockerCommand") or ""), (
        "the web service is running the analysis worker"
    )


# -------------------------------------------------------------------- Railway


def test_railway_has_a_worker_configuration():
    """Railway takes one service per config file, so the worker is its own."""
    path = ROOT / "railway.worker.toml"
    assert path.exists(), (
        "railway.worker.toml is missing: railway.toml describes only the web "
        "service, so a Railway deploy has no worker"
    )
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    start = str(config.get("deploy", {}).get("startCommand") or "")
    assert WORKER_COMMAND in start, (
        f"the Railway worker does not run the arq worker, it runs: {start!r}"
    )
    assert "healthcheckPath" not in config.get("deploy", {}), (
        "the worker serves no HTTP port; a health check would restart it forever"
    )


def test_the_railway_web_service_still_serves_http():
    config = tomllib.loads(_read("railway.toml"))
    start = str(config.get("deploy", {}).get("startCommand") or "")
    assert "uvicorn" in start, "the Railway web service no longer starts a server"
    assert WORKER_COMMAND not in start, "the web service is running the worker"


# ------------------------------------------------- migrations run exactly once


def test_migrations_run_from_the_entrypoint_not_from_both_processes():
    """Both images share the entrypoint, so both would migrate on start.

    That is safe -- alembic takes a lock and an up-to-date database is a no-op
    -- but it is only safe because it is idempotent, which is worth pinning.
    """
    entrypoint = _read("docker-entrypoint.sh")
    assert "alembic upgrade head" in entrypoint
    assert "exec \"$@\"" in entrypoint, "the entrypoint does not hand off to the command"
