"""Run discovery and the complete NBM catalog endpoints."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.config import NbmDomain, NbmProduct
from app.core.catalog import DOMAIN_CATALOG, catalog_dict
from app.core.s3_client import (
    S3Client,
    S3ClientError,
    S3ObjectNotFound,
    build_idx_key,
)

router = APIRouter(tags=["runs", "catalog"])

# Standard NBM core layout.  The result of discovery is always based on HEADs
# against NOAA, so a delayed/missing hour is omitted rather than advertised by
# this optimistic list.
DISCOVERY_FORECAST_HOURS = [
    *range(1, 37),
    *range(39, 193, 3),
    *range(198, 265, 6),
]


class RunMetadata(BaseModel):
    """A published cycle and the forecast hours currently visible in S3."""

    model_config = ConfigDict(frozen=True)

    date: str = Field(description="Cycle date in UTC, YYYYMMDD")
    cycle: int = Field(ge=0, le=23)
    cycle_time: str = Field(description="ISO-8601 UTC cycle timestamp")
    domain: NbmDomain
    product: NbmProduct
    available_forecast_hours: list[int]
    f001_available: bool = True


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _cycle_candidates(
    now: datetime,
    *,
    lookback_hours: int,
    domain: NbmDomain,
) -> list[tuple[str, int, datetime]]:
    """Return cycle timestamps newest first for a domain's cadence."""
    end = _utc_now(now).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=lookback_hours)
    allowed = set(DOMAIN_CATALOG[domain].cycles)
    candidates: list[tuple[str, int, datetime]] = []
    cursor = end
    while cursor >= start:
        if cursor.hour in allowed:
            candidates.append((cursor.strftime("%Y%m%d"), cursor.hour, cursor))
        cursor -= timedelta(hours=1)
    return candidates


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _index_exists(
    client: Any,
    date: str,
    cycle: int,
    product: NbmProduct,
    fhour: int,
    domain: NbmDomain,
) -> bool:
    """Probe an index without downloading it, with a test-friendly fallback."""
    try:
        if hasattr(client, "object_exists"):
            if hasattr(client, "idx_key"):
                key = client.idx_key(date, cycle, product, fhour, domain)
            else:
                key = build_idx_key(date, cycle, product, fhour, domain)
            return bool(await _maybe_await(client.object_exists(key)))

        # Small fake clients and alternate storage adapters may implement only
        # fetch_idx.  A missing forecast step is represented by 404/None.
        entries = await _maybe_await(client.fetch_idx(date, cycle, product, fhour, domain))
        return entries is not None
    except (S3ObjectNotFound, S3ClientError, FileNotFoundError):
        return False
    except Exception:
        # Inventory discovery should continue when one NOAA request times out;
        # the endpoint will return the cycles that were positively observed.
        return False


async def _available_hours(
    client: Any,
    date: str,
    cycle: int,
    product: NbmProduct,
    domain: NbmDomain,
    *,
    concurrency: int = 16,
) -> list[int]:
    """Find posted hours and gracefully omit forecast steps not yet available."""
    first_available = await _index_exists(client, date, cycle, product, 1, domain)
    if not first_available:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def probe(hour: int) -> tuple[int, bool]:
        async with semaphore:
            return hour, await _index_exists(client, date, cycle, product, hour, domain)

    results = await asyncio.gather(*(probe(hour) for hour in DISCOVERY_FORECAST_HOURS))
    return [hour for hour, available in sorted(results) if available]


def _run_record(
    date: str,
    cycle: int,
    cycle_time: datetime,
    domain: NbmDomain,
    product: NbmProduct,
    hours: list[int],
) -> RunMetadata:
    return RunMetadata(
        date=date,
        cycle=cycle,
        cycle_time=cycle_time.isoformat().replace("+00:00", "Z"),
        domain=domain,
        product=product,
        available_forecast_hours=hours,
        f001_available=1 in hours,
    )


async def discover_latest_run(
    client: Any | None = None,
    *,
    now: datetime | None = None,
    domain: NbmDomain = "co",
    product: NbmProduct = "core",
) -> RunMetadata | None:
    """Find the newest cycle with a posted f001 index in the last 24 hours."""
    storage = client or get_s3_client()
    current = _utc_now(now)
    for date, cycle, cycle_time in _cycle_candidates(current, lookback_hours=24, domain=domain):
        # The explicit f001 gate is intentional: a cycle directory can exist
        # before NOAA posts its first usable forecast file.
        if await _index_exists(storage, date, cycle, product, 1, domain):
            hours = await _available_hours(storage, date, cycle, product, domain)
            if hours:
                return _run_record(date, cycle, cycle_time, domain, product, hours)
    return None


async def discover_available_runs(
    client: Any | None = None,
    *,
    now: datetime | None = None,
    days: int = 3,
    domain: NbmDomain = "co",
    product: NbmProduct = "core",
) -> list[RunMetadata]:
    """Return positive cycle discoveries from the trailing ``days`` window."""
    if days < 1 or days > 3:
        raise ValueError("days must be between 1 and 3")
    storage = client or get_s3_client()
    current = _utc_now(now)
    candidates = _cycle_candidates(current, lookback_hours=days * 24, domain=domain)

    # Gate all candidates on f001 first.  This avoids probing 85 objects for
    # every cycle that NOAA has not published at all.
    gates = await asyncio.gather(
        *(
            _index_exists(storage, date, cycle, product, 1, domain)
            for date, cycle, _cycle_time in candidates
        )
    )
    positive = [candidate for candidate, available in zip(candidates, gates) if available]

    async def make_record(candidate: tuple[str, int, datetime]) -> RunMetadata | None:
        date, cycle, cycle_time = candidate
        hours = await _available_hours(storage, date, cycle, product, domain)
        if not hours:
            return None
        return _run_record(date, cycle, cycle_time, domain, product, hours)

    records = await asyncio.gather(*(make_record(candidate) for candidate in positive))
    return [record for record in records if record is not None]


@lru_cache(maxsize=1)
def get_s3_client() -> S3Client:
    """Process-local client/cache used by the API workers."""
    return S3Client()


@router.get("/runs/latest", summary="Discover the latest completed NBM run")
async def latest_run(
    domain: NbmDomain = Query(default="co"),
    product: NbmProduct = Query(default="core"),
) -> dict[str, object]:
    run = await discover_latest_run(domain=domain, product=product)
    # Include the record under `run` as well as top-level fields.  The latter
    # keeps this endpoint convenient for simple clients while the former makes
    # the response extensible with discovery diagnostics.
    body: dict[str, object] = {"run": run.model_dump(mode="json") if run else None}
    if run:
        body.update(run.model_dump(mode="json"))
    return body


@router.get("/runs/available", summary="List NBM cycles and posted forecast hours")
async def available_runs(
    domain: NbmDomain = Query(default="co"),
    product: NbmProduct = Query(default="core"),
) -> dict[str, object]:
    runs = await discover_available_runs(domain=domain, product=product, days=3)
    return {
        "domain": domain,
        "product": product,
        "days": 3,
        "runs": [run.model_dump(mode="json") for run in runs],
    }


@router.get("/catalog", summary="Complete hierarchical NBM product catalog")
def complete_catalog() -> dict[str, object]:
    """Return domains, categories, and every element's GRIB selector."""
    return catalog_dict()


__all__ = [
    "DISCOVERY_FORECAST_HOURS",
    "RunMetadata",
    "available_runs",
    "complete_catalog",
    "discover_available_runs",
    "discover_latest_run",
    "get_s3_client",
    "latest_run",
    "router",
]
