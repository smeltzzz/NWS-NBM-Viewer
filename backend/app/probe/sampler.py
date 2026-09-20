"""Coordinate conversion and grid sampling for point probes.

Given a WGS84 ``(latitude, longitude)`` and an NBM domain, this module:

1. Transforms the coordinate into the domain's native CRS via
   :class:`pyproj.Transformer`.
2. Maps the projected position onto the GRIB grid using the message's
   geotransform (row/col, fractional).
3. Returns either the nearest cell or a 4-point bilinear interpolation.

A process-wide :class:`GridSampler` caches the per-domain CRS transformers and
a lightweight coordinate→index lookup so repeated clicks on the same domain
skip the projection setup cost.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Literal

import pyproj
from rasterio.crs import CRS
from rasterio.transform import Affine, xy

from app.core.catalog import DOMAIN_CATALOG
from app.logging_config import get_logger
from app.tiles.grib import Grid
from app.tiles.source import domain_grid

__all__ = [
    "GridIndex",
    "GridSampler",
    "OutOfDomainError",
    "SampleResult",
    "clear_sampler_cache",
    "get_grid_sampler",
]

log = get_logger(__name__)

SampleMethod = Literal["nearest", "bilinear"]


class OutOfDomainError(ValueError):
    """The requested lat/lon does not fall on the domain grid."""


@dataclass(frozen=True)
class GridIndex:
    """Fractional (and integer) location of a point on a native grid."""

    row: float
    col: float
    row_i: int
    col_i: int
    x: float  # projected easting / longitude
    y: float  # projected northing / latitude
    inside: bool

    @property
    def as_tuple(self) -> tuple[int, int]:
        return (self.row_i, self.col_i)


@dataclass(frozen=True)
class SampleResult:
    """One sampled scalar plus the grid index it came from."""

    value: float | None
    index: GridIndex
    method: SampleMethod
    nodata: bool = False


@dataclass(frozen=True)
class _DomainProjection:
    """Cached CRS + transformer pair for one NBM domain."""

    code: str
    crs: CRS
    to_native: pyproj.Transformer
    to_wgs84: pyproj.Transformer
    # Approximate grid geometry used when a live GRIB has not been decoded yet
    # (bounds checks before the first fetch).
    width: int
    height: int
    transform: Affine
    bbox_4326: tuple[float, float, float, float]


class GridSampler:
    """High-speed lat/lon → grid index + value sampler.

    Transformers and domain footprints are memoised.  Per-request the caller
    still supplies the live :class:`~app.tiles.grib.Grid` (with its own
    transform/CRS from the GRIB message) so production and synthetic paths
    share one code path.
    """

    def __init__(self) -> None:
        self._projections: dict[str, _DomainProjection] = {}
        self._lock = threading.RLock()
        # (domain, round(lat,4), round(lon,4), width, height, transform_hash)
        # → GridIndex.  Bounded manually below.
        self._index_cache: dict[tuple, GridIndex] = {}
        self._index_cache_max = 4096

    # ── Domain projection cache ───────────────────────────────────────────
    def projection(self, domain: str) -> _DomainProjection:
        code = domain.strip().lower()
        cached = self._projections.get(code)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._projections.get(code)
            if cached is not None:
                return cached
            proj = self._build_projection(code)
            self._projections[code] = proj
            return proj

    @staticmethod
    def _build_projection(code: str) -> _DomainProjection:
        meta = DOMAIN_CATALOG.get(code)
        if meta is None:
            raise KeyError(f"unknown NBM domain {code!r}")
        if meta.bbox is None:
            raise KeyError(f"domain {code!r} has no bounding box")

        # Prefer the synthetic/domain_grid CRS so probe and tiler agree when
        # NOAA is unreachable; live GRIB CRS still wins at sample time.
        try:
            grid = domain_grid(code)
            crs = grid.crs
            width, height = grid.width, grid.height
            transform = grid.transform
        except Exception:  # noqa: BLE001 — fall back to EPSG/catalog
            if meta.epsg:
                crs = CRS.from_epsg(meta.epsg)
            else:
                crs = CRS.from_epsg(4326)
            width = height = 0
            transform = Affine.identity()

        to_native = pyproj.Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
        to_wgs84 = pyproj.Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
        return _DomainProjection(
            code=code,
            crs=crs,
            to_native=to_native,
            to_wgs84=to_wgs84,
            width=width,
            height=height,
            transform=transform,
            bbox_4326=meta.bbox,
        )

    # ── Footprint test ────────────────────────────────────────────────────
    def in_domain_bbox(self, domain: str, lat: float, lon: float) -> bool:
        """Cheap geographic bbox test (degrees) before any GRIB work."""
        west, south, east, north = self.projection(domain).bbox_4326
        # Alaska/Oceanic can cross the antimeridian; treat lon carefully.
        if west <= east:
            lon_ok = west <= lon <= east
        else:  # wraps (not currently used, but safe)
            lon_ok = lon >= west or lon <= east
        return lon_ok and south <= lat <= north

    # ── Index resolution ──────────────────────────────────────────────────
    def locate(
        self,
        domain: str,
        lat: float,
        lon: float,
        *,
        grid: Grid | None = None,
    ) -> GridIndex:
        """Project ``(lat, lon)`` onto the domain grid and return indices.

        When ``grid`` is supplied (preferred), the GRIB message's own CRS and
        affine transform are authoritative.  Otherwise the cached domain
        geometry is used for a pre-fetch bounds estimate.
        """
        if not math.isfinite(lat) or not math.isfinite(lon):
            raise ValueError("latitude and longitude must be finite")
        if not -90.0 <= lat <= 90.0:
            raise ValueError(f"latitude {lat} out of range [-90, 90]")
        if not -180.0 <= lon <= 180.0:
            raise ValueError(f"longitude {lon} out of range [-180, 180]")

        if grid is not None:
            crs = grid.crs
            transform = grid.transform
            height, width = grid.height, grid.width
            # Build a short-lived transformer; cache key includes CRS epsg/proj.
            transformer = pyproj.Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
        else:
            proj = self.projection(domain)
            crs = proj.crs
            transform = proj.transform
            height, width = proj.height, proj.width
            transformer = proj.to_native

        cache_key = (
            domain.lower(),
            round(lat, 5),
            round(lon, 5),
            width,
            height,
            hash(
                (
                    float(transform.a),
                    float(transform.b),
                    float(transform.c),
                    float(transform.d),
                    float(transform.e),
                    float(transform.f),
                    crs.to_string(),
                )
            ),
        )
        hit = self._index_cache.get(cache_key)
        if hit is not None:
            return hit

        x, y = transformer.transform(lon, lat)
        # Fractional row/col via the inverse affine.  rasterio.rowcol rounds;
        # we want the continuous position for bilinear weights.
        #   x = c + a*col + b*row
        #   y = f + d*col + e*row
        a, b, c, d, e, f = (
            transform.a,
            transform.b,
            transform.c,
            transform.d,
            transform.e,
            transform.f,
        )
        det = a * e - b * d
        if abs(det) < 1e-18:
            raise ValueError("degenerate geotransform; cannot invert")
        col_f = (e * (x - c) - b * (y - f)) / det
        row_f = (-d * (x - c) + a * (y - f)) / det

        row_i = int(round(row_f))
        col_i = int(round(col_f))
        inside = 0 <= row_f < height and 0 <= col_f < width if height and width else False
        # Also accept nearest-cell-inside when the fractional position is within
        # half a cell of the edge (common for points near the domain rim).
        if not inside and height and width:
            inside = 0 <= row_i < height and 0 <= col_i < width

        index = GridIndex(
            row=float(row_f),
            col=float(col_f),
            row_i=row_i,
            col_i=col_i,
            x=float(x),
            y=float(y),
            inside=inside,
        )
        self._remember(cache_key, index)
        return index

    def _remember(self, key: tuple, index: GridIndex) -> None:
        with self._lock:
            if len(self._index_cache) >= self._index_cache_max:
                # Drop an arbitrary half to keep inserts cheap.
                for stale in list(self._index_cache.keys())[: self._index_cache_max // 2]:
                    self._index_cache.pop(stale, None)
            self._index_cache[key] = index

    # ── Value sampling ────────────────────────────────────────────────────
    def sample(
        self,
        grid: Grid,
        index: GridIndex,
        *,
        method: SampleMethod = "bilinear",
    ) -> SampleResult:
        """Read one value from ``grid`` at ``index``.

        ``nearest`` snaps to the closest cell.  ``bilinear`` blends the four
        surrounding cells (falling back to nearest on the rim).
        """
        values = grid.values
        height, width = values.shape
        nodata = grid.nodata

        def _is_nodata(v: float) -> bool:
            if not math.isfinite(v):
                return True
            if nodata is not None and math.isfinite(nodata) and v == nodata:
                return True
            return False

        if method == "nearest" or not index.inside:
            r, c = index.row_i, index.col_i
            if not (0 <= r < height and 0 <= c < width):
                return SampleResult(value=None, index=index, method="nearest", nodata=True)
            raw = float(values[r, c])
            if _is_nodata(raw):
                return SampleResult(value=None, index=index, method="nearest", nodata=True)
            return SampleResult(value=raw, index=index, method="nearest", nodata=False)

        # Bilinear: weight the four surrounding cells.
        row_f, col_f = index.row, index.col
        r0 = int(math.floor(row_f))
        c0 = int(math.floor(col_f))
        r1 = r0 + 1
        c1 = c0 + 1
        # Clamp to the valid window; if the point is outside entirely, bail.
        if r0 < -1 or c0 < -1 or r1 > height or c1 > width:
            return SampleResult(value=None, index=index, method="bilinear", nodata=True)

        dr = row_f - r0
        dc = col_f - c0
        corners = (
            (r0, c0, (1.0 - dr) * (1.0 - dc)),
            (r0, c1, (1.0 - dr) * dc),
            (r1, c0, dr * (1.0 - dc)),
            (r1, c1, dr * dc),
        )
        accum = 0.0
        weight = 0.0
        for r, c, w in corners:
            if w <= 0.0:
                continue
            if not (0 <= r < height and 0 <= c < width):
                continue
            raw = float(values[r, c])
            if _is_nodata(raw):
                continue
            accum += raw * w
            weight += w

        if weight <= 1e-12:
            # All four corners missing — try pure nearest as a last resort.
            return self.sample(grid, index, method="nearest")

        return SampleResult(
            value=float(accum / weight),
            index=index,
            method="bilinear",
            nodata=False,
        )

    def sample_latlon(
        self,
        domain: str,
        lat: float,
        lon: float,
        grid: Grid,
        *,
        method: SampleMethod = "bilinear",
    ) -> SampleResult:
        """Convenience: locate + sample in one call."""
        index = self.locate(domain, lat, lon, grid=grid)
        return self.sample(grid, index, method=method)

    def clear(self) -> None:
        with self._lock:
            self._projections.clear()
            self._index_cache.clear()


# ── Process singleton ─────────────────────────────────────────────────────────
_sampler: GridSampler | None = None
_sampler_lock = threading.Lock()


def get_grid_sampler() -> GridSampler:
    global _sampler
    if _sampler is None:
        with _sampler_lock:
            if _sampler is None:
                _sampler = GridSampler()
    return _sampler


def clear_sampler_cache() -> None:
    """Drop cached transformers / index lookups (tests)."""
    sampler = get_grid_sampler()
    sampler.clear()


def cell_center_lonlat(grid: Grid, row: int, col: int) -> tuple[float, float]:
    """Return the WGS84 ``(lon, lat)`` of the centre of cell ``(row, col)``."""
    x, y = xy(grid.transform, row, col, offset="center")
    transformer = pyproj.Transformer.from_crs(grid.crs, CRS.from_epsg(4326), always_xy=True)
    lon, lat = transformer.transform(x, y)
    return float(lon), float(lat)
