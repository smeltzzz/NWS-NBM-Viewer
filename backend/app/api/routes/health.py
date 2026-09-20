"""Health and metadata endpoints.

Semantics:
    /health/live   -> liveness:     process is up and serving requests
    /health/ready  -> readiness:    liveness + critical plumbing initialised
    /health        -> deep health:  liveness + upstream (NODD/NOMADS) reachability

These are OPERATIONAL endpoints and are intentionally mounted at the root
(unversioned) so orchestrators, load balancers and Docker healthchecks can hit
a stable path. Application metadata lives under the versioned API via
``meta.router`` (``/api/v1/version``).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status

from app.config import settings
from app.logging_config import get_logger
from app.services.cache import get_cache
from app.upstream import get_upstream_probe

log = get_logger(__name__)

router = APIRouter(tags=["health"])

HealthStatus = Literal["ok", "degraded"]


@router.get("/health/live", summary="Liveness probe")
def live() -> dict[str, object]:
    """Always returns 200 while the process is accepting requests."""
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/health/ready", summary="Readiness probe")
def ready(response: Response) -> dict[str, object]:
    """Fails (503) until the cache layer and background jobs are initialised."""
    cache_ok = get_cache().ping()
    payload: dict[str, object] = {
        "status": "ok" if cache_ok else "degraded",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "checks": {
            "cache": {"backend": settings.cache_backend, "healthy": cache_ok},
        },
    }
    if not cache_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/health", summary="Deep health (upstream probe)")
def deep_health(response: Response) -> dict[str, object]:
    """Readiness + a memoized reachability probe of NODD/NOMADS upstreams.

    The probe is cheap (GET, first chunk) and cached for a short window; a
    sandboxed or air-gapped environment simply reports 'degraded' without
    failing the process.
    """
    from app.services.cache import get_cache

    probe = get_upstream_probe().check()
    upstreams = {
        name: {
            "reachable": up.reachable,
            "latency_ms": up.latency_ms,
            "error": up.error,
        }
        for name, up in probe.results.items()
    }
    healthy = any(up.reachable for up in probe.results.values()) if probe.results else False

    cache_ok = get_cache().ping()

    overall: HealthStatus = "ok" if (healthy and cache_ok) else "degraded"
    checks: dict[str, object] = {
        "cache": {"backend": settings.cache_backend, "healthy": cache_ok},
        "upstreams": upstreams,
        "is_isolated_environment": not healthy,
    }
    # Background-cycle observability. Informational only: a poller hiccup
    # must not flip health — the API degrades to the last known-good pointer.
    if settings.scheduler_enabled:
        try:
            from app.cron.poller import status_snapshot

            poller_state = status_snapshot()
            checks["poller"] = poller_state
            if poller_state.get("consecutive_failures", 0) >= 3:
                overall = "degraded"
                checks["poller_degraded"] = True
        except Exception as exc:  # noqa: BLE001 — health reporting is best effort
            checks["poller"] = {"error": f"{type(exc).__name__}: {exc}"}
    payload: dict[str, object] = {
        "status": overall,
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "checks": checks,
    }
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload
