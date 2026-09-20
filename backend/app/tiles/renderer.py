"""On-the-fly tile rendering: GRIB2 slice → Web Mercator → RGBA → WebP.

Pipeline for ``/{domain}/{cycle}/{element}/{fhour}/{z}/{x}/{y}.webp``:

1. **Bounds check.**  If the tile does not overlap the domain footprint the
   request is answered before a single byte of GRIB is touched.
2. **Layer-2 lookup.**  A cached WebP buffer short-circuits everything below.
3. **Resolve + decode.**  The data source turns the ``.idx`` entry into one
   GRIB2 byte range; :func:`app.tiles.grib.decode_message` reads it through
   ``rasterio.io.MemoryFile`` — never a temporary file.  The decoded grid is
   memoised in layer 1, so a whole pan shares one decode.
4. **Reproject.**  ``rasterio.warp.reproject`` warps the native grid (CONUS is
   Lambert Conformal Conic, EPSG:6372) into the tile's EPSG:3857 window with
   **bilinear** resampling.  That is what removes the stepped, blocky look you
   get from nearest-neighbour on a 2.5 km source grid.
5. **Colour + alpha.**  The field is normalised into the ramp's canonical
   units and mapped through :mod:`app.tiles.colormaps`; no-data and
   below-threshold pixels (QPF < 0.01 in, reflectivity < 5 dBZ …) become fully
   transparent so the basemap shows through.
6. **Encode.**  256×256 (or 512×512) RGBA to WebP.  WebP is roughly 30 %
   smaller than PNG at equal quality and decodes faster in the browser.

``opacity``, ``smooth`` and ``units`` are query parameters; all three are part
of the layer-2 cache key so two renders never collide.
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import rasterio
from morecantile import tms
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds

from app.config import settings
from app.logging_config import get_logger
from app.tiles import colormaps
from app.tiles.cache import (
    EMPTY_TILE_MARKER,
    cycle_is_latest,
    get_grid_cache,
    get_tile_cache,
    is_empty_marker,
    tile_key,
)
from app.tiles.grib import Grid, decode_message
from app.tiles.source import (
    GridRequest,
    RenderPlan,
    get_data_source,
    render_plan,
)

__all__ = [
    "OUT_OF_BOUNDS",
    "DomainNotFound",
    "RenderedTile",
    "TileRequest",
    "TileRenderer",
    "empty_tile_payload",
    "get_tile_renderer",
    "tile_bounds_4326",
    "tile_bounds_3857",
]

log = get_logger(__name__)

WEB_MERCATOR = CRS.from_epsg(3857)
WGS84 = CRS.from_epsg(4326)
WEB_TMS = tms.get("WebMercatorQuad")

WEBP_MEDIA = "image/webp"


class DomainNotFound(KeyError):
    """The requested NBM domain is not published."""


@dataclass(frozen=True)
class TileRequest:
    """A validated single-tile render request."""

    domain: str
    cycle: str
    element: str
    fhour: int
    z: int
    x: int
    y: int
    opacity: float = 1.0
    smooth: bool = True
    units: Literal["imperial", "metric"] = "imperial"
    tilesize: int = 256
    colormap: str | None = None

    @property
    def grid_request(self) -> GridRequest:
        return GridRequest(
            domain=self.domain,
            cycle=self.cycle,
            element=self.element,
            fhour=self.fhour,
        )

    def cache_key(self, colormap: str | None = None) -> str:
        return tile_key(
            self.domain,
            self.cycle,
            self.element,
            self.fhour,
            self.z,
            self.x,
            self.y,
            self.units,
            opacity=self.opacity,
            smooth=self.smooth,
            colormap=colormap,
            tilesize=self.tilesize if self.tilesize != 256 else None,
        )


@dataclass
class RenderedTile:
    """Result of a render, with the bookkeeping the endpoint needs."""

    payload: bytes
    media_type: str = WEBP_MEDIA
    from_cache: bool = False
    empty: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    colormap: str | None = None

    @property
    def total_ms(self) -> float:
        return sum(self.timings.values()) * 1000.0


# Sentinel returned (not sent) when a tile misses the domain footprint.
OUT_OF_BOUNDS = RenderedTile(payload=b"", empty=True)


@dataclass(frozen=True)
class _EmptyTile:
    """Cached 1×1 fully-transparent WebP, served when the caller wants a body."""

    payload: bytes


_EMPTY: _EmptyTile | None = None


def empty_tile_payload() -> bytes:
    """A 1×1 fully transparent WebP, built once and reused."""
    global _EMPTY
    if _EMPTY is None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(buffer, format="WEBP", lossless=True)
        _EMPTY = _EmptyTile(payload=buffer.getvalue())
    return _EMPTY.payload


def tile_bounds_4326(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """``(west, south, east, north)`` of a tile in WGS84 degrees."""
    bounds = WEB_TMS.bounds(x, y, z)
    return (bounds.left, bounds.bottom, bounds.right, bounds.top)


def tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """``(west, south, east, north)`` of a tile in Web Mercator metres."""
    bounds = WEB_TMS.xy_bounds(x, y, z)
    return (bounds.left, bounds.bottom, bounds.right, bounds.top)


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


class TileRenderer:
    """Stateless renderer; caches live in :mod:`app.tiles.cache`."""

    # -- bounds ------------------------------------------------------------
    def domain_bounds_4326(self, domain: str) -> tuple[float, float, float, float]:
        from app.core.catalog import DOMAIN_CATALOG

        meta = DOMAIN_CATALOG.get(domain.lower())
        if meta is None:
            raise DomainNotFound(domain)
        return meta.bbox  # type: ignore[return-value]

    def tile_intersects_domain(self, request: TileRequest) -> bool:
        """Cheap footprint test used to answer out-of-domain tiles instantly."""
        try:
            domain = self.domain_bounds_4326(request.domain)
        except DomainNotFound:
            return False
        return _overlaps(tile_bounds_4326(request.z, request.x, request.y), domain)

    # -- public API --------------------------------------------------------
    async def render(self, request: TileRequest) -> RenderedTile:
        """Render one tile, using both cache tiers."""
        started = time.perf_counter()
        timings: dict[str, float] = {}

        # 1. Bounds check — before any cache or network work.
        t0 = time.perf_counter()
        if not self.tile_intersects_domain(request):
            return RenderedTile(
                payload=b"", empty=True, timings={"bounds": time.perf_counter() - t0}
            )
        timings["bounds"] = time.perf_counter() - t0

        plan = render_plan(request.element)
        colormap_name = request.colormap or plan.colormap

        # 2. Layer 2 — finished WebP buffer.
        key = request.cache_key(colormap_name)
        tile_cache = get_tile_cache()
        t0 = time.perf_counter()
        cached = await asyncio.to_thread(tile_cache.get, key)
        timings["cache_lookup"] = time.perf_counter() - t0
        if cached is not None:
            empty = is_empty_marker(cached)
            return RenderedTile(
                payload=b"" if empty else cached,
                empty=empty,
                from_cache=True,
                timings=timings,
                colormap=colormap_name,
            )

        # 3. Layer 1 — decoded grid (shared by every tile of this variable).
        t0 = time.perf_counter()
        grid = await self._grid(request, plan)
        timings["grid"] = time.perf_counter() - t0

        # 4-6. Warp + colormap + encode (CPU bound; keep the loop free).
        t0 = time.perf_counter()
        payload = await asyncio.to_thread(self._render_payload, grid, request, plan, colormap_name)
        timings["render"] = time.perf_counter() - t0

        # Negative caching: an empty tile is stored as a sentinel so the next
        # request for it is a hit rather than another 30-50 ms warp.  The live
        # cycle expires on the same clock as the Cache-Control we send.
        ttl = settings.tile_cache_ttl_latest_seconds if cycle_is_latest(request.cycle) else None
        await asyncio.to_thread(
            tile_cache.set,
            key,
            payload if payload is not None else EMPTY_TILE_MARKER,
            ttl,
        )

        timings["total"] = time.perf_counter() - started
        return RenderedTile(
            payload=payload or empty_tile_payload(),
            empty=payload is None,
            from_cache=False,
            timings=timings,
            colormap=colormap_name,
        )

    # -- internals ---------------------------------------------------------
    async def _grid(self, request: TileRequest, plan: RenderPlan) -> Grid:
        """Layer-1 lookup, decoding at most once per key per process."""
        cache = get_grid_cache()
        key = request.grid_request.cache_key
        hit = cache.get(key)
        if hit is not None:
            return hit

        lock = _grid_lock(key)
        async with lock:
            hit = cache.peek(key)
            if hit is not None:
                return hit
            source = get_data_source()
            message = await source.message(request.grid_request)
            grid = await asyncio.to_thread(decode_message, message)
            cache.put(key, grid)
            log.debug(
                "decoded %s: %dx%d %s (%d B message)",
                key,
                grid.width,
                grid.height,
                grid.crs.to_epsg() or grid.crs.to_proj4(),
                len(message),
            )
            return grid

    def _render_payload(
        self,
        grid: Grid,
        request: TileRequest,
        plan: RenderPlan,
        colormap_name: str,
    ) -> bytes | None:
        """Synchronous warp → colormap → WebP.  ``None`` means "nothing to draw"."""
        left, bottom, right, top = tile_bounds_3857(request.z, request.x, request.y)

        warped = self.warp_to_tile(
            grid, (left, bottom, right, top), request.tilesize, smooth=request.smooth
        )
        if not np.isfinite(warped).any():
            # The tile lands inside the domain bbox but off the grid itself
            # (ocean tiles around Alaska/Hawaii are the common case).
            return None

        values, _system = colormaps.grib_to_colormap_units(
            warped, plan.unit_kind, plan.grib_unit  # type: ignore[arg-type]
        )
        rgba = colormaps.apply(values, colormap_name, units=request.units, opacity=request.opacity)
        if not rgba[:, :, 3].any():
            return None

        return encode_webp(
            rgba,
            quality=settings.tile_webp_quality,
            lossless=settings.tile_webp_lossless,
            method=settings.tile_webp_method,
        )

    @staticmethod
    def warp_to_tile(
        grid: Grid,
        bounds_3857: tuple[float, float, float, float],
        tilesize: int,
        *,
        smooth: bool = True,
        resampling: Resampling | None = None,
    ) -> np.ndarray:
        """Reproject a native grid into a Web Mercator tile window.

        Bilinear resampling is the default: the NBM source grid is 2.5 km, so
        a z=8 tile covers several source cells and nearest-neighbour would
        paint visible blocks.  Out-of-coverage pixels stay NaN.
        """
        left, bottom, right, top = bounds_3857
        dst_transform = from_bounds(left, bottom, right, top, tilesize, tilesize)
        destination = np.full((tilesize, tilesize), np.nan, dtype=np.float32)

        source = grid.values
        src_nodata = grid.nodata
        if src_nodata is None:
            # GRIB messages without an explicit nodata still carry fill values;
            # NaN is what the colormapper treats as transparent.
            src_nodata = float("nan")

        reproject(
            source=np.nan_to_num(source, nan=float(src_nodata) if np.isfinite(src_nodata) else 0.0),
            destination=destination,
            src_transform=grid.transform,
            src_crs=grid.crs,
            src_nodata=src_nodata if np.isfinite(src_nodata) else None,
            dst_transform=dst_transform,
            dst_crs=WEB_MERCATOR,
            dst_nodata=np.nan,
            # NB: Resampling.nearest == 0 is falsy — an `or` default here would
            # silently upgrade an explicit nearest request to bilinear.
            resampling=Resampling.bilinear if resampling is None else resampling,
            num_threads=settings.tile_warp_threads,
        )

        if smooth:
            destination = _nan_aware_blur(destination, radius=settings.tile_smooth_radius)
        return destination


# ── Helpers ───────────────────────────────────────────────────────────────────
_GRID_LOCKS: dict[str, asyncio.Lock] = {}


def _grid_lock(key: str) -> asyncio.Lock:
    """One lock per grid key, so a first paint decodes each variable once."""
    lock = _GRID_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        if len(_GRID_LOCKS) > 512:  # keep the dict bounded
            _GRID_LOCKS.clear()
        _GRID_LOCKS[key] = lock
    return lock


def _nan_aware_blur(values: np.ndarray, radius: int = 1) -> np.ndarray:
    """Plus-shaped box blur that ignores NaN instead of spreading it.

    Applied to the *data* rather than the RGBA image, so blurred edges stay on
    the palette (a smooth QPF boundary, not smeared colour).  Cost is a handful
    of full-array ops on a 256×256 tile — well under a millisecond.

    Edges are replicated rather than wrapped: a naive ``np.roll`` would bleed
    the right-hand column of a tile into its left-hand column.
    """
    if radius <= 0:
        return values

    originally_valid = np.isfinite(values)
    filled = np.where(originally_valid, values, 0.0).astype(np.float32)
    weight = originally_valid.astype(np.float32)
    height, width = filled.shape

    for _pass in range(radius):
        padded = np.pad(filled, 1, mode="edge")
        padded_weight = np.pad(weight, 1, mode="edge")
        accum = filled.copy()
        count = weight.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            accum += padded[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width]
            count += padded_weight[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width]
        safe = count > 0
        filled = np.where(safe, accum / np.maximum(count, 1.0), 0.0).astype(np.float32)
        # A pixel that had no value of its own inherits its neighbours', so the
        # next pass can use it.
        weight = np.where(safe, np.minimum(count, 1.0), 0.0).astype(np.float32)

    return np.where(originally_valid, filled, np.nan).astype(np.float32, copy=False)


def encode_webp(
    rgba: np.ndarray,
    *,
    quality: int = 82,
    lossless: bool = False,
    method: int = 4,
) -> bytes:
    """Encode an RGBA array as WebP.

    Lossy WebP is the default: at equal perceptual quality it is ~30 % smaller
    than PNG, which matters for a map that loads a few hundred tiles per pan.
    Alpha is not quantised away, so semi-transparent ramps survive.
    """
    from PIL import Image

    image = Image.fromarray(np.ascontiguousarray(rgba), mode="RGBA")
    buffer = io.BytesIO()
    if lossless:
        image.save(buffer, format="WEBP", lossless=True, quality=100, method=method)
    else:
        image.save(
            buffer,
            format="WEBP",
            quality=int(quality),
            exact=True,  # preserve fully-transparent pixels instead of clearing RGB
            method=int(method),
        )
    return buffer.getvalue()


_renderer: TileRenderer | None = None


def get_tile_renderer() -> TileRenderer:
    global _renderer
    if _renderer is None:
        _renderer = TileRenderer()
    return _renderer


def domain_bounds_3857(domain: str) -> tuple[float, float, float, float]:
    """The domain footprint in Web Mercator — used by the capabilities route."""
    bbox = get_tile_renderer().domain_bounds_4326(domain)
    return transform_bounds(  # type: ignore[return-value]
        WGS84, WEB_MERCATOR, *bbox, densify_pts=21
    )


# ``rasterio`` is imported for its ``MemoryFile``-backed GRIB reader and for
# ``warp``; referenced here so linters keep the dependency visible.
_ = rasterio.__version__
