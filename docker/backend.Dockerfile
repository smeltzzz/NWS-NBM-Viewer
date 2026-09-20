# ═════════════════════════════════════════════════════════════════════════════
# FastAPI backend image — NODD S3 access, GRIB2 decoding, rio-tiler rendering
#
#   target: dev  -> uvicorn --reload (source bind-mounted by compose)
#   target: prod -> gunicorn/uvicorn workers, non-root, no source mount
# ═════════════════════════════════════════════════════════════════════════════

# ── Base: Python runtime + geospatial-free deps ───────────────────────────────
FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# libexpat1/libgomp1 are runtime deps of the GDAL & PROJ wheels shipped by the
# `rasterio` and `pyproj` manylinux builds (no system GDAL install required).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libexpat1 \
        libgomp1 \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer: installed into an isolated venv OUTSIDE /app so the compose
# bind mount of ./backend never shadows installed packages.
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN python -m venv "$VIRTUAL_ENV" && pip install --upgrade pip setuptools wheel

COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# ── Dev: hot reload over a bind-mounted source tree ───────────────────────────
FROM base AS dev

ENV ENVIRONMENT=development \
    LOG_LEVEL=info \
    HOST=0.0.0.0 \
    PORT=8000

COPY . .

EXPOSE 8000

# --reload watches the bind-mounted source; --reload-dir keeps the watcher
# scoped to application code so cache writes don't trigger restarts.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app", "--proxy-headers"]

# ── Prod: immutable source, non-root, multiple workers ────────────────────────
FROM base AS prod

# 24/7 defaults baked in: background scheduler + NOAA active-cycle poller
# (owned by exactly one worker via a file lock) and 72 h tile retention.
# Override any of these via environment in the orchestrator.
ENV ENVIRONMENT=production \
    LOG_LEVEL=warning \
    WEB_CONCURRENCY=2 \
    SCHEDULER_ENABLED=true \
    POLLER_ENABLED=true \
    POLLER_INTERVAL_MINUTES=10 \
    CACHE_WARMUP_ON_START=false \
    CACHE_MAX_AGE_HOURS=72 \
    CACHE_EVICTION_MINUTES=60 \
    POLLER_OPS_ENDPOINTS_ENABLED=true

RUN useradd --create-home --uid 10001 nbm && \
    mkdir -p /data/tilecache && chown -R nbm:nbm /data /app

COPY --chown=nbm:nbm . .

USER nbm
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

# Concurrency-hardened uvicorn flags:
#   --backlog               deep accept queue during tile storms
#   --limit-concurrency     shed load (503) instead of melting the loop
#   --limit-max-requests    recycle workers gently (memory hygiene)
#   --timeout-keep-alive    match the edge proxy's keepalive window
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --backlog ${UVICORN_BACKLOG:-2048} --limit-concurrency ${UVICORN_LIMIT_CONCURRENCY:-1000} --limit-max-requests ${UVICORN_LIMIT_MAX_REQUESTS:-250000} --timeout-keep-alive ${UVICORN_KEEPALIVE_SECONDS:-75} --proxy-headers --no-access-log"]
