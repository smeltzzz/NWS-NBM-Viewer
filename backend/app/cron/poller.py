"""NBM active-cycle poller — the 24/7 heartbeat of the viewer.

One ``AsyncIOScheduler`` owns the background loop:

1. **Cycle poll** (every ``POLLER_INTERVAL_MINUTES`` = 10): discover the newest
   ``t00z``-``t23z`` NBM core cycle in the NOAA NODD bucket
   (``noaa-nbm-grib2-pds``).  When a cycle's ``f001``-``f036`` window is fully
   posted, the application's ``GET /api/v1/runs/latest`` pointer flips to it
   instantly (no per-request NOAA discovery while the pointer is fresh).

2. **Warm-up** (async, triggered by the poll on the complete-cycle edge):
   pre-render and pre-cache the root tiles (z = 3, 4, 5) for the key national
   products — 2 m temperature, 6 h QPF, max/min temperature, wind gusts — so
   the first visitor after publication gets cache hits instead of paying GRIB
   fetch + decode + warp + encode on the request path.

3. **Retention** (every ``CACHE_EVICTION_MINUTES`` = 60): evict cached tiles
   older than ``CACHE_MAX_AGE_HOURS`` (72) from disk and re-apply the size
   budget, bounding ``TILE_CACHE_DIR`` growth on long-running hosts.

Graceful degradation is built into every stage:

* S3 throttling (429/503 SlowDown) raises :class:`~app.core.s3_client.S3Throttled`
  after bounded retries; the poller then pauses itself with exponential backoff
  (+ jitter) and keeps serving the last known-good pointer.
* A delayed NOAA hour/cycle is *not* an error: discovery publishes whatever is
  posted (``state="ingesting"``) and re-checks next tick; ``/runs/latest``
  flags ``stale`` when nothing newer appeared within
  ``POLLER_STALE_AFTER_MINUTES`` so the UI can say so instead of lying.
* A failure in warm-up or retention is logged and counted, never fatal.

Exactly one process per cache directory holds the poll — see
:mod:`app.cron.lock` — so N uvicorn workers generate 1× NOAA traffic.

Run standalone (e.g. as a systemd service sharing a host's cache dir):

    python -m app.cron.poller
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

__all__ = [
    "PollerStatus",
    "configure_jobs",
    "evict_stale_caches",
    "poll_active_cycle",
    "standalone_main",
    "start_asyncio_scheduler",
    "status_snapshot",
    "warmup_run",
]

JOB_POLL = "nbm-cycle-poll"
JOB_RETENTION = "nbm-cache-retention"
JOB_WARMUP_ON_START = "nbm-warmup-on-start"

WARMUP_PROGRESS_EVERY = 12


# ── Status bookkeeping ────────────────────────────────────────────────────────
@dataclasses.dataclass
class PollerStatus:
    """Module-level observability for the poll loop (exposed via ops API)."""

    enabled: bool = False
    owns_lock: bool = False
    polls: int = 0
    publishes: int = 0
    last_poll_at: float | None = None
    last_success_at: float | None = None
    last_publish_at: float | None = None
    consecutive_failures: int = 0
    backoff_until: float = 0.0
    last_error: str | None = None
    warmups: int = 0
    warmup_tiles: int = 0
    warmup_errors: int = 0

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        backoff_remaining = max(0.0, self.backoff_until - now)
        return {
            "enabled": self.enabled,
            "owns_lock": self.owns_lock,
            "polls": self.polls,
            "publishes": self.publishes,
            "last_poll_at": _iso(self.last_poll_at),
            "last_success_at": _iso(self.last_success_at),
            "last_publish_at": _iso(self.last_publish_at),
            "consecutive_failures": self.consecutive_failures,
            "backoff_remaining_seconds": round(backoff_remaining, 1),
            "backoff_active": backoff_remaining > 0,
            "last_error": self.last_error,
            "warmups": self.warmups,
            "warmup_tiles": self.warmup_tiles,
            "warmup_errors": self.warmup_errors,
            "interval_minutes": settings.poller_interval_minutes,
            "stale_after_minutes": settings.poller_stale_after_minutes,
            "retention_hours": settings.cache_max_age_hours,
        }


def _iso(monotonic_ts: float | None) -> str | None:
    if monotonic_ts is None:
        return None
    wall = datetime.now(timezone.utc).timestamp() - (time.monotonic() - monotonic_ts)
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat().replace("+00:00", "Z")


STATUS = PollerStatus()


def status_snapshot() -> dict[str, Any]:
    return STATUS.snapshot()


# ── Warm-up task registry ─────────────────────────────────────────────────────
_warmup_task: asyncio.Task[Any] | None = None
_warmup_cycle: str | None = None


def _backoff_delay_seconds(failures: int) -> float:
    """Exponential backoff with ±20 % jitter, capped at POLLER_BACKOFF_MAX."""
    base = settings.poller_backoff_base_seconds
    cap = settings.poller_backoff_max_seconds
    raw = min(cap, base * (2 ** max(0, failures - 1)))
    return max(1.0, raw * (1.0 + random.uniform(-0.2, 0.2)))


# ── Core: one poll tick ───────────────────────────────────────────────────────
async def poll_active_cycle(
    *,
    client: Any | None = None,
    now: datetime | None = None,
    domain: str | None = None,
    product: str | None = None,
) -> dict[str, Any]:
    """Discover the newest cycle, publish the pointer, trigger warm-up.

    Returns a diagnostic dict (also written to :data:`STATUS`).  Safe to call
    from tests with a fake ``client``.  Raises nothing — upstream failures
    translate into back-off state, and the API keeps serving the last
    known-good pointer (graceful degradation).
    """
    from app.api.endpoints.runs import discover_latest_run, get_s3_client
    from app.core.pointer import RunPointer, fetch_pointer, publish_pointer

    domain = domain or settings.nbm_default_domain
    product = product or settings.nbm_default_product
    storage = client or get_s3_client()

    STATUS.polls += 1
    STATUS.last_poll_at = time.monotonic()

    try:
        run = await discover_latest_run(
            storage, now=now, domain=domain, product=product  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001 — the poll loop must never die
        from app.core.s3_client import S3Throttled

        throttled = isinstance(exc, S3Throttled) or _looks_rate_limited(exc)
        STATUS.consecutive_failures += 1
        delay = _backoff_delay_seconds(STATUS.consecutive_failures)
        STATUS.backoff_until = time.monotonic() + delay
        STATUS.last_error = f"{type(exc).__name__}: {exc}"
        level = log.warning if throttled else log.error
        level(
            "NBM poll failed (%s); backing off %.0fs before next attempt",
            STATUS.last_error,
            delay,
        )
        return {
            "ok": False,
            "throttled": throttled,
            "backoff_seconds": round(delay, 1),
            "error": STATUS.last_error,
        }

    STATUS.consecutive_failures = 0
    STATUS.last_success_at = time.monotonic()

    if run is None:
        # NOAA published nothing in the look-back window (rare; a weekend
        # outage would look like this). Keep the previous pointer — clients
        # learn "stale" from its age instead of facing a 404.
        pointer = fetch_pointer(domain, product)
        if pointer is not None and pointer.is_stale_feed():
            log.warning(
                "NOAA cycle delay: newest observed run is %s (%.0f min old)",
                pointer.cycle_id,
                pointer.age_seconds() / 60,
            )
        else:
            log.info("NBM poll: no published cycle found yet")
        return {"ok": True, "found": False, "cycle": None}

    previous = fetch_pointer(domain, product)
    available = list(run.available_forecast_hours)
    cycle_id = f"{run.date}{run.cycle:02d}"
    window = set(range(1, settings.poller_complete_window_hours + 1))
    complete = window.issubset(available)
    changed = (
        previous is None or previous.cycle_id != cycle_id or not _same_hours(previous, available)
    )

    pointer = RunPointer(
        date=run.date,
        cycle=run.cycle,
        domain=domain,
        product=product,
        available_forecast_hours=available,
        complete=complete,
        state="complete" if complete else "ingesting",
        source="poller",
        warmup=previous.warmup if previous is not None and previous.cycle_id == cycle_id else None,
    )
    publish_pointer(pointer)
    STATUS.publishes += 1
    STATUS.last_publish_at = time.monotonic()
    log.info(
        "NBM pointer updated: %s (%d hours, %s)",
        pointer.cycle_id,
        len(available),
        pointer.state,
    )

    warmup_scheduled = False
    if settings.poller_warmup_enabled and complete:
        cycle_id = pointer.cycle_id
        already_warm = (
            previous is not None
            and previous.cycle_id == cycle_id
            and previous.warmup is not None
            and previous.warmup.status in ("running", "done")
        )
        if changed and not already_warm:
            schedule_warmup(cycle_id=cycle_id, domain=domain, product=product)
            warmup_scheduled = True

    return {
        "ok": True,
        "found": True,
        "cycle": pointer.cycle_id,
        "state": pointer.state,
        "hours": len(available),
        "warmup_scheduled": warmup_scheduled,
    }


def _same_hours(pointer: Any, available: list[int]) -> bool:
    return pointer is not None and list(pointer.available_forecast_hours) == list(available)


def _looks_rate_limited(exc: Exception) -> bool:
    name = type(exc).__name__
    if "Throttl" in name or "Timeout" in name or isinstance(exc, (ConnectionError, OSError)):
        return True
    status = getattr(exc, "status", None)
    return bool(status and int(status) in (429, 500, 502, 503, 504))


# ── Warm-up: root tiles for key national products ─────────────────────────────
def schedule_warmup(*, cycle_id: str, domain: str, product: str) -> bool:
    """Kick off the async warm-up task unless one is already running.

    Returns True when a task was started.  Progress is mirrored onto the run
    pointer (``pointer.warmup``) so ``/runs/latest`` and the ops endpoint can
    tell clients whether first paint will be hot or cold.
    """
    global _warmup_task, _warmup_cycle
    running = _warmup_task is not None and not _warmup_task.done()
    if running:
        log.info(
            "warm-up already running (%s); queuing %s is skipped — next tick retries",
            _warmup_cycle,
            cycle_id,
        )
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug("no running loop; warm-up deferred to next poll tick")
        return False
    _warmup_cycle = cycle_id
    _warmup_task = loop.create_task(
        warmup_run(cycle_id=cycle_id, domain=domain, product=product),
        name=f"nbm-warmup-{cycle_id}",
    )
    return True


def _tiles_for_domain_bbox(
    zoom: int, bbox: tuple[float, float, float, float]
) -> list[tuple[int, int]]:
    """Web-mercator tile coords intersecting ``bbox`` (lon/lat) at ``zoom``."""
    from morecantile import tms

    web = tms.get("WebMercatorQuad")
    west, south, east, north = bbox
    tiles: list[tuple[int, int]] = []
    for tile in web.tiles(west, south, east, north, zoom):
        # morecantile yields Tile objects (and sometimes bare tuples by version)
        x = getattr(tile, "x", None)
        y = getattr(tile, "y", None)
        if x is None or y is None:  # pragma: no cover - defensive
            x, y = tile[0], tile[1]
        tiles.append((int(x), int(y)))
    return sorted(set(tiles))


async def warmup_run(*, cycle_id: str, domain: str = "co", product: str = "core") -> dict[str, Any]:
    """Pre-render root tiles (z3/4/5) for the poller's key product list."""
    from app.core.pointer import WarmupProgress, update_warmup
    from app.tiles.renderer import TileRequest, get_tile_renderer

    renderer = get_tile_renderer()
    zooms = settings.poller_warmup_zoom_list or [3, 4, 5]
    elements = [e for e in settings.poller_warmup_element_list if e]
    hours = settings.poller_warmup_hour_list or [1, 6, 12, 18, 24, 36]
    hours = [h for h in hours if h <= settings.nbm_max_forecast_hour]

    update_warmup(domain, product, WarmupProgress(status="running", total=0))
    semaphore = asyncio.Semaphore(max(1, settings.poller_warmup_concurrency))
    rendered = 0
    hits = 0
    errors = 0
    counter_lock = asyncio.Lock()

    jobs: list[tuple[str, int, int, int, int]] = []  # (element, fhour, z, x, y)
    try:
        bbox = renderer.domain_bounds_4326(domain)
    except Exception as exc:  # noqa: BLE001 — unknown domain shouldn't crash loop
        log.error("warm-up: cannot resolve bounds for %s: %s", domain, exc)
        update_warmup(domain, product, WarmupProgress(status="failed"))
        return {"status": "failed", "error": str(exc)}

    for z in zooms:
        for x, y in _tiles_for_domain_bbox(z, bbox):
            for element in elements:
                for fhour in hours:
                    jobs.append((element, fhour, z, x, y))
    total = len(jobs)

    async def warm_one(job: tuple[str, int, int, int, int]) -> None:
        nonlocal rendered, hits, errors
        element, fhour, z, x, y = job
        request = TileRequest(
            domain=domain,
            cycle=cycle_id,
            element=element,
            fhour=fhour,
            z=z,
            x=x,
            y=y,
        )
        async with semaphore:
            try:
                tile = await renderer.render(request)
            except Exception as exc:  # noqa: BLE001 — per-tile failure is fine
                async with counter_lock:
                    errors += 1
                log.debug("warm-up tile %s failed: %s", request.cache_key(None), exc)
                return
            async with counter_lock:
                if tile.from_cache:
                    hits += 1
                else:
                    rendered += 1
                done = rendered + hits + errors
                if done % WARMUP_PROGRESS_EVERY == 0 or done == total:
                    update_warmup(
                        domain,
                        product,
                        WarmupProgress(
                            status="running" if done < total else "done",
                            total=total,
                            rendered=rendered,
                            cached_hits=hits,
                            errors=errors,
                        ),
                    )

    log.info(
        "warm-up started for %s/%s: %d tiles (elements=%s hours=%s zooms=%s)",
        cycle_id,
        domain,
        total,
        ",".join(elements),
        ",".join(str(h) for h in hours),
        ",".join(str(z) for z in zooms),
    )
    started = time.perf_counter()
    await asyncio.gather(*(warm_one(job) for job in jobs))
    elapsed = time.perf_counter() - started

    status = "done" if errors == 0 else ("failed" if rendered + hits == 0 else "done")
    update_warmup(
        domain,
        product,
        WarmupProgress(
            status=status,  # type: ignore[arg-type]
            total=total,
            rendered=rendered,
            cached_hits=hits,
            errors=errors,
        ),
    )
    STATUS.warmups += 1
    STATUS.warmup_tiles += rendered
    STATUS.warmup_errors += errors
    log.info(
        "warm-up %s for %s: %d rendered, %d already cached, %d errors in %.1fs",
        status,
        cycle_id,
        rendered,
        hits,
        errors,
        elapsed,
    )
    return {
        "status": status,
        "total": total,
        "rendered": rendered,
        "cached_hits": hits,
        "errors": errors,
        "seconds": round(elapsed, 2),
    }


# ── Retention: disk-cache age sweep ───────────────────────────────────────────
def evict_stale_caches() -> dict[str, Any]:
    """Evict cached tiles older than the retention window; never raises."""
    max_age = settings.cache_max_age_seconds
    reports: dict[str, Any] = {"max_age_hours": settings.cache_max_age_hours}
    try:
        from app.tiles.cache import purge_tile_caches

        reports["tiers"] = purge_tile_caches()
    except Exception as exc:  # noqa: BLE001
        log.warning("tile cache retention sweep failed: %s", exc)
        reports["tiers"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from app.services.cache import get_cache

        reports["metadata"] = get_cache().evict_older_than(max_age)
    except Exception as exc:  # noqa: BLE001
        reports["metadata"] = {"error": f"{type(exc).__name__}: {exc}"}
    removed = 0
    for tier in (reports.get("tiers") or {}).values():
        if isinstance(tier, dict):
            removed += int(tier.get("removed") or 0)
    if isinstance(reports.get("metadata"), dict):
        removed += int(reports["metadata"].get("removed") or 0)
    reports["removed_total"] = removed
    if removed:
        log.info(
            "retention sweep evicted %d stale entries (limit %dh)",
            removed,
            settings.cache_max_age_hours,
        )
    return reports


# ── Job wrappers ──────────────────────────────────────────────────────────────
async def _poll_job() -> None:
    if STATUS.backoff_until > time.monotonic():
        log.debug(
            "poll tick skipped: cooling down %.0fs after upstream failure",
            STATUS.backoff_until - time.monotonic(),
        )
        return
    await poll_active_cycle()


async def _retention_job() -> None:
    report = await asyncio.to_thread(evict_stale_caches)
    log.debug("retention job: %s", report)


async def _startup_warmup_job() -> None:
    """One-shot after boot when CACHE_WARMUP_ON_START=true."""
    result = await poll_active_cycle()
    if not result.get("found"):
        log.info("startup warm-up: no current cycle found yet")


# ── Scheduler lifecycle ──────────────────────────────────────────────────────
def configure_jobs(scheduler: AsyncIOScheduler, *, owner: bool = True) -> None:
    """Attach poller + retention jobs to ``scheduler`` (idempotent).

    ``owner=False`` (lock held by another worker) still registers the cheap
    retention sweep — it's idempotent and keeps caches trimmed even if the
    owner dies — but skips NOAA discovery traffic.  The first poll tick runs
    ~20 s after boot so ``/runs/latest`` is hot before the first visitors,
    then holds the 10-minute cadence.
    """
    from apscheduler.triggers.interval import IntervalTrigger

    if owner:
        scheduler.add_job(
            _poll_job,
            trigger=IntervalTrigger(minutes=settings.poller_interval_minutes),
            id=JOB_POLL,
            name="Poll NOAA S3 for new NBM cycles",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            jitter=15,
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20),
        )
        if settings.cache_warmup_on_start:
            scheduler.add_job(
                _startup_warmup_job,
                trigger="date",
                id=JOB_WARMUP_ON_START,
                name="Warm tile caches on startup",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                next_run_time=datetime.now(timezone.utc),
            )
    scheduler.add_job(
        _retention_job,
        trigger=IntervalTrigger(minutes=settings.cache_eviction_minutes),
        id=JOB_RETENTION,
        name="Evict cached tiles older than the retention window",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        jitter=30,
    )
    STATUS.enabled = True
    STATUS.owns_lock = owner


async def start_asyncio_scheduler(*, owner: bool = True) -> AsyncIOScheduler:
    """Start the shared scheduler in the running event loop (idempotent).

    Used by the FastAPI lifespan (via :mod:`app.scheduler`) and by the
    standalone daemon.  ``owner`` says whether this process won the poller
    lock and therefore runs NOAA discovery as well as retention.
    """
    from app.scheduler import start_scheduler

    return await start_scheduler(owner=owner)


# ── Standalone daemon ─────────────────────────────────────────────────────────
async def _run_forever() -> None:
    from app.cron.lock import acquire_poller_lock
    from app.scheduler import stop_scheduler

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    lock = acquire_poller_lock()
    if lock is None:  # pragma: no cover — only when a worker already owns it
        log.warning("another process owns the poller lock; daemon idles")
        await stop.wait()
        return

    try:
        await start_asyncio_scheduler(owner=True)
        # One immediate tick so a fresh daemon is useful within seconds.
        await poll_active_cycle()
        await stop.wait()
    finally:
        lock.release()
        stop_scheduler()


def standalone_main() -> None:
    """``python -m app.cron.poller`` — run the poller as its own process."""
    from app.logging_config import configure_logging

    configure_logging()
    log.info(
        "NBM poller daemon starting (interval=%dm, retention=%dh)",
        settings.poller_interval_minutes,
        settings.cache_max_age_hours,
    )
    try:
        asyncio.run(_run_forever())
    except KeyboardInterrupt:  # pragma: no cover
        pass


if __name__ == "__main__":  # pragma: no cover
    standalone_main()
