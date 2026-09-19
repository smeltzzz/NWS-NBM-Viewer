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

ENV ENVIRONMENT=production \
    LOG_LEVEL=warning \
    WEB_CONCURRENCY=2

RUN useradd --create-home --uid 10001 nbm && \
    mkdir -p /data/tilecache && chown -R nbm:nbm /data /app

COPY --chown=nbm:nbm . .

USER nbm
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --no-access-log"]
