"""Point inspection and full time-series meteogram extraction.

Orchestrates concurrent GRIB byte-range fetches, in-memory decode, grid
sampling, and unit conversion so a 264-hour meteogram can be assembled in a
single request.  The hot path is:

1. Resolve the target lat/lon onto the domain grid (cached transformers).
2. ``asyncio.gather`` every ``(element, fhour)`` message fetch.
3. Decode + bilinear-sample each message in a thread pool.
4. Convert GRIB SI units → imperial/metric and shape the JSON payload.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.config import settings
from app.core.catalog import DOMAIN_CATALOG, ELEMENT_CATALOG
from app.logging_config import get_logger
from app.probe.elements import (
    DEFAULT_POINT_ELEMENTS,
    METEOGRAM_SERIES,
    PTYPE_LABELS,
    ProbeElementPlan,
    probe_element_plan,
)
from app.probe.sampler import GridIndex, GridSampler, OutOfDomainError, get_grid_sampler
from app.tiles.cache import get_grid_cache
from app.tiles.grib import Grid, decode_message
from app.tiles.source import GridRequest, get_data_source

__all__ = [
    "MeteogramPoint",
    "MeteogramResponse",
    "PointProbeResponse",
    "ProbeService",
    "get_probe_service",
    "meteogram_forecast_hours",
    "parse_cycle",
    "valid_time_utc",
]

log = get_logger(__name__)

UnitsSystem = Literal["imperial", "metric"]
SampleMethod = Literal["nearest", "bilinear"]


# ── Forecast-hour layout ──────────────────────────────────────────────────────
# Meteogram cadence (product brief):
#   hourly    f001–f036
#   3-hourly  f039–f072
#   6-hourly  f078–f264
_METEOGRAM_INTERVALS: tuple[tuple[int, int, int], ...] = (
    (1, 36, 1),
    (39, 72, 3),
    (78, 264, 6),
)


def meteogram_forecast_hours(
    start: int = 1,
    end: int = 264,
) -> list[int]:
    """NBM meteogram forecast hours clipped to ``[start, end]``.

    Layout:
      * hourly   f001–f036
      * 3-hourly f039–f072
      * 6-hourly f078–f264
    """
    start = max(0, int(start))
    end = min(int(end), settings.nbm_max_forecast_hour)
    if end < start:
        return []

    hours: list[int] = []
    for lo, hi, step in _METEOGRAM_INTERVALS:
        for h in range(lo, hi + 1, step):
            if start <= h <= end:
                hours.append(h)
    return hours


def parse_cycle(value: str) -> str:
    """Normalise a cycle string to ``YYYYMMDDHH``."""
    text = value.strip().replace("-", "").replace(":", "").replace("T", "")
    if len(text) not in (8, 10) or not text.isdigit():
        raise ValueError(f"cycle must be YYYYMMDDHH, got {value!r}")
    if len(text) == 8:
        text += "00"
    hour = int(text[8:10])
    if hour > 23:
        raise ValueError(f"cycle hour must be 00-23, got {hour:02d}")
    return text


def valid_time_utc(cycle: str, fhour: int) -> datetime:
    """Cycle init time + forecast hour → aware UTC datetime."""
    init = datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return init + timedelta(hours=int(fhour))


# ── Unit conversion helpers ───────────────────────────────────────────────────
_MS_PER_KT = 0.5144444444444444
_MM_PER_IN = 25.4
_M_PER_FT = 0.3048


def _to_display(
    value: float,
    plan: ProbeElementPlan,
    units: UnitsSystem,
) -> float:
    """Convert a GRIB-native scalar into the requested display system."""
    kind = plan.unit_kind
    v = float(value)

    if kind == "temperature":
        # GRIB is Kelvin.
        f = (v - 273.15) * 9.0 / 5.0 + 32.0
        if units == "metric":
            return (f - 32.0) * 5.0 / 9.0
        return f

    if kind == "length":
        inches = v / _MM_PER_IN
        return inches * _MM_PER_IN if units == "metric" else inches

    if kind == "speed":
        knots = v / _MS_PER_KT
        return knots * _MS_PER_KT if units == "metric" else knots

    if kind == "distance":
        # GRIB metres → feet (imperial) or metres (metric).
        return v if units == "metric" else v / _M_PER_FT

    if kind == "direction":
        # Degrees, wrap to [0, 360).
        return float(v % 360.0)

    if kind in ("percent", "energy", "reflectivity", "index", "duration", "pressure"):
        return v

    # unknown — pass through
    return v


def _round(value: float | None, precision: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), precision)


# ── Response models (plain dicts for FastAPI JSON) ────────────────────────────
@dataclass
class PointValue:
    element: str
    name: str
    value: float | None
    units: str
    raw_grib: float | None = None
    formatted: str | None = None
    category: str | None = None  # e.g. precip type label
    missing: bool = False

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "element": self.element,
            "name": self.name,
            "value": self.value,
            "units": self.units,
            "missing": self.missing,
        }
        if self.formatted is not None:
            body["formatted"] = self.formatted
        if self.category is not None:
            body["category"] = self.category
        if self.raw_grib is not None:
            body["raw_grib"] = self.raw_grib
        return body


@dataclass
class PointProbeResponse:
    lat: float
    lon: float
    domain: str
    cycle: str
    fhour: int
    valid_time_utc: str
    method: str
    grid_index: dict[str, Any]
    values: dict[str, dict[str, Any]]
    # Convenience top-level summary matching the product brief.
    summary: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "domain": self.domain,
            "cycle": self.cycle,
            "forecast_hour": self.fhour,
            "valid_time_utc": self.valid_time_utc,
            "method": self.method,
            "grid_index": self.grid_index,
            "values": self.values,
            "summary": self.summary,
            "timings_ms": self.timings_ms,
        }


@dataclass
class MeteogramPoint:
    forecast_hour: int
    valid_time_utc: str
    temperature: float | None = None
    dewpoint: float | None = None
    max_temperature: float | None = None
    min_temperature: float | None = None
    qpf: float | None = None
    qpf_p10: float | None = None
    qpf_p50: float | None = None
    qpf_p90: float | None = None
    snow: float | None = None
    snow_p10: float | None = None
    snow_p50: float | None = None
    snow_p90: float | None = None
    ice: float | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    wind_gust: float | None = None
    sky_cover: float | None = None
    precip_type: int | None = None
    precip_type_label: str | None = None
    pop: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "forecast_hour": self.forecast_hour,
            "valid_time_utc": self.valid_time_utc,
            "temperature": self.temperature,
            "dewpoint": self.dewpoint,
            "max_temperature": self.max_temperature,
            "min_temperature": self.min_temperature,
            "qpf": self.qpf,
            "qpf_percentiles": {
                "p10": self.qpf_p10,
                "p50": self.qpf_p50,
                "p90": self.qpf_p90,
            },
            "snow": self.snow,
            "snow_percentiles": {
                "p10": self.snow_p10,
                "p50": self.snow_p50,
                "p90": self.snow_p90,
            },
            "ice": self.ice,
            "wind_speed": self.wind_speed,
            "wind_direction": self.wind_direction,
            "wind_gust": self.wind_gust,
            "sky_cover": self.sky_cover,
            "precip_type": self.precip_type,
            "precip_type_label": self.precip_type_label,
            "pop": self.pop,
        }


@dataclass
class MeteogramResponse:
    lat: float
    lon: float
    domain: str
    cycle: str
    start_fhour: int
    end_fhour: int
    forecast_hours: list[int]
    units: str
    unit_labels: dict[str, str]
    series: list[dict[str, Any]]
    grid_index: dict[str, Any]
    method: str
    timings_ms: dict[str, float] = field(default_factory=dict)
    missing_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "domain": self.domain,
            "cycle": self.cycle,
            "start_fhour": self.start_fhour,
            "end_fhour": self.end_fhour,
            "forecast_hours": self.forecast_hours,
            "units": self.units,
            "unit_labels": self.unit_labels,
            "series": self.series,
            "grid_index": self.grid_index,
            "method": self.method,
            "point_count": len(self.series),
            "missing_count": self.missing_count,
            "timings_ms": self.timings_ms,
        }


# ── Point-value cache ─────────────────────────────────────────────────────────
# Meteograms touch ~80 hours × ~18 elements.  Caching full float grids for that
# set would need gigabytes; caching the *scalar* at the click point is tiny and
# is what makes the warm 264-hour payload land under the 600 ms budget.
@dataclass
class _ValueCache:
    max_entries: int = 8192

    def __post_init__(self) -> None:
        self._data: OrderedDict[str, tuple[float | None, bool]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[float | None, bool] | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            self._data.move_to_end(key)
            return hit

    def put(self, key: str, value: float | None, nodata: bool) -> None:
        with self._lock:
            self._data[key] = (value, nodata)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# ── Service ───────────────────────────────────────────────────────────────────
class ProbeService:
    """Fetch + sample NBM fields at a single geographic point."""

    def __init__(
        self,
        *,
        sampler: GridSampler | None = None,
        max_concurrency: int = 64,
    ) -> None:
        self.sampler = sampler or get_grid_sampler()
        self.max_concurrency = max(1, int(max_concurrency))
        self._sem: asyncio.Semaphore | None = None
        self._value_cache = _ValueCache()
        # Reused grid index per (domain, rounded lat/lon, geometry fingerprint).
        self._index_memo: dict[tuple, GridIndex] = {}

    def _semaphore(self) -> asyncio.Semaphore:
        # Lazily bind the semaphore to the running loop.
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrency)
        return self._sem

    # -- public -------------------------------------------------------------
    async def probe_point(
        self,
        *,
        lat: float,
        lon: float,
        domain: str,
        cycle: str,
        fhour: int,
        elements: list[str] | None = None,
        units: UnitsSystem = "imperial",
        method: SampleMethod = "bilinear",
    ) -> PointProbeResponse:
        started = time.perf_counter()
        domain = domain.strip().lower()
        cycle_id = parse_cycle(cycle)
        self._validate_domain_point(domain, lat, lon)

        element_codes = self._resolve_elements(elements)
        plans = {code: probe_element_plan(code) for code in element_codes}

        t_fetch = time.perf_counter()
        samples = await self._sample_many(
            domain=domain,
            cycle=cycle_id,
            lat=lat,
            lon=lon,
            fhours=[fhour],
            plans=plans,
            method=method,
        )
        fetch_ms = (time.perf_counter() - t_fetch) * 1000.0

        # samples keyed by (element, fhour)
        grid_index_payload: dict[str, Any] = {}
        values: dict[str, dict[str, Any]] = {}
        for code, plan in plans.items():
            key = (code, fhour)
            entry = samples.get(key)
            if entry is None:
                pv = PointValue(
                    element=code,
                    name=plan.name,
                    value=None,
                    units=plan.imperial_unit if units == "imperial" else plan.metric_unit,
                    missing=True,
                )
            else:
                raw, index, nodata = entry
                if not grid_index_payload and index is not None:
                    grid_index_payload = self._index_payload(index)
                if nodata or raw is None:
                    pv = PointValue(
                        element=code,
                        name=plan.name,
                        value=None,
                        units=plan.imperial_unit if units == "imperial" else plan.metric_unit,
                        missing=True,
                    )
                else:
                    display = _to_display(raw, plan, units)
                    display = _round(display, plan.precision)
                    unit_label = (
                        plan.imperial_unit if units == "imperial" else plan.metric_unit
                    )
                    category = None
                    formatted = None
                    if plan.variable == "PTYPE" and display is not None:
                        category = PTYPE_LABELS.get(int(round(display)), "unknown")
                        formatted = category
                    elif display is not None:
                        formatted = self._format(display, plan, unit_label)
                    pv = PointValue(
                        element=code,
                        name=plan.name,
                        value=display,
                        units=unit_label,
                        raw_grib=_round(raw, 4),
                        formatted=formatted,
                        category=category,
                        missing=False,
                    )
            values[code] = pv.as_dict()

        summary = self._build_summary(values, units)
        total_ms = (time.perf_counter() - started) * 1000.0

        return PointProbeResponse(
            lat=lat,
            lon=lon,
            domain=domain,
            cycle=cycle_id,
            fhour=fhour,
            valid_time_utc=valid_time_utc(cycle_id, fhour).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            method=method,
            grid_index=grid_index_payload,
            values=values,
            summary=summary,
            timings_ms={"fetch_sample": round(fetch_ms, 2), "total": round(total_ms, 2)},
        )

    async def probe_meteogram(
        self,
        *,
        lat: float,
        lon: float,
        domain: str,
        cycle: str,
        start_fhour: int = 1,
        end_fhour: int = 264,
        units: UnitsSystem = "imperial",
        method: SampleMethod = "bilinear",
    ) -> MeteogramResponse:
        started = time.perf_counter()
        domain = domain.strip().lower()
        cycle_id = parse_cycle(cycle)
        self._validate_domain_point(domain, lat, lon)

        hours = meteogram_forecast_hours(start_fhour, end_fhour)
        if not hours:
            raise ValueError(
                f"no forecast hours in range [{start_fhour}, {end_fhour}]"
            )

        # Build the series → element plan map (skip unknown elements gracefully).
        series_plans: dict[str, ProbeElementPlan] = {}
        element_plans: dict[str, ProbeElementPlan] = {}
        for series_key, element_code in METEOGRAM_SERIES.items():
            assert isinstance(element_code, str)
            try:
                plan = probe_element_plan(element_code)
            except KeyError:
                log.debug("meteogram series %s: element %s missing", series_key, element_code)
                continue
            series_plans[series_key] = plan
            element_plans[plan.element] = plan

        t_fetch = time.perf_counter()
        samples = await self._sample_many(
            domain=domain,
            cycle=cycle_id,
            lat=lat,
            lon=lon,
            fhours=hours,
            plans=element_plans,
            method=method,
        )
        fetch_ms = (time.perf_counter() - t_fetch) * 1000.0

        grid_index_payload: dict[str, Any] = {}
        points: list[MeteogramPoint] = []
        missing = 0

        for fhour in hours:
            kwargs: dict[str, Any] = {
                "forecast_hour": fhour,
                "valid_time_utc": valid_time_utc(cycle_id, fhour).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
            for series_key, plan in series_plans.items():
                entry = samples.get((plan.element, fhour))
                if entry is None:
                    missing += 1
                    kwargs[series_key] = None
                    continue
                raw, index, nodata = entry
                if not grid_index_payload and index is not None:
                    grid_index_payload = self._index_payload(index)
                if nodata or raw is None:
                    missing += 1
                    kwargs[series_key] = None
                    continue
                display = _to_display(raw, plan, units)
                if series_key == "precip_type" and display is not None:
                    code = int(round(display))
                    kwargs["precip_type"] = code
                    kwargs["precip_type_label"] = PTYPE_LABELS.get(code, "unknown")
                else:
                    kwargs[series_key] = _round(display, plan.precision)
            points.append(MeteogramPoint(**kwargs))

        unit_labels = self._meteogram_unit_labels(series_plans, units)
        total_ms = (time.perf_counter() - started) * 1000.0

        return MeteogramResponse(
            lat=lat,
            lon=lon,
            domain=domain,
            cycle=cycle_id,
            start_fhour=hours[0],
            end_fhour=hours[-1],
            forecast_hours=hours,
            units=units,
            unit_labels=unit_labels,
            series=[p.as_dict() for p in points],
            grid_index=grid_index_payload,
            method=method,
            timings_ms={"fetch_sample": round(fetch_ms, 2), "total": round(total_ms, 2)},
            missing_count=missing,
        )

    # -- internals ----------------------------------------------------------
    def _validate_domain_point(self, domain: str, lat: float, lon: float) -> None:
        if domain not in DOMAIN_CATALOG:
            raise KeyError(f"unknown NBM domain {domain!r}")
        if not self.sampler.in_domain_bbox(domain, lat, lon):
            raise OutOfDomainError(
                f"({lat}, {lon}) is outside the {domain} domain footprint"
            )

    @staticmethod
    def _resolve_elements(elements: list[str] | None) -> list[str]:
        if not elements or (len(elements) == 1 and elements[0].lower() == "all"):
            # "all" → every probeable catalog element is heavy; use the rich
            # default set, and only expand to the full catalog when explicitly
            # requested with a second form.  Spec says elements="all" returns
            # all requested elements — honour full catalog for "all".
            if elements and elements[0].lower() == "all":
                return sorted(ELEMENT_CATALOG.keys())
            return list(DEFAULT_POINT_ELEMENTS)

        resolved: list[str] = []
        seen: set[str] = set()
        for raw in elements:
            code = raw.strip().lower()
            if not code or code in seen:
                continue
            if code not in ELEMENT_CATALOG:
                raise KeyError(f"unknown element {raw!r}")
            seen.add(code)
            resolved.append(code)
        if not resolved:
            return list(DEFAULT_POINT_ELEMENTS)
        return resolved

    async def _sample_many(
        self,
        *,
        domain: str,
        cycle: str,
        lat: float,
        lon: float,
        fhours: list[int],
        plans: dict[str, ProbeElementPlan],
        method: SampleMethod,
    ) -> dict[tuple[str, int], tuple[float | None, GridIndex | None, bool]]:
        """Concurrently fetch+sample every (element, fhour) pair.

        Returns ``{(element, fhour): (raw_value, index, nodata)}``.
        """
        # Round lat/lon for the scalar cache key (sub-metre is noise on a
        # 2.5 km grid).
        lat_k = round(lat, 4)
        lon_k = round(lon, 4)

        # Resolve the grid index once up front from domain geometry so cache
        # hits still report coordinates without reloading a GRIB.
        shared_index = self._domain_index(domain, lat, lon)

        sem = self._semaphore()
        tasks = []
        keys: list[tuple[str, int]] = []
        out: dict[tuple[str, int], tuple[float | None, GridIndex | None, bool]] = {}

        for element in plans:
            for fhour in fhours:
                key = (element, fhour)
                cache_key = (
                    f"{domain}:{cycle}:{element}:f{fhour:03d}:"
                    f"{lat_k}:{lon_k}:{method}"
                )
                hit = self._value_cache.get(cache_key)
                if hit is not None:
                    raw, nodata = hit
                    out[key] = (raw, shared_index, nodata)
                    continue
                keys.append(key)
                tasks.append(
                    self._sample_one(
                        sem,
                        domain=domain,
                        cycle=cycle,
                        element=element,
                        fhour=fhour,
                        lat=lat,
                        lon=lon,
                        method=method,
                        value_cache_key=cache_key,
                    )
                )

        if not tasks:
            return out

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for key, result in zip(keys, results):
            if isinstance(result, BaseException):
                # Missing idx entry / decode failure → treat as nodata rather
                # than failing the whole meteogram.
                log.debug("probe sample %s failed: %s", key, result)
                out[key] = (None, shared_index, True)
                continue
            raw, index, nodata = result
            out[key] = (raw, index or shared_index, nodata)
        return out

    def _domain_index(self, domain: str, lat: float, lon: float) -> GridIndex | None:
        """Project lat/lon onto the domain's native grid (no GRIB required)."""
        memo_key = (domain, round(lat, 5), round(lon, 5))
        cached = self._index_memo.get(memo_key)
        if cached is not None:
            return cached
        try:
            index = self.sampler.locate(domain, lat, lon, grid=None)
        except Exception:  # noqa: BLE001
            return None
        if len(self._index_memo) > 512:
            self._index_memo.clear()
        self._index_memo[memo_key] = index
        return index

    async def _sample_one(
        self,
        sem: asyncio.Semaphore,
        *,
        domain: str,
        cycle: str,
        element: str,
        fhour: int,
        lat: float,
        lon: float,
        method: SampleMethod,
        value_cache_key: str,
    ) -> tuple[float | None, GridIndex | None, bool]:
        async with sem:
            grid = await self._load_grid(domain, cycle, element, fhour)
            # Sampling is a handful of float ops — keep it on the event-loop
            # thread.  asyncio.to_thread per cell was dominating warm-cache
            # meteogram latency.
            raw, index, nodata = self._sample_grid(grid, domain, lat, lon, method)
            self._value_cache.put(value_cache_key, raw, nodata)
            return raw, index, nodata

    def _sample_grid(
        self,
        grid: Grid,
        domain: str,
        lat: float,
        lon: float,
        method: SampleMethod,
    ) -> tuple[float | None, GridIndex | None, bool]:
        index = self.sampler.locate(domain, lat, lon, grid=grid)
        if not index.inside:
            return None, index, True
        result = self.sampler.sample(grid, index, method=method)
        return result.value, index, result.nodata

    async def _load_grid(
        self, domain: str, cycle: str, element: str, fhour: int
    ) -> Grid:
        """Fetch one decoded grid for sampling.

        Prefers ``source.grid()`` when available (synthetic fast path) so a
        264-hour meteogram does not pay for hundreds of GRIB encode/decode
        round-trips.  Production S3/local sources fall back to message+decode.

        Grids are **not** inserted into the tile layer-1 cache here: a full
        meteogram would thrash that budget (80 × 18 float arrays).  The probe
        keeps a compact scalar cache instead.
        """
        request = GridRequest(domain=domain, cycle=cycle, element=element, fhour=fhour)
        # Opportunistic read of the tile grid cache (shared warm tiles help).
        cache = get_grid_cache()
        hit = cache.get(request.cache_key)
        if hit is not None:
            return hit

        source = get_data_source()
        grid_fn = getattr(source, "grid", None)
        if callable(grid_fn):
            return await grid_fn(request)
        message = await source.message(request)
        return await asyncio.to_thread(decode_message, message)

    @staticmethod
    def _index_payload(index: GridIndex) -> dict[str, Any]:
        return {
            "row": round(index.row, 4),
            "col": round(index.col, 4),
            "row_i": index.row_i,
            "col_i": index.col_i,
            "x": round(index.x, 3),
            "y": round(index.y, 3),
            "inside": index.inside,
        }

    @staticmethod
    def _format(value: float, plan: ProbeElementPlan, unit: str) -> str:
        if plan.variable == "WDIR":
            return f"{value:.0f}°"
        if plan.precision == 0:
            return f"{value:.0f}{unit}"
        return f"{value:.{plan.precision}f}{unit}"

    @staticmethod
    def _build_summary(
        values: dict[str, dict[str, Any]], units: UnitsSystem
    ) -> dict[str, Any]:
        """Human-friendly rollup: Temperature, Dewpoint, Wind, Gust, QPF, Sky."""

        def _v(code: str) -> float | None:
            entry = values.get(code)
            if not entry or entry.get("missing"):
                return None
            return entry.get("value")

        def _u(code: str) -> str:
            entry = values.get(code) or {}
            return str(entry.get("units") or "")

        temp = _v("tmp")
        dpt = _v("dpt")
        wind = _v("wind")
        wdir = _v("wdir")
        gust = _v("gust")
        qpf = _v("qpf_1h") or _v("qpf_6h")
        sky = _v("sky")

        wind_str = None
        if wind is not None:
            if wdir is not None:
                wind_str = f"{wind:.0f} {_u('wind')} @ {wdir:.0f}°"
            else:
                wind_str = f"{wind:.0f} {_u('wind')}"

        return {
            "temperature": (
                f"{temp:.1f}{_u('tmp')}" if temp is not None else None
            ),
            "dewpoint": f"{dpt:.1f}{_u('dpt')}" if dpt is not None else None,
            "wind": wind_str,
            "wind_gust": f"{gust:.0f} {_u('gust')}" if gust is not None else None,
            "qpf": f"{qpf:.2f}{_u('qpf_1h') or _u('qpf_6h')}" if qpf is not None else None,
            "sky_cover": f"{sky:.0f}%" if sky is not None else None,
            "units": units,
        }

    @staticmethod
    def _meteogram_unit_labels(
        series_plans: dict[str, ProbeElementPlan], units: UnitsSystem
    ) -> dict[str, str]:
        labels: dict[str, str] = {}
        for key, plan in series_plans.items():
            labels[key] = (
                plan.imperial_unit if units == "imperial" else plan.metric_unit
            )
        return labels


# ── Per-key load locks (shared pattern with the tile renderer) ────────────────
_GRID_LOCKS: dict[str, asyncio.Lock] = {}


def _grid_lock(key: str) -> asyncio.Lock:
    lock = _GRID_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        if len(_GRID_LOCKS) > 2048:
            _GRID_LOCKS.clear()
        _GRID_LOCKS[key] = lock
    return lock


_service: ProbeService | None = None


def get_probe_service() -> ProbeService:
    global _service
    if _service is None:
        _service = ProbeService()
    return _service


def reset_probe_service() -> None:
    """Drop the singleton (tests)."""
    global _service
    if _service is not None:
        _service._value_cache.clear()
    _service = None
