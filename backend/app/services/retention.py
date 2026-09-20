"""Age-based disk-cache retention shared by every DiskCache-backed tier.

The 24/7 model is simple:

* **Write-time cap.**  Every tile / sidecar write clamps its TTL to
  ``settings.cache_max_age_seconds`` so nothing on disk can ever outlive the
  retention window (historical tiles used to be written with *no* expiry).
* **Sweep.**  The poller's hourly retention job calls
  :func:`evict_diskcache_older_than`, which purges expired entries, retroactively
  caps legacy never-expiring rows (``expire_time IS NULL``), and re-applies the
  size budget via ``cull()``.

Everything is defensive: a retention failure degrades to "nothing was evicted
this round" — it must never crash the scheduler or corrupt a serving cache.
The direct SQL in :func:`_cap_never_expiring_rows` relies on the stable
DiskCache 5.x schema; if it ever changes the statement fails, is swallowed,
and new writes are still capped at the API layer.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

log = get_logger(__name__)

__all__ = ["EvictionReport", "cap_ttl", "evict_diskcache_older_than"]


@dataclass
class EvictionReport:
    """Outcome of one retention sweep for a single cache directory."""

    directory: str = ""
    removed: int = 0
    retroactively_capped: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def bytes_freed(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)

    def as_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "removed": self.removed,
            "retroactively_capped": self.retroactively_capped,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "bytes_freed": self.bytes_freed,
            "errors": self.errors,
        }


def cap_ttl(ttl: float | None, max_age_seconds: int) -> int:
    """Clamp a requested TTL to the retention window.

    ``None``/``<=0`` ("never expire") becomes exactly ``max_age_seconds`` —
    bounded storage is non-negotiable in the 24/7 profile.
    """
    if max_age_seconds <= 0:
        return int(ttl) if ttl and ttl > 0 else 0
    if not ttl or ttl <= 0:
        return int(max_age_seconds)
    return int(min(ttl, max_age_seconds))


def evict_diskcache_older_than(cache: Any, max_age_seconds: int) -> EvictionReport:
    """Purge entries older than ``max_age_seconds`` from a DiskCache.

    Entries written through this app always carry a TTL (see :func:`cap_ttl`),
    so the bulk of the work is ``cache.expire()``.  Rows written *before* the
    cap existed (``expire_time IS NULL``) are stamped with
    ``store_time + max_age`` first so this very sweep can remove them.
    """
    directory = ""
    report = EvictionReport(directory=directory)
    try:
        directory = str(cache.directory)
        report.directory = directory
        report.bytes_before = int(cache.volume())
    except Exception as exc:  # noqa: BLE001 — retention is best-effort
        report.errors.append(f"open: {type(exc).__name__}: {exc}")
        return report

    try:
        report.retroactively_capped = _cap_never_expiring_rows(cache, max_age_seconds)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"cap: {type(exc).__name__}: {exc}")

    try:
        # ``expire(now=...)`` removes every row whose expire_time is in the
        # past; rows we just capped participate immediately when stale.
        report.removed = int(cache.expire(now=time.time() + 1.0, retry=True) or 0)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"expire: {type(exc).__name__}: {exc}")

    try:
        # Re-apply the size budget (LRU cull) after the age sweep.
        cache.cull(retry=True)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"cull: {type(exc).__name__}: {exc}")

    try:
        report.bytes_after = int(cache.volume())
    except Exception:  # noqa: BLE001 — metrics only
        report.bytes_after = report.bytes_before
    return report


def _cap_never_expiring_rows(cache: Any, max_age_seconds: int) -> int:
    """Give never-expiring rows an ``expire_time`` of ``store_time + cap``.

    Returns the number of rows touched.  Uses the documented DiskCache 5.x
    sqlite layout; wrapped by the caller so a schema drift is a no-op, not a
    crash.  Only affects rows the sweep would otherwise never evict.
    """
    if max_age_seconds <= 0:
        return 0
    db_path = f"{cache.directory}/cache.db"
    cutoff = time.time() - max_age_seconds
    con = sqlite3.connect(db_path, timeout=max(0.5, getattr(cache, "timeout", 0) or 0.5))
    try:
        cur = con.execute(
            """
            UPDATE Cache
               SET expire_time = store_time + ?
             WHERE expire_time IS NULL
            """,
            (max_age_seconds,),
        )
        capped = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        con.commit()
        stale = con.execute(
            """
            SELECT COUNT(*) FROM Cache
             WHERE expire_time IS NOT NULL AND expire_time <= ?
            """,
            (cutoff,),
        ).fetchone()
        log.debug(
            "retention: capped %d never-expiring rows, %d already past the window",
            capped,
            stale[0] if stale else 0,
        )
        return capped
    finally:
        try:
            con.close()
        except sqlite3.Error:  # pragma: no cover
            pass
