"""Active-cycle poller: pointer publishing, warm-up, backoff, retention.

All tests run against fake S3 clients / fake renderers — zero network, zero
NOAA dependency.  The pointer store is monkeypatched to a dict so tests do not
touch the on-disk cache.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from app.api.endpoints.runs import RunMetadata
from app.config import settings
from app.core import pointer as pointer_mod
from app.core.s3_client import S3Throttled


class DictPointerCache:
    """Minimal in-memory stand-in for CacheService (get/set/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl_seconds=None):
        self.store[key] = value

    def delete(self, key):
        return self.store.pop(key, None) is not None


@pytest.fixture()
def pointer_cache(monkeypatch):
    cache = DictPointerCache()
    monkeypatch.setattr(pointer_mod, "_cache", lambda: cache)
    pointer_mod.reset_pointer()
    yield cache
    pointer_mod.reset_pointer()


def _metadata(hours, *, date="20260101", cycle=12):
    return RunMetadata(
        date=date,
        cycle=cycle,
        cycle_time=datetime(2026, 1, 1, cycle, tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        domain="co",
        product="core",
        available_forecast_hours=list(hours),
    )


# ── poll tick ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_poll_publishes_fresh_pointer_and_warms_on_complete_edge(pointer_cache, monkeypatch):
    from app.api.endpoints import runs as runs_mod
    from app.cron import poller

    hours = list(range(1, 37)) + list(range(39, 193, 3))

    async def fake_discover(*_a, **_k):
        return _metadata(hours)

    monkeypatch.setattr(runs_mod, "discover_latest_run", fake_discover)

    started: list[dict] = []
    monkeypatch.setattr(poller, "schedule_warmup", lambda **kw: (started.append(kw), True)[1])

    result = await poller.poll_active_cycle()
    assert result["ok"] and result["found"]
    assert result["state"] == "complete"
    assert result["cycle"] == "2026010112"
    assert result["warmup_scheduled"] is True
    assert started == [{"cycle_id": "2026010112", "domain": "co", "product": "core"}]

    stored = pointer_mod.fetch_pointer("co", "core")
    assert stored is not None
    assert stored.cycle_id == "2026010112"
    assert stored.complete is True
    assert stored.state == "complete"
    assert stored.source == "poller"


@pytest.mark.asyncio
async def test_poll_partial_cycle_is_ingesting_and_skips_warmup(pointer_cache, monkeypatch):
    from app.api.endpoints import runs as runs_mod
    from app.cron import poller

    hours = [1, 2, 3]

    async def fake_discover(*_a, **_k):
        return _metadata(hours)

    monkeypatch.setattr(runs_mod, "discover_latest_run", fake_discover)
    called = []
    monkeypatch.setattr(poller, "schedule_warmup", lambda **kw: called.append(kw) or True)

    result = await poller.poll_active_cycle()
    assert result["state"] == "ingesting"
    assert result["warmup_scheduled"] is False
    assert not called
    stored = pointer_mod.fetch_pointer("co", "core")
    assert stored is not None and stored.state == "ingesting"


@pytest.mark.asyncio
async def test_poll_s3_throttle_backs_off_and_keeps_last_pointer(pointer_cache, monkeypatch):
    from app.api.endpoints import runs as runs_mod
    from app.cron import poller

    async def fake_discover(*_a, **_k):
        raise S3Throttled(503, "https://s3/", "SlowDown")

    monkeypatch.setattr(runs_mod, "discover_latest_run", fake_discover)
    monkeypatch.setattr(settings, "poller_backoff_base_seconds", 120.0)

    poller.STATUS.consecutive_failures = 0
    poller.STATUS.backoff_until = 0.0

    result = await poller.poll_active_cycle()
    assert result["ok"] is False
    assert result["throttled"] is True
    assert poller.STATUS.backoff_until > time.monotonic()

    # While cooling down, the scheduled tick skips entirely (no hammering).
    polls_before = poller.STATUS.polls
    await poller._poll_job()
    assert poller.STATUS.polls == polls_before


@pytest.mark.asyncio
async def test_noaa_delay_keeps_last_good_pointer(pointer_cache, monkeypatch):
    from app.api.endpoints import runs as runs_mod
    from app.cron import poller

    async def fake_discover(*_a, **_k):
        return _metadata(list(range(1, 37)))

    monkeypatch.setattr(runs_mod, "discover_latest_run", fake_discover)
    first = await poller.poll_active_cycle()
    assert first["ok"] and first["found"]

    # NOAA goes silent: nothing published in the look-back window.
    async def none_discover(*_a, **_k):
        return None

    monkeypatch.setattr(runs_mod, "discover_latest_run", none_discover)
    result = await poller.poll_active_cycle()
    assert result == {"ok": True, "found": False, "cycle": None}

    stale = pointer_mod.fetch_pointer("co", "core")
    assert stale is not None
    # Pretend the last successful observation was 2 h ago → readers flag
    # the delayed feed instead of pretending data is current.
    aged = stale.model_copy(
        update={"observed_at": datetime.fromtimestamp(time.time() - 2 * 3600, tz=timezone.utc)}
    )
    pointer_mod.publish_pointer(aged)
    stored = pointer_mod.fetch_pointer("co", "core")
    assert stored is not None and stored.is_stale_feed()
    body = stored.as_api_dict()
    assert body["pointer"]["stale"] is True


# ── warm-up ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_warmup_renders_root_tiles_for_key_products(pointer_cache, monkeypatch):
    from app.cron import poller
    from app.tiles import renderer as renderer_mod

    monkeypatch.setattr(settings, "poller_warmup_elements", "tmp,gust")
    monkeypatch.setattr(settings, "poller_warmup_hours", "1")
    monkeypatch.setattr(settings, "poller_warmup_zooms", "3")
    monkeypatch.setattr(settings, "poller_warmup_concurrency", 2)

    requests = []

    class FakeTile:
        def __init__(self, from_cache: bool) -> None:
            self.from_cache = from_cache
            self.payload = b"RIFF"
            self.empty = False

    class FakeRenderer:
        def domain_bounds_4326(self, domain: str):
            assert domain == "co"
            return (-124.7, 24.5, -66.9, 49.4)

        async def render(self, request):
            requests.append(request)
            return FakeTile(from_cache=len(requests) % 2 == 0)

    fake = FakeRenderer()
    # warmup_run imports the factory from app.tiles.renderer at call time.
    monkeypatch.setattr(renderer_mod, "get_tile_renderer", lambda: fake)

    result = await poller.warmup_run(cycle_id="2026010112", domain="co", product="core")
    assert result["status"] in ("done", "failed")
    assert result["total"] > 0
    assert result["rendered"] + result["cached_hits"] == result["total"] - result["errors"]

    # Every request is a z=3 root tile from the product list.
    assert requests, "warm-up must issue tile renders"
    assert {r.z for r in requests} == {3}
    assert {r.element for r in requests} == {"tmp", "gust"}
    assert {r.fhour for r in requests} == {1}
    assert {r.cycle for r in requests} == {"2026010112"}


@pytest.mark.asyncio
async def test_warmup_records_pointer_progress(pointer_cache, monkeypatch):
    from app.cron import poller
    from app.tiles import renderer as renderer_mod

    class FakeTile:
        from_cache = False
        payload = b"RIFF"
        empty = False

    class FakeRenderer:
        def domain_bounds_4326(self, domain):
            return (-125, 24, -66, 50)

        async def render(self, request):
            return FakeTile()

    monkeypatch.setattr(renderer_mod, "get_tile_renderer", lambda: FakeRenderer())
    monkeypatch.setattr(settings, "poller_warmup_elements", "tmp")
    monkeypatch.setattr(settings, "poller_warmup_hours", "1")
    monkeypatch.setattr(settings, "poller_warmup_zooms", "3")

    pointer_mod.publish_pointer(
        pointer_mod.RunPointer(date="20260101", cycle=12, domain="co", product="core")
    )
    result = await poller.warmup_run(cycle_id="2026010112", domain="co", product="core")
    assert result["status"] == "done"
    stored = pointer_mod.fetch_pointer("co", "core")
    assert stored is not None and stored.warmup is not None
    assert stored.warmup.status == "done"
    assert stored.warmup.total == result["total"]


# ── scheduler wiring ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_configure_jobs_registers_poll_and_retention(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.cron import poller

    monkeypatch.setattr(settings, "cache_warmup_on_start", False)
    sched = AsyncIOScheduler(timezone="UTC")
    poller.configure_jobs(sched, owner=True)
    ids = {job.id for job in sched.get_jobs()}
    assert {"nbm-cycle-poll", "nbm-cache-retention"} <= ids

    poll_job = sched.get_job("nbm-cycle-poll")
    lead = (poll_job.next_run_time - datetime.now(timezone.utc)).total_seconds()
    assert -5 <= lead <= 45  # near-immediate first tick
    assert poll_job.max_instances == 1 and poll_job.coalesce

    # Non-owners still sweep but never poll NOAA.
    sched2 = AsyncIOScheduler(timezone="UTC")
    poller.configure_jobs(sched2, owner=False)
    assert sched2.get_job("nbm-cycle-poll") is None
    assert sched2.get_job("nbm-cache-retention") is not None


# ── retention ────────────────────────────────────────────────────────────────
def test_evict_stale_caches_reports_removals(tmp_path, monkeypatch):
    from diskcache import Cache

    from app.cron import poller
    from app.services import retention

    cache = Cache(str(tmp_path / "cache"), size_limit=10**7)
    cache.set("fresh", b"x" * 50, expire=72 * 3600)
    cache.set("ancient", b"y" * 50, expire=1)  # already expired
    time.sleep(1.1)
    report = retention.evict_diskcache_older_than(cache, 72 * 3600)
    assert report.removed >= 1
    assert "ancient" not in list(cache.iterkeys())
    assert "fresh" in list(cache.iterkeys())
    cache.close()

    # The poller-level sweep never raises even with a broken backend.
    class Boom:
        def eviction_report(self):
            raise RuntimeError("backend exploded")

        def purge_expired(self):
            raise RuntimeError("backend exploded")

    monkeypatch.setattr("app.tiles.cache.get_tile_cache", lambda: Boom())
    sweep = poller.evict_stale_caches()
    assert "tiers" in sweep


def test_cap_ttl_semantics():
    from app.services.retention import cap_ttl

    assert cap_ttl(None, 72 * 3600) == 72 * 3600
    assert cap_ttl(0, 72 * 3600) == 72 * 3600
    assert cap_ttl(3600, 72 * 3600) == 3600
    assert cap_ttl(8 * 24 * 3600, 72 * 3600) == 72 * 3600
    assert cap_ttl(None, 0) == 0  # retention disabled keeps legacy semantics


def test_disk_backend_caps_never_expiring_tile_writes(tmp_path, monkeypatch):
    """Historical tiles (ttl=None) must still die with the 72 h retention."""
    import sqlite3

    from app.services import retention
    from app.tiles import cache as tile_cache_mod

    monkeypatch.setattr(settings, "cache_max_age_hours", 2)
    directory = tmp_path / "webp"
    backend = tile_cache_mod._DiskBackend(directory, size_limit=10**6)
    backend.set("run1:tile", b"payload", 0)  # renderer "no expiry" path
    assert backend.get("run1:tile") == b"payload"

    con = sqlite3.connect(str(directory / "cache.db"))
    row = con.execute("SELECT expire_time FROM Cache").fetchone()
    con.close()
    assert row and row[0] is not None
    assert row[0] <= time.time() + 2 * 3600 + 5

    # Not yet stale → sweep keeps it; after the window it goes.
    assert retention.evict_diskcache_older_than(backend._cache, 2 * 3600).removed == 0
    assert backend.purge_expired() >= 0
    backend.close()


def test_poller_lock_single_owner(tmp_path, monkeypatch):
    """Two schedulers in one machine ⇒ only one NOAA-poll owner."""
    from app.cron.lock import PollerLock

    path = tmp_path / "poller.lock"
    a = PollerLock(path)
    assert a.try_acquire() is True
    b = PollerLock(path)
    assert b.try_acquire() is False
    a.release()
    assert b.try_acquire() is True  # re-acquirable after the owner exits
    b.release()
