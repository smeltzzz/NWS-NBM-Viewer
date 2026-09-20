"""Operational endpoints for the active-cycle poller (``/api/v1/poller/*``).

Gated by ``POLLER_OPS_ENDPOINTS_ENABLED`` (default on): these expose
read-only state plus a *rate-limited* manual poll trigger.  ``POST /poll``
never fans out to NOAA harder than a normal scheduler tick would, so it is
safe to leave exposed for on-call automation (and CI smoke tests).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/poller", tags=["ops"])

_last_manual_poll: float = 0.0
_MANUAL_POLL_MIN_INTERVAL_SECONDS = 30.0


def _guard_enabled() -> None:
    if not settings.poller_ops_endpoints_enabled:
        raise HTTPException(
            status_code=404,
            detail="poller ops endpoints are disabled (POLLER_OPS_ENDPOINTS_ENABLED=false)",
        )


@router.get("/status", summary="Poller, pointer, and retention state")
async def poller_status() -> dict[str, object]:
    """Everything the 24/7 story exposes about itself, in one document."""
    from app.core.pointer import fetch_pointer
    from app.cron.poller import status_snapshot

    pointer = fetch_pointer(settings.nbm_default_domain, settings.nbm_default_product)
    snapshot = status_snapshot()
    snapshot["pointer"] = pointer.model_dump(mode="json") if pointer else None
    try:
        from app.api.endpoints.runs import get_s3_client

        snapshot["s3"] = get_s3_client().throttle_state()
    except Exception as exc:  # noqa: BLE001
        snapshot["s3"] = {"error": f"{type(exc).__name__}: {exc}"}
    return snapshot


@router.post("/poll", summary="Trigger an immediate cycle poll (+ optional warm-up)")
async def poll_now(
    warm: bool = Query(True, description="Run root-tile warm-up on a fresh complete cycle"),
) -> dict[str, object]:
    """Run one poll tick now — bounded to one call per 30 s per worker."""
    _guard_enabled()
    import time

    global _last_manual_poll
    now = time.monotonic()
    if now - _last_manual_poll < _MANUAL_POLL_MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=(
                "manual poll rate-limited; retry in "
                f"{_MANUAL_POLL_MIN_INTERVAL_SECONDS - (now - _last_manual_poll):.0f}s"
            ),
        )
    _last_manual_poll = now

    from app.cron.poller import poll_active_cycle

    result = await poll_active_cycle()
    if not result.get("ok"):
        # Surface upstream trouble without 5xx-ing: the API keeps serving the
        # last known-good pointer (graceful degradation).
        result["serving"] = "last-known-good pointer"
    if result.get("ok") and result.get("found") and warm and result.get("state") == "complete":
        from app.cron.poller import schedule_warmup

        result["warmup_scheduled"] = schedule_warmup(
            cycle_id=str(result.get("cycle")),
            domain=settings.nbm_default_domain,
            product=settings.nbm_default_product,
        )
    return result


@router.post("/evict", summary="Run the disk-cache retention sweep now")
async def evict_now() -> dict[str, object]:
    """Manually trigger the ``> CACHE_MAX_AGE_HOURS`` tile eviction."""
    _guard_enabled()
    import asyncio

    from app.cron.poller import evict_stale_caches

    return await asyncio.to_thread(evict_stale_caches)
