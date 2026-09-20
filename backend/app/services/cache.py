"""Pluggable cache layer (DiskCache + optional Redis) + tile/array helpers.

The same `CacheService` interface backs metadata caches and the shared
active-cycle run pointer.  ``CACHE_BACKEND=disk`` (default) is zero-dependency
and process-shared via the sqlite-backed DiskCache; ``redis`` activates the
multi-host variant so a run pointer published by one machine is instantly
visible to every API worker (with automatic fallback to disk when Redis is
unreachable — a cache outage must never take the API down).
"""

from __future__ import annotations

import pickle
from typing import Any

from diskcache import Cache

from app.config import settings
from app.logging_config import ensure_cache_dir, get_logger
from app.services.retention import cap_ttl, evict_diskcache_older_than

log = get_logger(__name__)

_REDIS_PREFIX = "nbm:meta:"


class CacheService:
    """Wraps DiskCache/Redis with size limits and TTLs."""

    def __init__(self) -> None:
        self.backend = settings.cache_backend
        self._disk: Cache | None = None
        self._redis: Any | None = None
        if self.backend == "redis":
            try:
                import redis

                client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
                client.ping()
                self._redis = client
                log.info("metadata cache: redis at %s", settings.redis_url)
            except Exception as exc:  # noqa: BLE001 — degrade, don't die
                log.warning("redis unavailable (%s); falling back to disk cache", exc)
                self.backend = "disk"
        if self.backend == "disk":
            path = ensure_cache_dir(settings.tile_cache_path)
            self._disk = Cache(
                directory=str(path),
                size_limit=settings.cache_size_limit_bytes,  # bytes, LRU evict
                eviction_policy="least-recently-stored",
            )
            log.info(
                "disk cache ready at %s (limit %.1f GB)",
                path,
                settings.cache_size_limit_gb,
            )

    # ── health ──────────────────────────────────────────────────────────────
    def ping(self) -> bool:
        if self._redis is not None:
            try:
                return bool(self._redis.ping())
            except Exception as exc:  # noqa: BLE001
                log.error("redis cache ping failed: %s", exc)
                return False
        if self._disk is not None:
            try:
                self._disk.volume()
                return True
            except Exception as exc:  # noqa: BLE001
                log.error("disk cache ping failed: %s", exc)
                return False
        return False

    # ── data plane ──────────────────────────────────────────────────────────
    def get(self, key: str) -> Any | None:
        if self._redis is not None:
            try:
                raw = self._redis.get(_REDIS_PREFIX + key)
                return pickle.loads(raw) if raw is not None else None
            except Exception as exc:  # noqa: BLE001
                log.warning("redis get %s failed: %s", key, exc)
                return None
        return self._disk.get(key, default=None) if self._disk is not None else None

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expire = ttl_seconds if ttl_seconds is not None else settings.tile_cache_ttl_seconds
        expire = cap_ttl(expire, settings.cache_max_age_seconds)
        if self._redis is not None:
            try:
                self._redis.set(
                    _REDIS_PREFIX + key, pickle.dumps(value, protocol=5), ex=expire or None
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("redis set %s failed: %s", key, exc)
            return
        if self._disk is None:
            return
        self._disk.set(key, value, expire=expire or None, retry=True)

    def delete(self, key: str) -> bool:
        if self._redis is not None:
            try:
                return bool(self._redis.delete(_REDIS_PREFIX + key))
            except Exception:  # noqa: BLE001
                return False
        if self._disk is None:
            return False
        try:
            return bool(self._disk.delete(key))
        except Exception:  # noqa: BLE001
            return False

    def get_or_set(self, key: str, producer, ttl_seconds: int | None = None) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        if value is not None:
            self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    # ── retention ───────────────────────────────────────────────────────────
    def evict_older_than(self, max_age_seconds: int) -> dict[str, Any]:
        """Age-based disk sweep (retention job).  Never raises.

        Redis needs no sweep: every write carries an explicit TTL, and Redis
        expires entries natively.
        """
        if self._redis is not None:
            return {"removed": 0, "backend": "redis", "note": "native TTLs"}
        if self._disk is None:
            return {"removed": 0}
        try:
            report = evict_diskcache_older_than(self._disk, max_age_seconds)
            return report.as_dict()
        except Exception as exc:  # noqa: BLE001
            log.warning("metadata cache eviction failed: %s", exc)
            return {"removed": 0, "error": f"{type(exc).__name__}: {exc}"}


_cache_singleton: CacheService | None = None


def get_cache() -> CacheService:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = CacheService()
    return _cache_singleton


def cache_key(*parts: object) -> str:
    """Deterministic, collision-safe cache key from arbitrary parts."""
    return "|".join(str(p) for p in parts)
