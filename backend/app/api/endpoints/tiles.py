"""``GET /api/v1/tiles/{domain}/{cycle}/{element}/{fhour}/{z}/{x}/{y}.webp``

The production tile endpoint.  Path parameters select the forecast; query
parameters tune the render.  Three cheap guards run before any GRIB work:

1. **Domain bounds.**  A tile that does not overlap the domain footprint gets
   ``204 No Content`` (or a 1×1 transparent WebP with ``?empty=image``) — no
   S3 request, no decode, no warp.
2. **Zoom / x / y range.**  Out-of-range indices are rejected with ``422``
   rather than producing an empty tile that a client would cache.
3. **Element support.**  Unknown elements are ``404``; known elements with no
   colour ramp assigned are ``422`` with a pointer to ``/capabilities``.

``Cache-Control`` is ``public, max-age=86400, immutable`` for historical runs
and ``public, max-age=300`` for the cycle still being published — a finished
NBM cycle can never change, so its tiles are CDN-safe forever.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response

from app.config import settings
from app.core.s3_client import S3Throttled
from app.logging_config import get_logger
from app.tiles import colormaps
from app.tiles.cache import (
    cache_control_headers,
    cycle_is_latest,
    get_grid_cache,
    get_tile_cache,
    reset_caches,
)
from app.tiles.renderer import (
    DomainNotFound,
    RenderedTile,
    TileRequest,
    empty_tile_payload,
    get_tile_renderer,
)
from app.tiles.source import (
    ElementNotRenderable,
    get_data_source,
    known_domains,
    render_plan,
    renderable_elements,
)

log = get_logger(__name__)

router = APIRouter(prefix="/tiles", tags=["tiles"])

WEBP_MEDIA = "image/webp"


# ── Parameter parsing ─────────────────────────────────────────────────────────
def _parse_cycle(value: str) -> str:
    """``YYYYMMDDHH`` (``YYYY-MM-DD-HH`` also accepted)."""
    text = value.strip().replace("-", "").replace(":", "").replace("T", "")
    if len(text) not in (8, 10) or not text.isdigit():
        raise HTTPException(status_code=422, detail=f"cycle must be YYYYMMDDHH, got {value!r}")
    if len(text) == 8:
        text += "00"
    hour = int(text[8:10])
    if hour > 23:
        raise HTTPException(status_code=422, detail=f"cycle hour must be 00-23, got {hour:02d}")
    return text


def _parse_fhour(value: str) -> int:
    """``24`` or ``f024``."""
    text = value.strip().lower().lstrip("f")
    if not text.isdigit():
        raise HTTPException(status_code=422, detail=f"fhour must be an integer, got {value!r}")
    hour = int(text)
    if not 0 <= hour <= settings.nbm_max_forecast_hour:
        raise HTTPException(
            status_code=422,
            detail=f"fhour {hour} outside [0, {settings.nbm_max_forecast_hour}]",
        )
    return hour


def _validate_zxy(z: int, x: int, y: int) -> None:
    if not settings.tile_min_zoom <= z <= settings.tile_max_zoom:
        raise HTTPException(
            status_code=422,
            detail=(f"zoom {z} outside [{settings.tile_min_zoom}, " f"{settings.tile_max_zoom}]"),
        )
    limit = 2**z
    if not 0 <= x < limit or not 0 <= y < limit:
        raise HTTPException(
            status_code=422,
            detail=f"x/y must be in [0, {limit - 1}] at zoom {z}",
        )


# ── Tile endpoint ─────────────────────────────────────────────────────────────
@router.get(
    "/{domain}/{cycle}/{element}/{fhour}/{z}/{x}/{y}.webp",
    summary="Render one NBM forecast tile as WebP",
    responses={
        200: {"content": {WEBP_MEDIA: {}}, "description": "WebP tile"},
        204: {"description": "Tile does not intersect the domain"},
    },
)
async def tile(
    response: Response,
    domain: str = Path(description="NBM domain: co, ak, hi, pr, gu, oc"),
    cycle: str = Path(description="Model run as YYYYMMDDHH"),
    element: str = Path(description="Catalog element code, e.g. tmp, qpf_24h"),
    fhour: str = Path(description="Forecast hour, e.g. 24 or f024"),
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    opacity: float = Query(1.0, ge=0.0, le=1.0, description="Layer alpha multiplier"),
    smooth: bool = Query(True, description="Bilinear warp + data-space smoothing"),
    units: Literal["imperial", "metric"] = Query(
        "imperial", description="Unit system for conversion and legend"
    ),
    tilesize: int = Query(256, description="Output tile edge in pixels (256 or 512)"),
    empty: Literal["no_content", "image"] = Query(
        settings.tile_empty_response,
        description="Response for a tile outside the domain",
    ),
) -> Response:
    """Render a single Web Mercator tile from an NBM GRIB2 message."""
    started = time.perf_counter()

    domain_code = domain.strip().lower()
    if domain_code not in known_domains():
        raise HTTPException(
            status_code=404,
            detail=f"unknown NBM domain {domain!r}; expected one of {known_domains()}",
        )

    cycle_id = _parse_cycle(cycle)
    forecast_hour = _parse_fhour(fhour)
    _validate_zxy(z, x, y)

    if tilesize not in (256, 512):
        raise HTTPException(status_code=422, detail=f"tilesize must be 256 or 512, got {tilesize}")

    element_code = element.strip().lower()
    try:
        plan = render_plan(element_code)
    except ElementNotRenderable as exc:
        # Unknown element vs. known-but-not-paintable: 404 vs. 422.
        from app.core.catalog import ELEMENT_CATALOG

        if element_code not in ELEMENT_CATALOG:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request = TileRequest(
        domain=domain_code,
        cycle=cycle_id,
        element=element_code,
        fhour=forecast_hour,
        z=z,
        x=x,
        y=y,
        opacity=opacity,
        smooth=smooth,
        units=units,
        tilesize=tilesize,
    )

    renderer = get_tile_renderer()
    try:
        rendered: RenderedTile = await renderer.render(request)
    except HTTPException:
        raise
    except LookupError as exc:
        # Index entry missing / object not published yet.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DomainNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown domain: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except S3Throttled as exc:
        # NOAA/S3 is rate-limiting or unhealthy *right now*. Tell browsers
        # (and the nginx edge cache: proxy_cache_use_stale) to hold their
        # last good copy and come back shortly instead of stampeding.
        retry_after = int(exc.retry_after or settings.poller_backoff_base_seconds)
        log.warning("tile upstream throttled for %s: %s", request.cache_key(plan.colormap), exc)
        raise HTTPException(
            status_code=503,
            detail="upstream NOAA traffic limit hit; retry shortly",
            headers={"Retry-After": str(max(5, min(retry_after, 300)))},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — raster failures must not 500 the API
        log.exception("tile render failed for %s", request.cache_key(plan.colormap))
        raise HTTPException(
            status_code=503,
            detail=f"tile render failed: {exc}",
            headers={"Retry-After": "30"},
        ) from exc

    headers = cache_control_headers(
        is_latest=cycle_is_latest(cycle_id), served_from_cache=rendered.from_cache
    )
    headers["X-Tile-Cache"] = "hit" if rendered.from_cache else "miss"
    headers["X-Tile-Element"] = plan.colormap
    headers["X-Tile-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    if rendered.empty:
        headers["X-Tile-Empty"] = "1"

    if rendered.empty:
        if empty == "no_content":
            return Response(status_code=204, headers=headers)
        return Response(content=empty_tile_payload(), media_type=WEBP_MEDIA, headers=headers)

    return Response(content=rendered.payload, media_type=WEBP_MEDIA, headers=headers)


# ── Service metadata ──────────────────────────────────────────────────────────
@router.get("/capabilities", summary="Describe the tile service")
async def capabilities() -> dict[str, object]:
    """Everything a client needs to build a layer selector and URL template."""
    renderer = get_tile_renderer()
    domains = {}
    for code in known_domains():
        try:
            domains[code] = {
                "bbox4326": list(renderer.domain_bounds_4326(code)),
            }
        except DomainNotFound:  # pragma: no cover - defensive
            continue

    return {
        "urlTemplate": (
            f"{settings.api_prefix}/tiles/{{domain}}/{{cycle}}/{{element}}/"
            "{fhour}/{z}/{x}/{y}.webp"
        ),
        "format": "webp",
        "tileSizes": [256, 512],
        "minZoom": settings.tile_min_zoom,
        "maxZoom": settings.tile_max_zoom,
        "maxForecastHour": settings.nbm_max_forecast_hour,
        "domains": domains,
        "elements": renderable_elements(),
        "colormaps": colormaps.describe(),
        "units": ["imperial", "metric"],
        "query": {
            "opacity": "float 0-1, default 1.0",
            "smooth": "bool, default true",
            "units": "imperial | metric",
            "tilesize": "256 | 512",
            "empty": "no_content | image",
        },
        "dataSource": get_data_source().name,
        "cache": {
            "historical": f"public, max-age={settings.tile_cache_max_age_historical}, immutable",
            "latest": f"public, max-age={settings.tile_cache_max_age_latest}",
            "latestCycleToleranceHours": settings.latest_cycle_tolerance_hours,
        },
    }


@router.get("/colormaps", summary="List colour ramps")
async def list_colormaps() -> dict[str, object]:
    return {"colormaps": colormaps.describe()}


@router.get("/colormaps/{name}", summary="Legend stops for one colour ramp")
async def colormap_legend(
    name: str,
    units: Literal["imperial", "metric"] = Query("imperial"),
) -> dict[str, object]:
    try:
        return colormaps.legend(name, units=units)  # type: ignore[return-value]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/cache", summary="Cache tier statistics")
async def cache_stats(
    reset: bool = Query(False, description="Drop both cache tiers first"),
) -> dict[str, object]:
    if reset:
        reset_caches()
    return {
        "grids": get_grid_cache().stats().as_dict(),
        "tiles": get_tile_cache().stats().as_dict(),
        "tileBackend": get_tile_cache().resolved_backend,
    }
