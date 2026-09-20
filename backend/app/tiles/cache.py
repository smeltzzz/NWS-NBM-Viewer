"""Two-tier cache for the raster tiling pipeline.

Dynamic tiling of GRIB2 is CPU-heavy: one CONUS forecast hour decodes to a
~1500x900 float grid, and a single map pan asks for a few hundred tiles that
all come from it.  The two tiers split that cost:

**Layer 1 — decoded grids (in-process LRU).**
    Keyed by ``domain:cycle:element:fhour``.  Every tile for a variable shares
    one decoded array, so the GRIB decode + S3 byte-range fetch is paid once
    per variable rather than once per tile.  Budgeted in *bytes* (not entries)
    because a CONUS float32 grid is ~5 MB and a Puerto Rico grid is ~0.5 MB.

**Layer 2 — encoded tiles (DiskCache or Redis).**
    Keyed by ``{domain}:{run}:{element}:{fhour}:{z}:{x}:{y}:{units}``.  A warm
    tile is a single serialised WebP buffer read straight off the cache; no
    GRIB, no warp, no colormap, no encoder.

Both tiers are safe to hit from a thread pool: Layer 1 takes a lock, Layer 2
delegates to a backend that is already process-safe.  A cache failure is never
fatal — the tiers degrade to "always miss" so a full disk cannot take the API
down.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Protocol

from app.config import settings
from app.logging_config import ensure_cache_dir, get_logger
from app.services.retention import cap_ttl, evict_diskcache_older_than

__all__ = [
    "CacheStats",
    "GridCache",
    "TileCache",
    "cache_control_headers",
    "cycle_is_latest",
    "get_grid_cache",
    "get_tile_cache",
    "purge_tile_caches",
    "reset_caches",
    "tile_key",
]

log = get_logger(__name__)

TileCacheBackend = Literal["disk", "redis", "memory"]


@dataclass
class CacheStats:
    """Hit/miss counters exposed on ``/api/v1/tiles/cache``."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    entries: int = 0
    bytes: int = 0
    errors: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "evictions": self.evictions,
            "entries": self.entries,
            "bytes": self.bytes,
            "errors": self.errors,
        }


#: Layer-2 value for a tile that rendered to nothing (off-grid, or every value
#: below the colormap threshold).  Caching it matters: sparse fields such as
#: 24 h QPF or snow are transparent over most of CONUS, so without a negative
#: entry every one of those tiles would re-run the full warp on every request.
#: A real WebP always starts with ``RIFF``, so this can never collide.
EMPTY_TILE_MARKER = b"\x00empty-tile"


def is_empty_marker(value: bytes | None) -> bool:
    """True when a layer-2 value is the negative-cache sentinel."""
    return value == EMPTY_TILE_MARKER


def tile_key(
    domain: str,
    cycle: str,
    element: str,
    fhour: int,
    z: int,
    x: int,
    y: int,
    units: str,
    *,
    opacity: float = 1.0,
    smooth: bool = True,
    colormap: str | None = None,
    tilesize: int | None = None,
) -> str:
    """Canonical Layer-2 key.

    The documented core is ``{domain}:{run}:{element}:{fhour}:{z}:{x}:{y}:{units}``;
    rendering options that change pixels are appended so two different renders
    of the same tile cannot collide.
    """
    parts = [
        str(domain).lower(),
        str(cycle),
        str(element).lower(),
        f"f{int(fhour):03d}",
        str(int(z)),
        str(int(x)),
        str(int(y)),
        str(units).lower(),
    ]
    options: list[str] = []
    if abs(opacity - 1.0) > 1e-6:
        options.append(f"o{opacity:.2f}")
    if not smooth:
        options.append("raw")
    if colormap:
        options.append(f"c{colormap}")
    if tilesize:
        options.append(f"s{int(tilesize)}")
    if options:
        parts.append("~".join(options))
    return ":".join(parts)


# ── Layer 1 ───────────────────────────────────────────────────────────────────
class GridCache:
    """Byte-budgeted, thread-safe LRU of decoded GRIB grids."""

    def __init__(self, *, max_bytes: int, max_entries: int = 128) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self.max_entries = int(max_entries)
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self._stats = CacheStats()
        self._loads: dict[str, threading.Event] = {}

    # -- basic operations --------------------------------------------------
    def get(self, key: str) -> Any | None:
        """Look up a grid, recording a hit or miss."""
        with self._lock:
            value = self._data.get(key)
            if value is None:
                self._stats.misses += 1
                return None
            self._data.move_to_end(key)
            self._stats.hits += 1
            return value

    def peek(self, key: str) -> Any | None:
        """Look up without touching the statistics.

        Used for the double-check inside the load lock: that lookup is an
        internal race guard, not a cache miss the caller caused.
        """
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: str, value: Any) -> None:
        size = self._sizeof(value)
        if size > self.max_bytes:
            # A single grid larger than the whole budget would thrash the
            # cache on every insert; better to leave it uncached.
            log.debug("grid %s (%d B) exceeds the layer-1 budget; not cached", key, size)
            return
        with self._lock:
            existing = self._data.pop(key, None)
            if existing is not None:
                self._bytes -= self._sizeof(existing)
            self._data[key] = value
            self._bytes += size
            self._data.move_to_end(key)
            self._evict()
            self._sync_stats()

    def get_or_load(self, key: str, loader: Callable[[], Any]) -> Any:
        """Return a cached grid or call ``loader`` exactly once per key.

        Concurrent requests for the same variable block on a per-key event
        instead of each decoding the GRIB independently — the difference
        between one decode and eight when a map first paints.
        """
        cached = self.get(key)
        if cached is not None:
            return cached

        with self._lock:
            event = self._loads.get(key)
            if event is None:
                self._loads[key] = threading.Event()
                leader = True
            else:
                leader = False

        if not leader:
            event.wait(timeout=30.0)
            cached = self.get(key)
            if cached is not None:
                return cached
            # The leader failed; fall through and load it ourselves.

        try:
            value = loader()
        finally:
            with self._lock:
                if leader:
                    self._loads.pop(key, None).set()

        if value is not None:
            self.put(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0
            self._sync_stats()

    def stats(self) -> CacheStats:
        with self._lock:
            self._sync_stats()
            return self._stats

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _sizeof(value: Any) -> int:
        for attr in ("nbytes", "__len__"):
            size = getattr(value, attr, None)
            if attr == "nbytes" and size is not None:
                return int(size)
            if attr == "__len__" and size is not None:
                return int(size())
        return 1

    def _evict(self) -> None:
        while self._data and (self._bytes > self.max_bytes or len(self._data) > self.max_entries):
            _key, victim = self._data.popitem(last=False)
            self._bytes -= self._sizeof(victim)
            self._stats.evictions += 1

    def _sync_stats(self) -> None:
        self._stats.entries = len(self._data)
        self._stats.bytes = self._bytes


# ── Layer 2 ───────────────────────────────────────────────────────────────────
class _Backend(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl: int) -> None: ...

    # Evict entries past the retention window; return how many went.
    def purge_expired(self) -> int: ...
    def close(self) -> None: ...


class _MemoryBackend:
    """Process-local TTL cache; used for tests and as a Redis/disk fallback.

    Entries carry both an expiry (clamped to the retention window) and their
    store time, so :meth:`purge_expired` can drop anything older than
    ``settings.cache_max_age_hours`` even when it was written with no TTL.
    """

    def __init__(self, *, max_entries: int = 4096) -> None:
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, float, bytes]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires, _stored_at, value = entry
            if expires and expires < time.monotonic():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: bytes, ttl: int) -> None:
        now = time.monotonic()
        ttl = cap_ttl(ttl, settings.cache_max_age_seconds)
        expires = now + ttl if ttl > 0 else 0.0
        with self._lock:
            self._data[key] = (expires, now, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def purge_expired(self) -> int:
        cutoff = time.monotonic() - settings.cache_max_age_seconds
        removed = 0
        with self._lock:
            for key in [k for k, (_e, stored, _v) in self._data.items() if stored <= cutoff]:
                del self._data[key]
                removed += 1
        return removed

    def close(self) -> None:
        with self._lock:
            self._data.clear()


class _DiskBackend:
    def __init__(self, directory, *, size_limit: int) -> None:
        from diskcache import Cache

        self._cache = Cache(
            directory=str(directory),
            size_limit=size_limit,
            eviction_policy="least-recently-used",
        )

    def get(self, key: str) -> bytes | None:
        value = self._cache.get(key, default=None)
        return None if value is None else bytes(value)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        # Retention: even "never expires" writes (historical tiles are
        # immutable, so the renderer passes ttl=None) are clamped to the
        # configured disk window to bound TILE_CACHE_DIR growth.
        ttl = cap_ttl(ttl, settings.cache_max_age_seconds)
        self._cache.set(key, value, expire=ttl if ttl > 0 else None, retry=True)

    def purge_expired(self) -> int:
        return evict_diskcache_older_than(self._cache, settings.cache_max_age_seconds).removed

    def eviction_report(self):
        """Full sweep report (used by the poller + ops endpoint)."""
        return evict_diskcache_older_than(self._cache, settings.cache_max_age_seconds)

    def close(self) -> None:
        self._cache.close()


class _RedisBackend:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=2.0)
        self._client.ping()

    def get(self, key: str) -> bytes | None:
        value = self._client.get(f"nbm:tile:{key}")
        return None if value is None else bytes(value)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        ttl = cap_ttl(ttl, settings.cache_max_age_seconds)
        # Redis expires are native; nothing to sweep — TTLs do the work.
        self._client.set(f"nbm:tile:{key}", value, ex=ttl if ttl > 0 else None)

    def purge_expired(self) -> int:
        return 0

    def eviction_report(self) -> None:
        return None

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 — best effort
            pass


@dataclass
class TileCache:
    """Layer-2 store for finished WebP tile buffers."""

    backend_name: TileCacheBackend = "disk"
    ttl_seconds: int = field(default_factory=lambda: settings.tile_cache_ttl_seconds)
    _backend: Any = None
    _stats: CacheStats = field(default_factory=CacheStats)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self._backend is None:
            self._backend = self._build_backend(self.backend_name)

    @staticmethod
    def _build_backend(name: TileCacheBackend) -> _Backend:
        if name == "redis":
            try:
                backend = _RedisBackend(settings.redis_url)
                log.info("tile cache: redis at %s", settings.redis_url)
                return backend
            except Exception as exc:  # noqa: BLE001
                log.warning("redis tile cache unavailable (%s); using disk", exc)
                name = "disk"
        if name == "disk":
            try:
                directory = ensure_cache_dir(settings.tile_cache_path / "webp")
                backend = _DiskBackend(directory, size_limit=settings.cache_size_limit_bytes)
                log.info(
                    "tile cache: disk at %s (limit %.1f GB)",
                    directory,
                    settings.cache_size_limit_gb,
                )
                return backend
            except Exception as exc:  # noqa: BLE001
                log.warning("disk tile cache unavailable (%s); using memory", exc)
        return _MemoryBackend()

    @property
    def resolved_backend(self) -> str:
        """``disk`` / ``redis`` / ``memory`` — the tier actually in use."""
        return {
            "_DiskBackend": "disk",
            "_RedisBackend": "redis",
            "_MemoryBackend": "memory",
        }.get(type(self._backend).__name__, "unknown")

    def get(self, key: str) -> bytes | None:
        try:
            value = self._backend.get(key)
        except Exception as exc:  # noqa: BLE001 — cache must never 500
            self._stats.errors += 1
            log.warning("tile cache get failed for %s: %s", key, exc)
            return None
        if value is None:
            self._stats.misses += 1
            return None
        self._stats.hits += 1
        return value

    def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        if not value:
            return
        try:
            self._backend.set(key, value, self.ttl_seconds if ttl is None else ttl)
        except Exception as exc:  # noqa: BLE001
            self._stats.errors += 1
            log.warning("tile cache set failed for %s: %s", key, exc)

    def get_or_render(self, key: str, producer: Callable[[], bytes], ttl: int | None = None):
        """Return ``(payload, from_cache)``."""
        cached = self.get(key)
        if cached is not None:
            return cached, True
        payload = producer()
        self.set(key, payload, ttl=ttl)
        return payload, False

    def stats(self) -> CacheStats:
        with self._lock:
            return self._stats

    def purge_expired(self) -> int:
        """Retention sweep for the encoded-tile tier (never raises)."""
        try:
            purge = getattr(self._backend, "purge_expired", None)
            return int(purge()) if callable(purge) else 0
        except Exception as exc:  # noqa: BLE001 — retention must not crash a job
            log.warning("tile cache purge failed: %s", exc)
            return 0

    def eviction_report(self):
        """Detailed sweep report, when the backend can provide one."""
        report = getattr(self._backend, "eviction_report", None)
        return report() if callable(report) else None

    def close(self) -> None:
        try:
            self._backend.close()
        except Exception:  # noqa: BLE001
            pass


# ── Singletons ────────────────────────────────────────────────────────────────
_grid_cache: GridCache | None = None
_tile_cache: TileCache | None = None
_singleton_lock = threading.Lock()


def get_grid_cache() -> GridCache:
    global _grid_cache
    if _grid_cache is None:
        with _singleton_lock:
            if _grid_cache is None:
                _grid_cache = GridCache(
                    max_bytes=settings.tile_grid_cache_mb * 1024 * 1024,
                    max_entries=settings.tile_grid_cache_entries,
                )
                log.info(
                    "grid cache (layer 1): %d MB budget, %d entries",
                    settings.tile_grid_cache_mb,
                    settings.tile_grid_cache_entries,
                )
    return _grid_cache


def get_tile_cache() -> TileCache:
    global _tile_cache
    if _tile_cache is None:
        with _singleton_lock:
            if _tile_cache is None:
                _tile_cache = TileCache(
                    backend_name=settings.cache_backend,  # type: ignore[arg-type]
                    ttl_seconds=settings.tile_cache_ttl_seconds,
                )
    return _tile_cache


def purge_tile_caches() -> dict[str, object]:
    """Retention entry point used by the poller's hourly sweep job.

    Sweeps the encoded-tile tier and, for the disk backend, the S3 sidecar
    cache that shares ``TILE_CACHE_DIR``.  Any failure degrades to a zero
    count — the cache is never allowed to take the application down.
    """
    reports: dict[str, object] = {}
    tile_cache = get_tile_cache()
    report = tile_cache.eviction_report()
    removed = report.removed if report is not None else tile_cache.purge_expired()
    reports["tiles"] = report.as_dict() if report is not None else {"removed": removed}
    try:
        from app.api.endpoints.runs import get_s3_client

        purge = getattr(get_s3_client(), "purge_expired", None)
        if callable(purge):
            reports["s3"] = purge()
    except Exception as exc:  # noqa: BLE001
        log.warning("s3 sidecar purge skipped: %s", exc)
        reports["s3"] = {"error": f"{type(exc).__name__}: {exc}"}
    reports["removed"] = removed
    return reports


def reset_caches() -> None:
    """Drop both tiers.  Used by tests and by ``/tiles/cache?reset=1``."""
    global _grid_cache, _tile_cache
    with _singleton_lock:
        if _grid_cache is not None:
            _grid_cache.clear()
        if _tile_cache is not None:
            _tile_cache.close()
        _tile_cache = None
        _grid_cache = None


# ── HTTP cache policy ─────────────────────────────────────────────────────────
def cycle_is_latest(
    cycle: datetime | str,
    *,
    now: datetime | None = None,
    tolerance_hours: int | None = None,
) -> bool:
    """Is ``cycle`` the newest run NOAA is still publishing?

    NBM posts hourly.  A cycle inside the tolerance window may still be
    receiving late forecast hours, so its tiles must not be marked
    ``immutable``.  Accepts a datetime or ``YYYYMMDDHH``.
    """
    if isinstance(cycle, str):
        cycle = _parse_cycle(cycle)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if cycle.tzinfo is None:
        cycle = cycle.replace(tzinfo=timezone.utc)
    window = timedelta(
        hours=settings.latest_cycle_tolerance_hours if tolerance_hours is None else tolerance_hours
    )
    return current - cycle <= window


def _parse_cycle(value: str) -> datetime:
    text = value.strip().replace("-", "").replace(":", "").replace("T", "")
    for fmt in ("%Y%m%d%H", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text[: len(fmt) + 2], fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"{value!r} is not a YYYYMMDDHH cycle identifier")


def cache_control_headers(
    *,
    is_latest: bool,
    served_from_cache: bool = False,
) -> dict[str, str]:
    """Build the ``Cache-Control`` header set for a tile response.

    Historical runs are frozen — once NOAA has moved on, a given
    ``(cycle, element, fhour)`` tile can never change, so it is cacheable
    forever and safe behind any CDN.  The newest cycle is still being
    published, so it gets a short TTL instead.
    """
    if is_latest:
        directive = f"public, max-age={settings.tile_cache_max_age_latest}"
    else:
        directive = f"public, max-age={settings.tile_cache_max_age_historical}, immutable"
    headers = {"Cache-Control": directive}
    if served_from_cache:
        headers["Age"] = "0"
    return headers
