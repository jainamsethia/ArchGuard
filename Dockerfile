# --- Builder stage --
FROM python:3.12-slim AS builder
WORKDIR /build

# Security: Install only what's needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* ./
RUN pip install --no-cache-dir "poetry==2.4.1" "poetry-plugin-export>=1.8.0" && \
    poetry lock --no-update && \
    poetry export -f requirements.txt --output requirements.txt --without-hashes && \
    pip install --no-cache-dir --target /deps -r requirements.txt

COPY archguard/ ./archguard/
RUN pip install --no-cache-dir --no-deps --target /deps ".[dashboard,cloud,ml]"

# --- Runtime stage --
FROM python:3.12-slim AS runtime

# Security hardening
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user
RUN useradd --create-home --shell /bin/bash --uid 1000 archguard
WORKDIR /app
COPY --from=builder /deps /app/lib

# Set correct ownership BEFORE switching user
RUN chown -R archguard:archguard /app

# Create cache directory with correct permissions
RUN mkdir -p /home/archguard/.archguard-cache && \
    chown -R archguard:archguard /home/archguard/.archguard-cache

ENV PYTHONPATH=/app/lib
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# THIS IS THE LINE THAT WAS COMMENTED OUT - now uncommented
USER archguard

# The default command starts the web dashboard.
# To run the CLI instead: docker run archguard archguard analyze --help
# To start the dashboard explicitly: docker run -p 8000:8000 archguard
# HEALTHCHECK probes port 8000 — correct for local docker run and docker-compose
# (port 8000 is always mapped in those contexts). Railway overrides CMD with
# startCommand and uses its own healthcheckPath setting, so this HEALTHCHECK
# instruction is ignored in Railway deployments.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT []
CMD ["uvicorn", "archguard.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]
