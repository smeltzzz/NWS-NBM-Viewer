"""NWS NBM Viewer API — FastAPI application entrypoint.

Run locally:

    cd backend
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Then hit http://127.0.0.1:8000/health/live — or /docs for OpenAPI.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.v1 import api_router
from app.config import settings
from app.logging_config import configure_logging, get_logger
from app.scheduler import start_scheduler, stop_scheduler
from app.upstream import get_upstream_probe

configure_logging()
log = get_logger(__name__)


def _uptime_seconds() -> float:
    return time.monotonic() - _STARTED_AT


_STARTED_AT = time.monotonic()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown hooks: cache init, optional scheduler, upstream warm."""
    log.info(
        "%s v%s starting (env=%s, api_prefix=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
        settings.api_prefix,
    )

    # Fail fast if a required runtime dependency is missing, so production
    # probes report a clear cause instead of a delayed 500.
    if settings.environment not in ("test",):
        # Cache touched on first request too; this surfaces config errors early.
        try:
            from app.services.cache import get_cache

            get_cache()
        except Exception as exc:  # noqa: BLE001
            log.error("cache initialisation failed: %s", exc)

        if settings.upstream_probe_enabled:
            # Non-blocking warm: don't gate startup on NOAA reachability.
            try:
                get_upstream_probe().check()
            except Exception as exc:  # noqa: BLE001
                log.warning("upstream warm failed: %s", exc)

    if settings.scheduler_enabled:
        stop_scheduler()  # idempotent; safe for --reload child processes
        try:
            await start_scheduler()
        except Exception as exc:  # noqa: BLE001 — never let jobs block startup
            log.error("scheduler start failed (%s); serving without background jobs", exc)

    log.info("startup complete in %.2fs", _uptime_seconds())
    yield

    if settings.scheduler_enabled:
        stop_scheduler()
    log.info("shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
    # Docs/OpenAPI are disabled in production environments.
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    lifespan=lifespan,
)

# CORS — same-origin deployments (Next.js rewrite) don't need it, but
# cross-origin frontends (Vercel -> Fly.io) rely on these exact rules.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Cache-Control", "ETag", "Content-Length", "X-Tile-Cache"],
)

# ── Routers ────────────────────────────────────────────────────────────────
# Versioned, feature-grouped router at /api/v1/* (meta, nbm, tiles, colormaps).
app.include_router(api_router)
# Operational health endpoints: mounted UNVERSIONED at the root so load
# balancers, k8s probes and Docker healthchecks hit a stable, version-free path.
from app.api.routes import health  # noqa: E402  (kept local on purpose)

app.include_router(health.router)


# ── Root / convenience endpoints ───────────────────────────────────────────
@app.get("/", include_in_schema=False, response_class=PlainTextResponse)
def root() -> str:
    """Human-friendly landing response for misdirected browser traffic."""
    return (
        f"{settings.app_name} v{settings.app_version}\n"
        f"API: {settings.api_prefix}\n"
        "Health: /health/live | /health/ready | /health\n"
        "Docs (non-prod): /docs\n"
    )


@app.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
