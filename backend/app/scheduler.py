"""APScheduler host for unattended 24/7 operation.

Owns the single per-process ``AsyncIOScheduler`` shared by the FastAPI
lifespan and the standalone poller daemon (``python -m app.cron.poller``).
Jobs:

* ``nbm-inventory-refresh`` — cheap upstream reachability probe that feeds
  ``/health`` (every ``INVENTORY_REFRESH_MINUTES``).
* ``nbm-cycle-poll`` + ``nbm-cache-retention`` — contributed by
  :mod:`app.cron.poller` when the poller is enabled; registered only in the
  process holding the advisory poller lock (one per cache directory).

The scheduler is async-native so jobs run *inside* the uvicorn event loop —
no extra threads, no sync HTTP clients blocking workers.  Every job is
``coalesce + max_instances=1``: a tick that overruns (slow NOAA, warm-up in
flight) is skipped, not stacked.  All failures are logged and counted; a job
crash can never take the API down.
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None
_poller_lock = None


def get_shared_scheduler() -> AsyncIOScheduler:
    """Process-wide scheduler (created lazily, UTC clock)."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def _upstream_refresh_job() -> None:
    from app.upstream import get_upstream_probe

    # Sync httpx client → run it off the event loop.
    probe = await asyncio.to_thread(lambda: get_upstream_probe().check(force=True))
    log.info(
        "inventory refresh tick: upstreams=%s",
        {name: up.reachable for name, up in probe.results.items()},
    )


async def start_scheduler(*, owner: bool | None = None) -> AsyncIOScheduler:
    """Start the shared scheduler in the running loop (idempotent).

    ``owner`` selects whether this process registers the NOAA-polling jobs;
    ``None`` decides via the poller lock.  Called from the FastAPI lifespan.
    """
    from app.cron import poller as cycle_poller
    from app.cron.lock import acquire_poller_lock

    scheduler = get_shared_scheduler()
    if scheduler.running:
        return scheduler

    scheduler.add_job(
        _upstream_refresh_job,
        trigger=IntervalTrigger(minutes=settings.inventory_refresh_minutes),
        id="nbm-inventory-refresh",
        name="Refresh NBM inventory metadata",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    if settings.poller_enabled:
        global _poller_lock
        if owner is None:
            _poller_lock = acquire_poller_lock()
            owner = _poller_lock is not None
            if not owner:
                log.info("another worker owns the NBM poller lock; retention-only jobs")
        cycle_poller.configure_jobs(scheduler, owner=bool(owner))

    scheduler.start()
    log.info(
        "scheduler started (poller=%s owner=%s, refresh=%sm, eviction=%sm)",
        settings.poller_enabled,
        owner,
        settings.inventory_refresh_minutes,
        settings.cache_eviction_minutes,
    )
    return scheduler


def stop_scheduler(wait: bool = False) -> None:
    """Stop the shared scheduler and release this process's poller lock."""
    global _scheduler, _poller_lock
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=wait)
    _scheduler = None
    if _poller_lock is not None:
        _poller_lock.release()
        _poller_lock = None


_poller_lock = None
