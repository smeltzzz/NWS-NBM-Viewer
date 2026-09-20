"""``GET /api/v1/probe/point`` and ``GET /api/v1/probe/meteogram``.

Map-click inspection: sample exact values at a lat/lon for one forecast hour,
or pull the full 264-hour meteogram time series for the same location.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from app.config import settings
from app.core.catalog import DOMAIN_CATALOG
from app.logging_config import get_logger
from app.probe.elements import DEFAULT_POINT_ELEMENTS, METEOGRAM_SERIES, probeable_elements
from app.probe.sampler import OutOfDomainError
from app.probe.service import (
    get_probe_service,
    meteogram_forecast_hours,
    parse_cycle,
)

log = get_logger(__name__)

router = APIRouter(prefix="/probe", tags=["probe"])

UnitsSystem = Literal["imperial", "metric"]
SampleMethod = Literal["nearest", "bilinear"]


def _parse_elements(raw: str | None) -> list[str] | None:
    if raw is None or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or None


def _parse_fhour(value: int) -> int:
    if not 0 <= value <= settings.nbm_max_forecast_hour:
        raise HTTPException(
            status_code=422,
            detail=f"fhour {value} outside [0, {settings.nbm_max_forecast_hour}]",
        )
    return value


def _validate_latlon(lat: float, lon: float) -> None:
    if not -90.0 <= lat <= 90.0:
        raise HTTPException(status_code=422, detail=f"lat {lat} outside [-90, 90]")
    if not -180.0 <= lon <= 180.0:
        raise HTTPException(status_code=422, detail=f"lon {lon} outside [-180, 180]")


def _validate_domain(domain: str) -> str:
    code = domain.strip().lower()
    if code not in DOMAIN_CATALOG:
        raise HTTPException(
            status_code=404,
            detail=f"unknown NBM domain {domain!r}; expected one of {sorted(DOMAIN_CATALOG)}",
        )
    return code


# ── Point inspection ──────────────────────────────────────────────────────────
@router.get(
    "/point",
    summary="Sample NBM elements at a single lat/lon and forecast hour",
    response_description="Instant values for the requested elements",
)
async def probe_point(
    response: Response,
    lat: float = Query(..., description="Latitude in WGS84 degrees"),
    lon: float = Query(..., description="Longitude in WGS84 degrees"),
    domain: str = Query("co", description="NBM domain: co, ak, hi, pr, gu, oc"),
    cycle: str = Query(..., description="Model run as YYYYMMDDHH"),
    fhour: int = Query(24, description="Forecast hour (0–264)"),
    elements: str | None = Query(
        None,
        description=(
            "Comma-separated catalog element codes, or 'all'. "
            f"Default: {','.join(DEFAULT_POINT_ELEMENTS)}"
        ),
    ),
    units: UnitsSystem = Query("imperial", description="Display unit system"),
    method: SampleMethod = Query("bilinear", description="Grid sampling: bilinear or nearest"),
) -> dict:
    """Return instant values at ``(lat, lon)`` for one forecast hour.

    Example summary fields: Temperature 72.4°F, Dewpoint 54.1°F,
    Wind 12 kt @ 240°, Wind Gust 22 kt, QPF 0.00", Sky Cover 45%.
    """
    started = time.perf_counter()
    _validate_latlon(lat, lon)
    domain_code = _validate_domain(domain)
    try:
        cycle_id = parse_cycle(cycle)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    forecast_hour = _parse_fhour(fhour)

    try:
        element_list = _parse_elements(elements)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    service = get_probe_service()
    try:
        result = await service.probe_point(
            lat=lat,
            lon=lon,
            domain=domain_code,
            cycle=cycle_id,
            fhour=forecast_hour,
            elements=element_list,
            units=units,
            method=method,
        )
    except OutOfDomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("probe/point failed for %s/%s f%03d", domain_code, cycle_id, forecast_hour)
        raise HTTPException(status_code=503, detail=f"probe failed: {exc}") from exc

    payload = result.as_dict()
    elapsed = (time.perf_counter() - started) * 1000.0
    payload["timings_ms"]["endpoint"] = round(elapsed, 2)
    response.headers["X-Probe-Ms"] = f"{elapsed:.2f}"
    response.headers["Cache-Control"] = "public, max-age=60"
    return payload


# ── Full time-series meteogram ────────────────────────────────────────────────
@router.get(
    "/meteogram",
    summary="Full forecast meteogram time series at a lat/lon",
    response_description="264-hour (or windowed) meteogram JSON",
)
async def probe_meteogram(
    response: Response,
    lat: float = Query(..., description="Latitude in WGS84 degrees"),
    lon: float = Query(..., description="Longitude in WGS84 degrees"),
    domain: str = Query("co", description="NBM domain: co, ak, hi, pr, gu, oc"),
    cycle: str = Query(..., description="Model run as YYYYMMDDHH"),
    start_fhour: int = Query(1, ge=0, le=264, description="First forecast hour"),
    end_fhour: int = Query(264, ge=0, le=264, description="Last forecast hour"),
    units: UnitsSystem = Query("imperial", description="Display unit system"),
    method: SampleMethod = Query("bilinear", description="Grid sampling: bilinear or nearest"),
) -> dict:
    """Return the multi-day forecast curve set for one map location.

    Cadence:
      * hourly f001–f036
      * 3-hourly f039–f072
      * 6-hourly f078–f264

    Each series point carries temperature/dewpoint, max/min envelope, QPF
    percentiles (10/50/90), snow/ice, wind vector + gust, sky cover, and
    precipitation-type flags.  Concurrent byte-range reads keep the full
    264-hour payload under the 600 ms budget on a warm cache.
    """
    started = time.perf_counter()
    _validate_latlon(lat, lon)
    domain_code = _validate_domain(domain)
    try:
        cycle_id = parse_cycle(cycle)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if end_fhour < start_fhour:
        raise HTTPException(
            status_code=422,
            detail=f"end_fhour ({end_fhour}) must be >= start_fhour ({start_fhour})",
        )

    service = get_probe_service()
    try:
        result = await service.probe_meteogram(
            lat=lat,
            lon=lon,
            domain=domain_code,
            cycle=cycle_id,
            start_fhour=start_fhour,
            end_fhour=end_fhour,
            units=units,
            method=method,
        )
    except OutOfDomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "probe/meteogram failed for %s/%s [%d,%d]",
            domain_code,
            cycle_id,
            start_fhour,
            end_fhour,
        )
        raise HTTPException(status_code=503, detail=f"meteogram failed: {exc}") from exc

    payload = result.as_dict()
    elapsed = (time.perf_counter() - started) * 1000.0
    payload["timings_ms"]["endpoint"] = round(elapsed, 2)
    response.headers["X-Probe-Ms"] = f"{elapsed:.2f}"
    response.headers["Cache-Control"] = "public, max-age=60"
    return payload


# ── Wind vector field (particle / barb overlays) ─────────────────────────────
@router.get(
    "/wind-field",
    summary="10 m wind U/V vector grid over a lon/lat viewport",
    response_description="Row-major U and V arrays sampled on a caller-sized lattice",
)
async def probe_wind_field(
    response: Response,
    domain: str = Query("co", description="NBM domain: co, ak, hi, pr, gu, oc"),
    cycle: str = Query(..., description="Model run as YYYYMMDDHH"),
    fhour: int = Query(24, description="Forecast hour (0–264)"),
    min_lon: float = Query(..., description="Viewport west edge (degrees)"),
    min_lat: float = Query(..., description="Viewport south edge (degrees)"),
    max_lon: float = Query(..., description="Viewport east edge (degrees)"),
    max_lat: float = Query(..., description="Viewport north edge (degrees)"),
    cols: int = Query(128, ge=8, le=256, description="Lattice width"),
    rows: int = Query(80, ge=8, le=256, description="Lattice height"),
    units: UnitsSystem = Query("imperial", description="Display unit system"),
) -> dict:
    """Return the zonal/meridional wind components over a map viewport.

    Frontend animated wind-particle and wind-barb layers interpolate these
    raw vector fields in the browser (Canvas/WebGL); the tile endpoint's
    coloured raster cannot be inverted back into usable vector values.
    Row 0 of each array is the northern edge of the bbox; missing cells are
    ``null``.
    """
    started = time.perf_counter()
    domain_code = _validate_domain(domain)
    try:
        cycle_id = parse_cycle(cycle)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    forecast_hour = _parse_fhour(fhour)

    service = get_probe_service()
    try:
        result = await service.probe_wind_field(
            domain=domain_code,
            cycle=cycle_id,
            fhour=forecast_hour,
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            cols=cols,
            rows=rows,
            units=units,
        )
    except OutOfDomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "probe/wind-field failed for %s/%s f%03d", domain_code, cycle_id, forecast_hour
        )
        raise HTTPException(status_code=503, detail=f"wind-field failed: {exc}") from exc

    elapsed = (time.perf_counter() - started) * 1000.0
    result["timings_ms"]["endpoint"] = round(elapsed, 2)
    response.headers["X-Probe-Ms"] = f"{elapsed:.2f}"
    response.headers["Cache-Control"] = "public, max-age=60"
    return result


# ── Service metadata ──────────────────────────────────────────────────────────
@router.get("/capabilities", summary="Describe the point-probe service")
async def probe_capabilities() -> dict:
    """Everything a client needs to build a click-to-inspect UI."""
    hours = meteogram_forecast_hours(1, 264)
    return {
        "endpoints": {
            "point": f"{settings.api_prefix}/probe/point",
            "meteogram": f"{settings.api_prefix}/probe/meteogram",
        },
        "domains": sorted(DOMAIN_CATALOG),
        "defaultPointElements": list(DEFAULT_POINT_ELEMENTS),
        "elements": probeable_elements(),
        "meteogramSeries": {k: v for k, v in METEOGRAM_SERIES.items()},
        "meteogramHours": hours,
        "meteogramHourCount": len(hours),
        "meteogramCadence": [
            {"start": 1, "end": 36, "step": 1},
            {"start": 39, "end": 72, "step": 3},
            {"start": 78, "end": 264, "step": 6},
        ],
        "maxForecastHour": settings.nbm_max_forecast_hour,
        "units": ["imperial", "metric"],
        "methods": ["bilinear", "nearest"],
        "sampling": {
            "default": "bilinear",
            "description": (
                "lat/lon is projected into the domain CRS with pyproj, then "
                "4-point bilinear-interpolated (or nearest-neighbour) on the "
                "native GRIB grid."
            ),
        },
    }
