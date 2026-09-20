"""Map-tile rendering service.

The rendering pipeline mirrors production: a georeferenced raster surface is
opened with rio-tiler, the requested web-mercator tile is read (reprojected /
resampled on the fly), a colour ramp is applied and encoded to PNG.

Until the GRIB/COG materialisation milestone lands a real NBM dataset source,
``get_reader()`` returns an in-memory demo surface (a synthetic temperature-like
field over CONUS) so the full rio-tiler path is exercised end-to-end and the
frontend has real tiles to paint. The swap point is a single method.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import numpy as np
from rasterio.io import MemoryFile
from rasterio.profiles import Profile
from rasterio.transform import from_bounds

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# Lazy rio-tiler imports so config/services stay importable without GDAL.
try:
    from rio_tiler.io import Reader
    from rio_tiler.utils import render  # noqa: F401  (kept for parity)

    _HAS_RIO_TILER = True
except Exception as _exc:  # noqa: BLE001 — GDAL/rasterio optional in some contexts
    Reader = None  # type: ignore[assignment]
    log.warning("rio-tiler unavailable; tile rendering disabled: %s", _exc)
    _HAS_RIO_TILER = False

DEMO_NX, DEMO_NY = 96, 96


def _demo_dataset_bytes() -> bytes:
    """Build a small EPSG:4326 GeoTIFF in memory (synthetic CONUS field)."""
    minx, miny, maxx, maxy = (-126.0, 20.0, -66.0, 50.0)
    profile = Profile(
        driver="GTiff",
        dtype="float32",
        nodata=-9999.0,
        width=DEMO_NX,
        height=DEMO_NY,
        count=1,
        crs="EPSG:4326",
        transform=from_bounds(minx, miny, maxx, maxy, DEMO_NX, DEMO_NY),
    )

    # Temperature-like synthetic field: warm south, cooler north, gentle
    # topographically-flavoured waves so tiles aren't flat colour fields.
    lons = np.linspace(minx, maxx, DEMO_NX)
    lats = np.linspace(maxy, miny, DEMO_NY)  # descending
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    field = (
        20.0
        - 0.55 * (lat_grid - 38.0)
        + 8.0 * np.cos(np.deg2rad(lon_grid - 97.0))
        + 2.5 * np.sin(np.deg2rad(lat_grid * 2.0))
        + 4.0 * np.cos(np.deg2rad((lon_grid + lat_grid) * 0.5))
    ).astype("float32")

    with MemoryFile() as memfile:
        with memfile.open(**profile) as dst:
            dst.write(field, 1)
        # memfile.read() copies the complete GeoTIFF bytes out, so the bytes
        # survive the MemoryFile's deallocation and can be reopened later.
        return memfile.read()


class TileRenderer:
    """Renders a single {z}/{x}/{y} tile as PNG bytes."""

    _demo_bytes: bytes | None = None

    @property
    def rio_tiler_available(self) -> bool:
        return _HAS_RIO_TILER and Reader is not None

    @contextmanager
    def get_reader(self) -> Iterator[object]:
        """Yield a rio-tiler Reader for the active NBM surface.

        Modes (swap point for the data milestone):
          * demo — in-memory synthetic CONUS field (current)
          * cog  — /vsicurl/<NODD COG> or fsspec S3 handler (future)
          * grib — materialised Zarr/GeoTIFF from decoded GRIB2 (future)
        """
        if not self.rio_tiler_available:
            raise RuntimeError("rio-tiler/rasterio is not installed in this environment")

        if self._demo_bytes is None:
            self._demo_bytes = _demo_dataset_bytes()

        with MemoryFile(self._demo_bytes) as memfile:
            with memfile.open() as src_dataset:
                # rio-tiler >=7 accepts an open dataset via the `dataset` kwarg
                # (positional `input` must be a path). The dataset stays open
                # for the context-managed reader's lifespan.
                yield Reader(None, dataset=src_dataset)

    def render_tile(
        self,
        z: int,
        x: int,
        y: int,
        *,
        tilesize: int | None = None,
        colormap: dict[int, tuple[int, int, int, int]] | None = None,
    ) -> bytes:
        """Read + render one tile. Returns PNG bytes."""
        size = tilesize or settings.tile_size
        with self.get_reader() as reader:
            image = reader.tile(x, y, z, tilesize=size)
            if colormap:
                image = image.apply_colormap(colormap)
            return image.render(img_format="PNG")

    def render_preview(
        self,
        *,
        width: int = 256,
        height: int = 256,
        colormap: dict[int, tuple[int, int, int, int]] | None = None,
    ) -> bytes:
        """Full-field thumbnail (used by the layer legend / style endpoint)."""
        with self.get_reader() as reader:
            image = reader.preview(width=width, height=height)
            if colormap:
                image = image.apply_colormap(colormap)
            return image.render(img_format="PNG")


_renderer_singleton: TileRenderer | None = None


def get_tile_renderer() -> TileRenderer:
    global _renderer_singleton
    if _renderer_singleton is None:
        _renderer_singleton = TileRenderer()
    return _renderer_singleton
