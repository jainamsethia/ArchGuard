# --- Builder stage --
FROM python:3.12-slim AS builder
WORKDIR /build

# Security: Install only what's needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* ./
RUN pip install --no-cache-dir "poetry==2.4.1" && \
    poetry export -f requirements.txt --output requirements.txt --without hashes && \
    pip install --no-cache-dir --target /deps -r requirements.txt

COPY archguard/ ./archguard/
RUN pip install --no-cache-dir --target /deps .

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

ENTRYPOINT ["python", "-m", "archguard"]
