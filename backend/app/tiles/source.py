"""Resolving ``(domain, cycle, element, fhour)`` into a decoded grid.

Three interchangeable sources sit behind one protocol:

``S3GribSource``
    The production path.  Fetches the small ``.idx`` sidecar, locates the byte
    range for the requested GRIB selector, then issues a single HTTP ``Range``
    request for exactly that message.  Nothing is written to disk.

``LocalGribSource``
    Reads a GRIB2 file from a local directory.  Used for development against
    a downloaded cycle and by CI.

``SyntheticGribSource``
    Builds a deterministic, physically-plausible field and encodes it as real
    GRIB2 in memory.  It exists so the *whole* pipeline — GRIB decode, warp,
    colormap, WebP — is exercisable with no network and no NOAA dependency,
    and so the test-suite runs the same code path as production.

:func:`get_data_source` picks one from ``settings.tile_data_source``
(``"auto"`` prefers S3 and falls back to synthetic when the bucket cannot be
reached).
"""

from __future__ import annotations

import asyncio
import functools
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import numpy as np
import pyproj
from rasterio.crs import CRS
from rasterio.transform import from_origin

from app.config import settings
from app.core.catalog import DOMAIN_CATALOG, ELEMENT_CATALOG
from app.logging_config import get_logger
from app.tiles.grib import Grid, decode_message, encode_message

__all__ = [
    "ElementNotRenderable",
    "GridRequest",
    "RenderPlan",
    "S3GribSource",
    "LocalGribSource",
    "SyntheticGribSource",
    "domain_grid",
    "get_data_source",
    "render_plan",
]

log = get_logger(__name__)


class ElementNotRenderable(ValueError):
    """The catalog element has no colour ramp assigned to it."""


@dataclass(frozen=True)
class GridRequest:
    """Everything needed to fetch one forecast field."""

    domain: str
    cycle: str  # YYYYMMDDHH
    element: str
    fhour: int

    @property
    def date(self) -> str:
        return self.cycle[:8]

    @property
    def hour(self) -> int:
        return int(self.cycle[8:10])

    @property
    def cache_key(self) -> str:
        return f"{self.domain}:{self.cycle}:{self.element}:{self.fhour:03d}"


# ── Element → colormap binding ────────────────────────────────────────────────
@dataclass(frozen=True)
class RenderPlan:
    """How one catalog element is painted and in what units it arrives."""

    element: str
    colormap: str
    #: Unit kind used by :mod:`app.tiles.colormaps` conversions.
    unit_kind: str
    #: Unit symbol the GRIB2 field is published in.
    grib_unit: str
    #: GRIB product stream the element lives in (core or qmd).
    product: str


# Variable → (colormap, unit kind, GRIB unit).  Order matters: the first
# matching rule wins, so probability elements are resolved before this table.
_VARIABLE_PLAN: dict[str, tuple[str, str, str]] = {
    "TMP": ("temperature", "temperature", "K"),
    "MAXT": ("temperature", "temperature", "K"),
    "MINT": ("temperature", "temperature", "K"),
    "DPT": ("temperature", "temperature", "K"),
    "APTMP": ("temperature", "temperature", "K"),
    "HEAT": ("temperature", "temperature", "K"),
    "WCHILL": ("temperature", "temperature", "K"),
    "APCP": ("precip_accum", "length", "mm"),
    "ASNOW": ("snow_accum", "length", "mm"),
    "ICEACCR": ("ice_accum", "length", "mm"),
    "WIND": ("wind_speed", "speed", "m/s"),
    "GUST": ("wind_gust", "speed", "m/s"),
    "CAPE": ("cape", "energy", "J/kg"),
    "REFC": ("reflectivity", "reflectivity", "dBZ"),
    "RH": ("probabilities", "percent", "%"),
    "POP12": ("probabilities", "percent", "%"),
    "POP06": ("probabilities", "percent", "%"),
    "POP01": ("probabilities", "percent", "%"),
    "TCDC": ("probabilities", "percent", "%"),
}


@functools.lru_cache(maxsize=256)
def render_plan(element: str) -> RenderPlan:
    """Resolve a catalog element code to its colour ramp and unit handling.

    Raises :class:`ElementNotRenderable` for elements with no assigned ramp
    (wind direction, echo tops, Haines index …) — the endpoint turns that into
    a 422 rather than guessing a scale.
    """
    meta = ELEMENT_CATALOG.get(element.lower())
    if meta is None:
        raise ElementNotRenderable(
            f"{element!r} is not a known NBM element; see /api/v1/tiles/capabilities"
        )

    if meta.statistical_process == "probability":
        return RenderPlan(element, "probabilities", "percent", "%", meta.master_source)

    variable = (meta.variable or "").upper()
    match = _VARIABLE_PLAN.get(variable)
    if match is None:
        raise ElementNotRenderable(
            f"element {element!r} ({variable or 'unknown variable'}) has no colour "
            f"ramp assigned"
        )
    colormap, unit_kind, grib_unit = match
    return RenderPlan(element, colormap, unit_kind, grib_unit, meta.master_source)


def renderable_elements() -> dict[str, str]:
    """``{element code: colormap name}`` for every paintable element."""
    out: dict[str, str] = {}
    for code in ELEMENT_CATALOG:
        try:
            out[code] = render_plan(code).colormap
        except ElementNotRenderable:
            continue
    return dict(sorted(out.items()))


# ── Native domain grids ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class DomainGrid:
    """Native projection and extent of one NBM publication domain."""

    code: str
    crs: CRS
    transform: object
    width: int
    height: int
    resolution_m: float

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(left, bottom, right, top)`` in the native CRS."""
        import rasterio.transform

        return rasterio.transform.array_bounds(
            self.height, self.width, self.transform
        )


# Native projection per domain, taken from the catalog's own declaration.
# CONUS is the Lambert Conformal Conic grid the NBM publishes as EPSG:6372;
# Alaska is polar stereographic; the island domains are Mercator; the oceanic
# domain is plain lat/lon.  In production the authoritative georeferencing is
# whatever the GRIB2 message carries — this table only shapes the synthetic
# grids used when NOAA is unreachable.
_LCC_6372 = "+proj=lcc +lat_0=12 +lon_0=-102 +lat_1=17.5 +lat_2=29.5 \
+x_0=2500000 +y_0=0 +ellps=GRS80 +units=m +no_defs"
_POLAR_AK = (
    "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-152 +x_0=0 +y_0=0 "
    "+ellps=GRS80 +units=m +no_defs"
)
_MERCATOR = "+proj=merc +lon_0=0 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
_LONLAT = "+proj=longlat +ellps=WGS84 +no_defs"

_PROJECTIONS: dict[str, str] = {
    "co": _LCC_6372,
    "ak": _POLAR_AK,
    "hi": _MERCATOR,
    "pr": _MERCATOR,
    "gu": _MERCATOR,
    "oc": _LONLAT,
}

_METERS_PER_DEGREE = 111_319.49079327358

# Sampling factor applied to the nominal resolution when synthesising.  The
# real NBM grids are what production decodes; the synthetic source only needs
# a grid fine enough to make bilinear resampling meaningful.
_SYNTH_DOWNSAMPLE = 4


def _nominal_resolution_m(meta) -> float:
    """Native grid spacing in metres, tolerating range-valued resolutions."""
    if meta.resolution_km is not None:
        return meta.resolution_km * 1000.0
    low, high = meta.resolution_range_km
    return ((low + high) / 2.0) * 1000.0


@functools.lru_cache(maxsize=16)
def domain_grid(code: str, *, downsample: int = 1) -> DomainGrid:
    """Compute the native grid for a domain from its catalog bounding box."""
    meta = DOMAIN_CATALOG.get(code.lower())
    if meta is None:
        raise KeyError(f"unknown NBM domain {code!r}")

    projection = _PROJECTIONS[meta.code]
    # Prefer the catalog's EPSG code so the synthetic grid and the catalog
    # agree on the CONUS Lambert Conformal definition.
    crs = CRS.from_epsg(meta.epsg) if meta.epsg else CRS.from_proj4(projection)
    west, south, east, north = meta.bbox
    resolution = _nominal_resolution_m(meta) * max(1, downsample)

    if "+proj=longlat" in projection:
        # Degrees, not metres.
        degrees = resolution / _METERS_PER_DEGREE
        width = max(1, int(np.ceil((east - west) / degrees)))
        height = max(1, int(np.ceil((north - south) / degrees)))
        transform = from_origin(west, north, degrees, degrees)
    else:
        transformer = pyproj.Transformer.from_crs(
            CRS.from_epsg(4326), crs, always_xy=True
        )
        xs, ys = transformer.transform(
            [west, east, west, east], [south, south, north, north]
        )
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        width = max(1, int(np.ceil((x_max - x_min) / resolution)))
        height = max(1, int(np.ceil((y_max - y_min) / resolution)))
        transform = from_origin(x_min, y_max, resolution, resolution)

    return DomainGrid(
        code=meta.code,
        crs=crs,
        transform=transform,
        width=width,
        height=height,
        resolution_m=resolution,
    )


def known_domains() -> list[str]:
    return sorted(DOMAIN_CATALOG)


# ── Sources ───────────────────────────────────────────────────────────────────
class DataSource(Protocol):
    """Anything that can turn a :class:`GridRequest` into a :class:`Grid`."""

    name: str

    async def message(self, request: GridRequest) -> bytes:
        """Return the raw GRIB2 bytes for the requested field."""
        ...

    async def close(self) -> None: ...


class S3GribSource:
    """Production source: ``.idx`` byte-range resolution against NODD."""

    name = "s3"

    def __init__(self) -> None:
        # Imported lazily so the module loads in geospatial-free contexts.
        from app.core.s3_client import S3Client

        self._client = S3Client()
        self._index_lock = asyncio.Lock()

    async def message(self, request: GridRequest) -> bytes:
        from app.core.s3_client import IdxEntry, S3ClientError

        plan = render_plan(request.element)
        selector = ELEMENT_CATALOG[request.element].grib_parameter

        async with self._index_lock:
            entries = await self._client.fetch_idx(
                request.date, request.hour, plan.product, request.fhour, request.domain
            )

        entry = _select_entry(entries, selector)
        if entry is None:
            raise LookupError(
                f"GRIB selector {selector!r} for element {request.element!r} is not "
                f"in the {request.domain}/{request.cycle} f{request.fhour:03d} index"
            )
        byte_range = entry.byte_range
        if byte_range is None:
            raise LookupError(
                f"byte range for {selector!r} could not be determined "
                "(the GRIB object size is unknown)"
            )

        key = self._client.grib_key(
            request.date, request.hour, plan.product, request.fhour, request.domain
        )
        start, end = byte_range
        log.debug(
            "byte range %d-%d (%d B) for %s in %s",
            start,
            end,
            end - start + 1,
            selector,
            key,
        )
        try:
            return await self._client.fetch_grib_message(key, start, end)
        except S3ClientError as exc:
            raise LookupError(f"S3 byte-range fetch failed: {exc}") from exc

    async def close(self) -> None:
        await self._client.close()


def _select_entry(entries: Iterable, selector: str):
    """Find the ``.idx`` entry matching a ``:VAR:level:step:`` selector."""
    target = [part.strip() for part in selector.strip(":").split(":") if part.strip()]
    if not target:
        return None

    fallback = None
    for entry in entries:
        if entry.variable.strip().upper() != target[0].upper():
            continue
        if len(target) > 1 and entry.level.strip().lower() != target[1].lower():
            continue
        if len(target) > 2:
            step = (entry.forecast_step or "").strip().lower()
            if step != target[2].lower():
                continue
            return entry
        if fallback is None:
            fallback = entry
    return fallback


class LocalGribSource:
    """Reads GRIB2 files from a local directory."""

    name = "local"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    async def message(self, request: GridRequest) -> bytes:
        plan = render_plan(request.element)
        candidate = (
            self.directory
            / f"blend.t{request.hour:02d}z.{plan.product}."
            f"f{request.fhour:03d}.{request.domain}.grib2"
        )
        if not candidate.is_file():
            raise LookupError(f"no local GRIB2 file at {candidate}")
        return await asyncio.to_thread(candidate.read_bytes)

    async def close(self) -> None:
        return None


class SyntheticGribSource:
    """Deterministic in-memory GRIB2, so the pipeline runs with no network.

    The field is a smooth function of position seeded by
    ``(domain, element, fhour)`` — the same request always produces the same
    grid, which keeps cached tiles reproducible and tests stable.
    """

    name = "synthetic"

    #: Elements whose real-world fields are mostly empty: a large fraction of
    #: the domain sits below the colormap threshold, which is exactly what
    #: exercises the transparency mask.
    _SPARSE: frozenset[str] = frozenset(
        {"precip_accum", "snow_accum", "ice_accum", "reflectivity", "cape"}
    )

    #: Plausible value ranges per colormap, in canonical (imperial) units.
    _RANGES: dict[str, tuple[float, float]] = {
        "temperature": (-10.0, 105.0),
        "precip_accum": (0.0, 6.0),
        "snow_accum": (0.0, 30.0),
        "ice_accum": (0.0, 1.2),
        "wind_speed": (0.0, 55.0),
        "wind_gust": (0.0, 75.0),
        "cape": (0.0, 4500.0),
        "reflectivity": (0.0, 68.0),
        "probabilities": (0.0, 100.0),
    }

    def __init__(self, *, downsample: int | None = None) -> None:
        self.downsample = max(
            1, settings.tile_synthetic_downsample if downsample is None else downsample
        )
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    async def message(self, request: GridRequest) -> bytes:
        return await asyncio.to_thread(self._message_sync, request)

    def _message_sync(self, request: GridRequest) -> bytes:
        with self._lock:
            cached = self._cache.get(request.cache_key)
        if cached is not None:
            return cached

        plan = render_plan(request.element)
        grid = domain_grid(request.domain, downsample=self.downsample)
        low, high = self._RANGES.get(plan.colormap, (0.0, 100.0))

        # Canonical units are converted back to GRIB's SI units so the decode
        # path exercises the same normalisation as a real NOAA message.
        field = self._build_field(grid, request, low, high, plan.colormap)
        field = _imperial_to_grib(field, plan)

        payload = encode_message(
            field,
            crs=grid.crs,
            transform=grid.transform,
            parameter=plan.element.upper(),
        )
        with self._lock:
            # Bound the source's own buffer cache; layer 1 caches the decoded
            # grid, this only avoids re-encoding the same message.
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[request.cache_key] = payload
        return payload

    @staticmethod
    def _build_field(
        grid: DomainGrid,
        request: GridRequest,
        low: float,
        high: float,
        plan_colormap: str,
    ) -> np.ndarray:
        """A smooth, deterministic field with structure worth resampling."""
        height, width = grid.shape
        seed = zlib.crc32(
            f"{request.domain}|{request.element}|{request.fhour}".encode()
        )
        rng = np.random.default_rng(seed)
        # Normalised coordinates in [0, 1]; row 0 is the northern edge.
        xs = np.linspace(0.0, 1.0, width, dtype=np.float32)
        ys = np.linspace(0.0, 1.0, height, dtype=np.float32)
        xx, yy = np.meshgrid(xs, ys)

        phase = float(rng.uniform(0.0, 6.283))
        # Latitude gradient + a few low-frequency waves = continent-scale
        # structure with smooth gradients (bilinear resampling is visible).
        field = (
            0.62 * (1.0 - yy)
            + 0.18 * np.sin(3.1 * xx + phase) * np.cos(2.3 * yy)
            + 0.12 * np.sin(6.7 * yy - 1.4 * xx + phase * 0.5)
            + 0.08 * np.cos(11.3 * xx + 4.1 * yy)
        )
        span = float(np.ptp(field))
        field = np.clip((field - field.min()) / max(span, 1e-9), 0.0, 1.0)

        if plan_colormap in SyntheticGribSource._SPARSE:
            # Real precipitation/reflectivity fields are dominated by "nothing
            # happening": carve out a dry region so a large share of the tile
            # falls under the colormap threshold and renders transparent.
            field = np.clip((field - 0.42) / 0.58, 0.0, 1.0) ** 1.7

        return (low + (high - low) * field.astype(np.float32)).astype(np.float32)

    async def close(self) -> None:
        with self._lock:
            self._cache.clear()


def _imperial_to_grib(field: np.ndarray, plan: RenderPlan) -> np.ndarray:
    """Convert canonical (imperial) synthetic values into GRIB's own units."""
    unit = plan.grib_unit
    if unit == "K":
        return ((field - 32.0) * 5.0 / 9.0 + 273.15).astype(np.float32)
    if unit == "mm":
        return (field * 25.4).astype(np.float32)
    if unit == "m/s":
        return (field * 0.5144444444444444).astype(np.float32)
    return field.astype(np.float32)


class _AutoSource:
    """Prefers S3, falls back to synthetic when the bucket is unreachable."""

    name = "auto"

    def __init__(self) -> None:
        self._primary = S3GribSource()
        self._fallback = SyntheticGribSource()
        self._degraded = False
        self._active: DataSource | None = None

    async def message(self, request: GridRequest) -> bytes:
        if self._active is None:
            self._active = await self._probe()
        try:
            return await self._active.message(request)
        except (LookupError, OSError) as exc:
            if self._active is self._primary and not self._degraded:
                log.warning(
                    "S3 grid fetch failed (%s); serving %s/%s from the synthetic "
                    "source instead",
                    exc,
                    request.domain,
                    request.element,
                )
                self._degraded = True
                self._active = self._fallback
                return await self._active.message(request)
            raise

    async def _probe(self) -> DataSource:
        if not settings.nbm_tile_offline:
            try:
                # A cheap anonymous HEAD against the bucket root proves we can
                # reach NODD without downloading anything.
                import httpx

                async with httpx.AsyncClient(timeout=3.0) as client:
                    response = await client.head(
                        f"https://{settings.s3_bucket_grib}.s3.amazonaws.com/"
                    )
                if response.status_code < 500:
                    return self._primary
            except Exception as exc:  # noqa: BLE001
                log.info("NODD bucket unreachable (%s); using synthetic grids", exc)
        return self._fallback

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()


_source: DataSource | None = None
_source_lock = threading.Lock()


def get_data_source() -> DataSource:
    """Return the process-wide data source named by ``settings``."""
    global _source
    if _source is not None:
        return _source
    with _source_lock:
        if _source is None:
            _source = _build_source(settings.tile_data_source)
            log.info("tile data source: %s", _source.name)
    return _source


def set_data_source(source: DataSource | None) -> None:
    """Override the data source (tests) or reset it with ``None``."""
    global _source
    with _source_lock:
        _source = source


def _build_source(kind: str) -> DataSource:
    kind = (kind or "auto").lower()
    if kind == "s3":
        return S3GribSource()
    if kind == "local":
        return LocalGribSource(settings.tile_grib_dir)
    if kind == "synthetic":
        return SyntheticGribSource()
    return _AutoSource()
