"""The shared, cross-worker "latest active NBM run" pointer.

Written by the active-cycle poller (:mod:`app.cron.poller`) after it confirms a
newly published cycle in the NOAA S3 bucket, and read by
``GET /api/v1/runs/latest``.  Backed by the process DiskCache so every uvicorn
worker — and a standalone ``python -m app.cron.poller`` daemon sharing the same
volume — observes the same pointer without IPC.

Contract with the API:

* a **fresh** pointer (age < ``POINTER_FRESHNESS_SECONDS``) answers
  ``/runs/latest`` in O(1) — no NOAA traffic per request;
* an **absent/stale** pointer makes the endpoint fall back to live discovery
  and then self-heal by re-publishing (so a dead poller degrades to the old
  per-request behaviour instead of an outage);
* ``stale`` in the payload additionally marks "the poller has not seen NOAA
  publish anything new within ``poller_stale_after_minutes``", which the UI
  renders as a delayed-feed notice rather than pretending data is current.
"""

from __future__ import annotations

import contextlib
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings

__all__ = [
    "RunPointer",
    "WarmupProgress",
    "fetch_pointer",
    "publish_pointer",
    "reset_pointer",
    "update_warmup",
]

RunState = Literal["ingesting", "complete"]
PointerSource = Literal["poller", "live"]

#: A pointer survives restarts for a day; freshness is judged separately so a
#: restart keeps answering from disk until the poller re-validates.
POINTER_TTL_SECONDS = 24 * 3600


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WarmupProgress(BaseModel):
    """How far the async tile warm-up for this cycle has advanced."""

    model_config = ConfigDict(frozen=True)

    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    total: int = 0
    rendered: int = 0
    cached_hits: int = 0
    errors: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(100.0 * (self.rendered + self.cached_hits + self.errors) / self.total, 1)


class RunPointer(BaseModel):
    """The newest NBM cycle the poller has positively observed in NOAA S3."""

    model_config = ConfigDict(frozen=True)

    date: str = Field(pattern=r"^\d{8}$", description="Cycle date YYYYMMDD (UTC)")
    cycle: int = Field(ge=0, le=23, description="Cycle hour 0-23 (UTC)")
    domain: str = Field(default="co")
    product: str = Field(default="core")
    available_forecast_hours: list[int] = Field(default_factory=list)
    #: True once the whole f001-f0NN core window (``poller_complete_window_hours``)
    #: is present; while False the UI keeps the "ingesting" badge.
    complete: bool = False
    state: RunState = "ingesting"
    observed_at: datetime = Field(default_factory=_utcnow)
    source: PointerSource = "poller"
    warmup: WarmupProgress | None = None

    # ── Derived views ───────────────────────────────────────────────────────
    @property
    def cycle_id(self) -> str:
        """``YYYYMMDDHH`` — the identifier every tile/probe endpoint speaks."""
        return f"{self.date}{self.cycle:02d}"

    def age_seconds(self, now: float | None = None) -> float:
        current = now if now is not None else time.time()
        return max(0.0, current - self.observed_at.timestamp())

    def is_fresh(self, *, now: float | None = None) -> bool:
        return self.age_seconds(now) <= settings.pointer_freshness_seconds

    def is_stale_feed(self, *, now: float | None = None) -> bool:
        """No newer cycle seen within the NOAA cadence + grace window."""
        current = now if now is not None else time.time()
        return (current - self.observed_at.timestamp()) > settings.poller_stale_after_minutes * 60

    def cycle_time_utc(self) -> datetime:
        return datetime(
            int(self.date[0:4]),
            int(self.date[4:6]),
            int(self.date[6:8]),
            self.cycle,
            tzinfo=timezone.utc,
        )

    def run_metadata_dict(self) -> dict[str, Any]:
        """Shape compatible with ``RunMetadata`` in the runs endpoints."""
        return {
            "date": self.date,
            "cycle": self.cycle,
            "cycle_time": self.cycle_time_utc().isoformat().replace("+00:00", "Z"),
            "domain": self.domain,
            "product": self.product,
            "available_forecast_hours": list(self.available_forecast_hours),
            "f001_available": 1 in self.available_forecast_hours,
        }

    def as_api_dict(self) -> dict[str, Any]:
        """Full ``/runs/latest`` payload contribution (run + pointer diagnostics)."""
        body: dict[str, Any] = {"run": self.run_metadata_dict()}
        body.update(self.run_metadata_dict())
        body["pointer"] = {
            "cycle": self.cycle_id,
            "state": self.state,
            "complete": self.complete,
            "source": self.source,
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "age_seconds": round(self.age_seconds(), 1),
            "stale": self.is_stale_feed(),
            "warmup": self.warmup.model_dump(mode="json") if self.warmup else None,
        }
        return body


# ── Storage ──────────────────────────────────────────────────────────────────
def _pointer_key(domain: str, product: str) -> str:
    return f"run-pointer:{domain}:{product}"


# Small in-process memo so a chatty frontend never touches sqlite.
_local: dict[str, tuple[float, RunPointer | None]] = {}
_local_lock = threading.Lock()
_LOCAL_TTL_SECONDS = 5.0


def _cache() -> Any | None:
    try:
        from app.services.cache import get_cache

        return get_cache()
    except Exception:  # noqa: BLE001 — pointer store is optional infrastructure
        return None


def publish_pointer(pointer: RunPointer) -> None:
    """Persist the newest observed run so all workers/API see it instantly."""
    key = _pointer_key(pointer.domain, pointer.product)
    cache = _cache()
    if cache is not None:
        # A cache write failure must not bubble into the poller loop: the next
        # tick re-publishes, and workers still share state via the local memo.
        with contextlib.suppress(Exception):
            cache.set(key, pointer.model_dump(mode="json"), ttl_seconds=POINTER_TTL_SECONDS)
    with _local_lock:
        _local[key] = (time.monotonic(), pointer)


def fetch_pointer(domain: str, product: str) -> RunPointer | None:
    """Return the stored pointer (process memo → DiskCache), or ``None``."""
    key = _pointer_key(domain, product)
    now = time.monotonic()
    with _local_lock:
        cached = _local.get(key)
        if cached is not None and (now - cached[0]) < _LOCAL_TTL_SECONDS:
            return cached[1]
    pointer: RunPointer | None = None
    cache = _cache()
    if cache is not None:
        try:
            raw = cache.get(key)
            if isinstance(raw, dict):
                pointer = RunPointer.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — corrupt entry ⇒ rediscover
            log_pointer_failure("fetch", exc)
            pointer = None
    with _local_lock:
        _local[key] = (time.monotonic(), pointer)
    return pointer


def update_warmup(domain: str, product: str, warmup: WarmupProgress) -> RunPointer | None:
    """Attach warm-up progress to the stored pointer (read-modify-write)."""
    current = fetch_pointer(domain, product)
    if current is None:
        return None
    updated = current.model_copy(update={"warmup": warmup})
    publish_pointer(updated)
    return updated


def reset_pointer(domain: str | None = None, product: str | None = None) -> None:
    """Drop pointer state — used by tests and ``POST /poller/poll?reset=1``."""
    with _local_lock:
        if domain is None:
            _local.clear()
        else:
            _local.pop(_pointer_key(domain, product or "core"), None)
    cache = _cache()
    if cache is None:
        return
    pairs = (
        [(domain or "co", product or "core")]
        if domain is not None
        else [(d, p) for d in ("co", "ak", "hi", "pr", "gu", "oc") for p in ("core", "qmd")]
    )
    for dom, prod in pairs:
        try:
            cache.delete(_pointer_key(dom, prod))
        except Exception:  # noqa: BLE001
            pass


def log_pointer_failure(stage: str, exc: Exception) -> None:
    import logging

    logging.getLogger(__name__).warning("run-pointer %s failed: %s", stage, exc)
