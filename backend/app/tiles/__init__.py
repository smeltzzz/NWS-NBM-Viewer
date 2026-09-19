"""High-performance raster tiling and colormapping pipeline.

    app.tiles.colormaps  official NWS/WPC/SPC ramps + vectorised palette mapper
    app.tiles.grib       in-memory GRIB2 decode/encode via rasterio.MemoryFile
    app.tiles.source     .idx byte-range resolution (S3 / local / synthetic)
    app.tiles.cache      layer-1 decoded-grid LRU + layer-2 WebP tile store
    app.tiles.renderer   warp (LCC -> EPSG:3857) + colormap + WebP encode

The HTTP surface lives in ``app.api.endpoints.tiles``.
"""

from __future__ import annotations

from app.tiles import colormaps, grib, source
from app.tiles.cache import (
    GridCache,
    TileCache,
    cache_control_headers,
    cycle_is_latest,
    get_grid_cache,
    get_tile_cache,
    EMPTY_TILE_MARKER,
    is_empty_marker,
    tile_key,
)
from app.tiles.grib import Grid, decode_message, encode_message
from app.tiles.renderer import RenderedTile, TileRequest, TileRenderer, get_tile_renderer
from app.tiles.source import GridRequest, RenderPlan, get_data_source, render_plan

__all__ = [
    "Grid",
    "GridCache",
    "GridRequest",
    "RenderPlan",
    "RenderedTile",
    "TileCache",
    "TileRenderer",
    "TileRequest",
    "cache_control_headers",
    "colormaps",
    "cycle_is_latest",
    "decode_message",
    "encode_message",
    "get_data_source",
    "get_grid_cache",
    "get_tile_renderer",
    "get_tile_cache",
    "grib",
    "render_plan",
    "source",
    "tile_key",
]
