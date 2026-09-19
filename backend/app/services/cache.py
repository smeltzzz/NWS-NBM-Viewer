"""Pluggable cache layer (DiskCache now, Redis later) + tile/array helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from diskcache import Cache

from app.config import settings
from app.logging_config import ensure_cache_dir, get_logger

log = get_logger(__name__)


class CacheService:
    """Wraps DiskCache with size limits and TTLs; Redis-ready interface."""

    def __init__(self) -> None:
        self.backend = settings.cache_backend
        self._disk: Cache | None = None
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

    def ping(self) -> bool:
        if self._disk is not None:
            try:
                self._disk.volume()
                return True
            except Exception as exc:  # noqa: BLE001
                log.error("disk cache ping failed: %s", exc)
                return False
        return False  # redis not wired yet -> readiness reports degraded

    def get(self, key: str) -> Any | None:
        return self._disk.get(key, default=None) if self._disk is not None else None

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        if self._disk is None:
            return
        expire = ttl_seconds if ttl_seconds is not None else settings.tile_cache_ttl_seconds
        self._disk.set(key, value, expire=expire, retry=True)

    def get_or_set(
        self, key: str, producer, ttl_seconds: int | None = None
    ) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        if value is not None:
            self.set(key, value, ttl_seconds=ttl_seconds)
        return value


_cache_singleton: CacheService | None = None


def get_cache() -> CacheService:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = CacheService()
    return _cache_singleton


def cache_key(*parts: object) -> str:
    """Deterministic, collision-safe cache key from arbitrary parts."""
    return "|".join(str(p) for p in parts)
