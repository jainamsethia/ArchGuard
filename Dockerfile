# Two images from one file. The split is the point: the web process never loads
# an embedding model, and torch alone is larger than everything else combined,
# so shipping it to the web service cost multiple gigabytes and a cold start for
# work that service does not do.
#
#   docker build --target web    -t archguard:web    .
#   docker build --target worker -t archguard:worker .
#
# `--target web` must NOT be able to import torch, and `--target worker` must
# have pip-audit on PATH and the embedding model already baked in. Both are
# asserted in CI, because both regress silently: an extra in the wrong list
# looks like nothing until the image is built.

# --- Shared dependency-resolution stage -----------------------------------
FROM python:3.12-slim AS deps
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* ./
COPY README.md ./
# `poetry check --lock` instead of `poetry lock`: regenerating the lockfile
# during the build threw away the committed pins, so the image could ship
# different (and unaudited) versions than CI's pip-audit ever saw. Fail loudly
# on a stale lock instead -- a stale lock is a repo problem to fix in the repo.
RUN pip install --no-cache-dir "poetry==2.4.1" "poetry-plugin-export>=1.8.0" && \
    poetry check --lock && \
    poetry export -f requirements.txt --output requirements-web.txt --without-hashes && \
    poetry export -f requirements.txt --output requirements-worker.txt --without-hashes --extras "worker"

# --- Web dependencies ------------------------------------------------------
FROM deps AS build-web
RUN pip install --no-cache-dir --target /deps -r requirements-web.txt
COPY archguard/ ./archguard/
RUN pip install --no-cache-dir --no-deps --target /deps .

# --- Worker dependencies ---------------------------------------------------
FROM deps AS build-worker
RUN pip install --no-cache-dir --target /deps -r requirements-worker.txt && \
    # pip-audit is a runtime dependency of the worker, not a dev tool (C2). It
    # was in the dev group and the image never had it, so "Scan Dependencies"
    # answered "pip-audit not found in PATH" in every deployed environment --
    # a shipped feature that had never once worked in production.
    pip install --no-cache-dir --target /deps "pip-audit>=2.7"
COPY archguard/ ./archguard/
RUN pip install --no-cache-dir --no-deps --target /deps ".[worker]"

# Bake the embedding model into the image. Downloading ~90 MB from HuggingFace
# on the first Layer-3 request meant a cold analysis paid for a network fetch
# into an ephemeral layer, every time a container was replaced -- and failed
# outright wherever egress is restricted.
ENV PYTHONPATH=/deps
ENV SENTENCE_TRANSFORMERS_HOME=/models
RUN mkdir -p /models && \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# --- Shared runtime base ---------------------------------------------------
FROM python:3.12-slim AS runtime-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN useradd --create-home --shell /bin/bash --uid 1000 archguard
WORKDIR /app

# Alembic needs its config at runtime, not just at build time: the entrypoint
# brings the schema to head before the process starts. The migrations
# themselves come with the installed package -- alembic.ini names them by
# package reference -- and alembic.ini carries no URL (env.py reads
# DATABASE_URL), so copying it ships no credential.
COPY alembic.ini ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/.archguard-cache && \
    chown -R archguard:archguard /app /app/.archguard-cache

ENV PYTHONPATH=/app/lib
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/lib/bin:${PATH}"

# --- Web image -------------------------------------------------------------
FROM runtime-base AS web
COPY --from=build-web /deps /app/lib
RUN chown -R archguard:archguard /app/lib
USER archguard

# HEALTHCHECK probes port 8000 -- correct for local `docker run` and for
# docker-compose, where 8000 is always mapped. Railway overrides CMD with
# startCommand and uses its own healthcheckPath, so this is ignored there.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "archguard.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Worker image ----------------------------------------------------------
FROM runtime-base AS worker
COPY --from=build-worker /deps /app/lib
COPY --from=build-worker /models /models
RUN chown -R archguard:archguard /app/lib /models
USER archguard

ENV SENTENCE_TRANSFORMERS_HOME=/models
# The model is already on disk, so a worker that cannot reach HuggingFace still
# runs layers 3 and 4. Without this, the library phones home to check for a
# newer revision and blocks until the request times out.
ENV HF_HUB_OFFLINE=1

# No HEALTHCHECK: the worker serves no port. Liveness is the queue draining,
# which /metrics reports from the web side.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["arq", "archguard.worker.main.WorkerSettings"]

# --- Default target --------------------------------------------------------
# Last stage wins for a bare `docker build .`, and the web image is the safer
# default: a plain build should not silently produce the multi-gigabyte one.
#
# Selectable by build argument as well as by `--target`, because not every
# platform exposes one. Render's blueprints, for instance, build a Dockerfile
# path with no way to name a stage:
#
#   docker build .                                     -> web
#   docker build --target worker .                     -> worker
#   docker build --build-arg ARCHGUARD_IMAGE=worker .  -> worker
ARG ARCHGUARD_IMAGE=web
FROM ${ARCHGUARD_IMAGE} AS default
