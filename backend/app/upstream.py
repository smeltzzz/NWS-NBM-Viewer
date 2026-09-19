"""
Lightweight reachability probe for NOAA/NBM upstream data sources.

NODD (AWS S3) and NOMADS (HTTP) are probed with tiny, cheap requests, memoized
for a short window so the health endpoint can report real upstream status
without hammering NOAA on every scrape. Both degrade gracefully: a sandboxed
or air-gapped deployment simply reports `degraded`, never fails startup.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class UpstreamStatus:
    name: str
    reachable: bool
    latency_ms: float | None = None
    error: str | None = None


@dataclass
class ProbeResult:
    checked_at: float
    results: dict[str, UpstreamStatus] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.checked_at

    @property
    def healty_sources(self) -> list[str]:
        return [name for name, up in self.results.items() if up.reachable]


class UpstreamProbe:
    """Memoized, concurrency-safe reachability probe for upstream hosts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: ProbeResult | None = None
        # Anonymous, extra-paranoid NO_AUTH profile: never sends credentials
        # and never retries (a probe is one quick shot).
        self._client = httpx.Client(
            timeout=settings.upstream_probe_timeout_seconds,
            transport=httpx.HTTPTransport(retries=0),
            follow_redirects=True,
            headers={"User-Agent": f"{settings.app_name}/{settings.app_version} (health-probe)"},
        )

    # ── Targets ────────────────────────────────────────────────────────────
    @property
    def targets(self) -> list[tuple[str, str]]:
        """(name, url) pairs. S3 probe uses the anonymous virtual-host style URL."""
        return [
            ("NODD S3 GRIB", f"https://{settings.s3_bucket_grib}.s3.{settings.aws_default_region}.amazonaws.com/"),
            ("NODD S3 COG", f"https://{settings.s3_bucket_cog}.s3.{settings.aws_default_region}.amazonaws.com/"),
            ("NOMADS HTTP", f"{settings.nomads_base_url}/"),
        ]

    # ── Public API ─────────────────────────────────────────────────────────
    def check(self, force: bool = False) -> ProbeResult:
        """Return cached result, or probe now if stale/forced/unavailable."""
        if not settings.upstream_probe_enabled:
            # Probing disabled: report pluggable/unknown as unreachable and
            # move on without touching the network.
            return ProbeResult(
                checked_at=time.monotonic(),
                results={
                    name: UpstreamStatus(name=name, reachable=False, error="probe disabled")
                    for name, _ in self.targets
                },
            )

        with self._lock:
            if self._cache is not None and not force and self._cache.age_seconds < settings.upstream_probe_cache_seconds:
                return self._cache

        # Probe outside the lock so concurrent health checks don't block each
        # other; last writer wins, which is fine given the TTL.
        result = self._probe_all()
        with self._lock:
            self._cache = result
        return result

    # ── Internals ──────────────────────────────────────────────────────────
    def _probe_all(self) -> ProbeResult:
        results: dict[str, UpstreamStatus] = {}
        for name, url in self.targets:
            status = self._probe_one(name, url)
            results[name] = status
            if status.reachable:
                log.info("upstream %s reachable in %.0fms", name, status.latency_ms)
            else:
                log.warning("upstream %s unreachable: %s", name, status.error)
        return ProbeResult(checked_at=time.monotonic(), results=results)

    def _probe_one(self, name: str, url: str) -> UpstreamStatus:
        started = time.monotonic()
        try:
            # HEAD with a tiny body allowance; only connectivity matters.
            with self._client.stream("GET", url) as response:
                for _ in response.iter_bytes(128):
                    break
            return UpstreamStatus(
                name=name,
                reachable=response.status_code < 500,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
            )
        except httpx.HTTPError as exc:  # Timeout, connect, SSL, 4xx, etc.
            return UpstreamStatus(
                name=name, reachable=False, error=f"{type(exc).__name__}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 — a probe must never raise
            return UpstreamStatus(
                name=name, reachable=False, error=f"{type(exc).__name__}: {exc}"
            )


_probe_singleton: UpstreamProbe | None = None


def get_upstream_probe() -> UpstreamProbe:
    """Process-wide singleton for the upstream probe."""
    global _probe_singleton
    if _probe_singleton is None:
        _probe_singleton = UpstreamProbe()
    return _probe_singleton
