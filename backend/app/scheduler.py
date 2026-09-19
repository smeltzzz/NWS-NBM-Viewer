"""APScheduler integration for inventory refresh and cache warmup.

Kept minimal and off by default in development. The GRIB inventory milestone
subscribes real jobs (poll latest NODD cycle, refresh forecast-hour matrix,
prime the tile cache) here.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None


def _refresh_job() -> None:
    from app.upstream import get_upstream_probe

    probe = get_upstream_probe().check(force=True)
    log.info(
        "inventory refresh tick: upstreams=%s",
        {name: up.reachable for name, up in probe.results.items()},
    )


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler (idempotent)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    _scheduler.add_job(
        _refresh_job,
        trigger=IntervalTrigger(minutes=settings.inventory_refresh_minutes),
        id="nbm-inventory-refresh",
        name="Refresh NBM inventory metadata",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _scheduler.start()
    log.info("scheduler started (inventory refresh every %s min)", settings.inventory_refresh_minutes)
    return _scheduler


def stop_scheduler() -> None:
    """Stop the background scheduler (idempotent)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
