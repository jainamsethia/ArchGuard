# Stage 1: builder
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy dependency files first (layer caching)
COPY pyproject.toml ./
COPY archguard/__init__.py ./archguard/__init__.py

# Export requirements and install to /install prefix
RUN poetry export --without-hashes -f requirements.txt -o requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy full source
COPY . .
RUN pip install --no-cache-dir --prefix=/install --no-deps .

# Stage 2: runtime
FROM python:3.11-slim AS runtime

# Install runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# MiniLM downloads on first run to /tmp/hf_cache (no build-time internet needed)
ENV TRANSFORMERS_CACHE=/tmp/hf_cache
ENV HF_HOME=/tmp/hf_cache

# Copy entrypoint and make executable BEFORE switching to non-root user
COPY action/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create non-root user
RUN useradd -m -u 1000 archguard
USER archguard

WORKDIR /github/workspace

ENTRYPOINT ["/entrypoint.sh"]
