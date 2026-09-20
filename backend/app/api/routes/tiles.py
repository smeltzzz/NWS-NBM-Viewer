"""XYZ tile endpoints.

    /api/v1/tiles/demo/{z}/{x}/{y}.png
    /api/v1/tiles/preview.png

These endpoints call through the tile renderer and return cache-friendly PNGs.
The demo surface is replaced by real NBM data in the GRIB/COG milestone; the
wire contract (path params + query params) is designed to survive that swap.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app import colormaps as palettes
from app.config import settings
from app.logging_config import get_logger
from app.services.cache import cache_key, get_cache
from app.services.tiles import get_tile_renderer

log = get_logger(__name__)

router = APIRouter(prefix="/tiles", tags=["tiles"])

PNG_MEDIA = "image/png"


def _validate_zxy(z: int, x: int, y: int) -> None:
    if not (settings.tile_min_zoom <= z <= settings.tile_max_zoom):
        raise HTTPException(
            status_code=422,
            detail=f"Zoom {z} outside [{settings.tile_min_zoom}, {settings.tile_max_zoom}]",
        )
    n = 2**z
    if not (0 <= x < n) or not (0 <= y < n):
        raise HTTPException(
            status_code=422, detail=f"Tile x/y out of range for zoom {z} (0..{n - 1})"
        )


def _resolve_colormap(name: str) -> dict[int, tuple[int, int, int, int]]:
    """Normalise a ramp name into a rio-tiler colormap or raise a clean 400/404."""
    if name not in palettes.COLOURMAPS:
        raise HTTPException(status_code=404, detail=f"Unknown colour map '{name}'")
    try:
        return palettes.to_rio_tiler_colormap(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown colour map '{name}'")
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"Colour engine unavailable: {exc}")


@router.get("/demo/{z}/{x}/{y}.png", summary="Render a demo map tile (PNG)")
def demo_tile(
    z: int,
    x: int,
    y: int,
    colormap: str = Query(default="nbm_temp", description="Colour ramp name"),
) -> Response:
    """Render one XYZ tile from the active surface into a PNG response."""
    _validate_zxy(z, x, y)
    cmap = _resolve_colormap(colormap)
    cache = get_cache()
    key = cache_key("tile", "demo", z, x, y, colormap)

    def _render() -> bytes:
        return get_tile_renderer().render_tile(z, x, y, colormap=cmap)

    try:
        png = cache.get_or_set(key, _render, ttl_seconds=settings.tile_cache_ttl_seconds)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — rasters can fail for many reasons
        log.exception("tile render failed for %s/%s/%s", z, x, y)
        raise HTTPException(status_code=500, detail=f"Tile render failed: {exc}") from exc

    return Response(
        content=png,
        media_type=PNG_MEDIA,
        headers={"Cache-Control": f"public, max-age={settings.tile_cache_ttl_seconds}"},
    )


@router.get("/preview.png", summary="Render a full-field preview (PNG)")
def preview(
    colormap: str = Query(default="nbm_temp"),
    width: int = Query(default=256, ge=16, le=1024),
    height: int = Query(default=256, ge=16, le=1024),
) -> Response:
    """Thumbnail of the whole active surface — used by legend/selector UI."""
    cmap = _resolve_colormap(colormap)
    cache = get_cache()
    key = cache_key("preview", colormap, width, height)

    def _render() -> bytes:
        return get_tile_renderer().render_preview(width=width, height=height, colormap=cmap)

    try:
        png = cache.get_or_set(key, _render, ttl_seconds=settings.tile_cache_ttl_seconds)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("preview render failed")
        raise HTTPException(status_code=500, detail=f"Preview render failed: {exc}") from exc

    return Response(
        content=png,
        media_type=PNG_MEDIA,
        headers={"Cache-Control": f"public, max-age={settings.tile_cache_ttl_seconds}"},
    )


# NOTE: the service-wide capability document now lives in
# app.api.endpoints.tiles (the GRIB/WebP pipeline).  This one only describes
# the legacy demo PNG surface.
@router.get("/demo/capabilities", summary="Describe the demo PNG surface")
def capabilities() -> dict[str, object]:
    return {
        "format": "png",
        "tilesize": settings.tile_size,
        "minZoom": settings.tile_min_zoom,
        "maxZoom": settings.tile_max_zoom,
        "colormaps": palettes.COLOURMAPS,
        "rendering": settings.colormap_mode,
        "source": "demo",  # becomes 'nbm' once the GRIB materialiser lands
    }
