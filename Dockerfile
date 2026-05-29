# Stage 1: builder
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (layer caching)
COPY pyproject.toml README.md ./

# Copy full source
COPY archguard/ ./archguard/

# Install the package
RUN pip install --no-cache-dir .

# Stage 2: runtime
FROM python:3.12-slim AS runtime

# Install runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/archguard /usr/local/bin/archguard

# MiniLM downloads on first run to /tmp/hf_cache (no build-time internet needed)
ENV TRANSFORMERS_CACHE=/tmp/hf_cache
ENV HF_HOME=/tmp/hf_cache

# Copy entrypoint and make executable BEFORE switching to non-root user
COPY action/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create non-root user
RUN useradd -m -u 1000 archguard \
    && mkdir -p /github/workspace \
    && chown -R archguard:archguard /github/workspace

USER archguard

WORKDIR /github/workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD archguard --version || exit 1

ENTRYPOINT ["/entrypoint.sh"]
