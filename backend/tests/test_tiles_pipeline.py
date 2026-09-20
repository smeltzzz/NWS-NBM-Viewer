"""Tests for the raster tiling + colormapping pipeline (``app/tiles``).

Everything here runs against the *real* pipeline: the synthetic data source
produces genuine GRIB2 bytes, which are decoded through the same
``rasterio.io.MemoryFile`` path a NOAA byte range would take, warped with the
same ``rasterio.warp.reproject`` call, coloured with the same ramps and
encoded with the same WebP encoder.  No network, no NOAA dependency.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.transform import from_origin

from app.config import settings
from app.tiles import colormaps
from app.tiles.cache import (
    GridCache,
    TileCache,
    cache_control_headers,
    cycle_is_latest,
    tile_key,
)
from app.tiles.grib import Grid, GribDecodeError, decode_message, encode_message, iter_messages
from app.tiles.renderer import (
    TileRenderer,
    empty_tile_payload,
    get_tile_renderer,
    tile_bounds_3857,
    tile_bounds_4326,
)
from app.tiles.source import (
    ElementNotRenderable,
    GridRequest,
    SyntheticGribSource,
    domain_grid,
    render_plan,
    renderable_elements,
    set_data_source,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient with an isolated cache directory and the synthetic source."""
    from app.tiles import cache as cache_module

    saved = (
        settings.tile_cache_dir,
        settings.cache_size_limit_gb,
        settings.tile_data_source,
        settings.environment,
    )
    settings.tile_cache_dir = tmp_path_factory.mktemp("tilecache")
    settings.cache_size_limit_gb = 0.05
    settings.tile_data_source = "synthetic"
    settings.environment = "test"

    set_data_source(SyntheticGribSource())
    cache_module.reset_caches()

    from main import app

    with TestClient(app) as test_client:
        yield test_client

    set_data_source(None)
    cache_module.reset_caches()
    (
        settings.tile_cache_dir,
        settings.cache_size_limit_gb,
        settings.tile_data_source,
        settings.environment,
    ) = saved


def _url(*parts: object, **query: object) -> str:
    """Build a tile URL; the final path segment carries the ``.webp`` suffix."""
    path = "/".join(str(part) for part in parts)
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    return f"{settings.api_prefix}/tiles/{path}.webp{'?' + qs if qs else ''}"


# A CONUS tile at z=5 that sits comfortably inside the domain.
INSIDE = (5, 7, 12)
# A z=5 tile over the Arctic — no overlap with any NBM domain.
OUTSIDE = (5, 0, 0)

REQUIRED_RAMPS = {
    "temperature",
    "precip_accum",
    "snow_accum",
    "ice_accum",
    "wind_speed",
    "wind_gust",
    "cape",
    "reflectivity",
    "probabilities",
}


# ── GRIB2 codec ───────────────────────────────────────────────────────────────


def test_grib_roundtrip_is_lossless() -> None:
    """encode_message -> decode_message returns the same numbers and geo-info."""
    grid = domain_grid("co")
    values = np.linspace(-40.0, 120.0, grid.width * grid.height, dtype=np.float32)
    values = values.reshape(grid.shape)

    payload = encode_message(values, crs=grid.crs, transform=grid.transform, parameter="TMP")
    assert payload[:4] == b"GRIB"
    assert payload[-4:] == b"7777"

    decoded = decode_message(payload)
    assert decoded.shape == grid.shape
    # GDAL quantises to the message's packing; a 16-bit simple-packed field on
    # this range resolves to well under a hundredth of a degree.
    assert float(np.abs(decoded.values - values).max()) < 0.01
    assert decoded.source_bytes == len(payload)

    # GRIB2 carries projection *parameters*, not an EPSG code, so the decoded
    # CRS comes back as WKT and the Lambert false easting migrates from the
    # datum into the geotransform.  The property that matters is that the grid
    # still covers exactly the same ground.
    assert "+proj=lcc" in decoded.crs.to_proj4()
    original = transform_bounds(grid.crs, "EPSG:4326", *grid.bounds, densify_pts=21)
    decoded_bounds = transform_bounds(decoded.crs, "EPSG:4326", *decoded.bounds, densify_pts=21)
    assert np.allclose(original, decoded_bounds, atol=1e-3)


def test_decode_rejects_non_grib() -> None:
    with pytest.raises(GribDecodeError, match="does not start with"):
        decode_message(b"not a grib message at all")


def test_iter_messages_splits_concatenated_buffer() -> None:
    grid = domain_grid("pr")
    small = np.ones(grid.shape, dtype=np.float32)
    one = encode_message(small, crs=grid.crs, transform=grid.transform)
    messages = list(iter_messages(one + one))
    assert len(messages) == 2
    assert all(m[:4] == b"GRIB" for m in messages)


def test_decode_from_memory_only() -> None:
    """The synthetic source emits real GRIB2 that decodes in memory."""
    source = SyntheticGribSource()
    request = GridRequest(domain="co", cycle="2026091900", element="tmp", fhour=24)
    payload = source._message_sync(request)  # noqa: SLF001 - exercising the codec
    grid = decode_message(payload)
    assert grid.width > 100 and grid.height > 100
    assert np.isfinite(grid.values).all()


# ── Colormaps ─────────────────────────────────────────────────────────────────


def test_all_required_ramps_registered() -> None:
    assert REQUIRED_RAMPS.issubset(set(colormaps.names()))


def test_reflectivity_matches_nws_table() -> None:
    """The 5-75 dBZ NWS table, verbatim."""
    values = np.array([[4.0, 5.0, 20.0, 45.0, 65.0, 70.0, 75.0]], dtype=np.float32)
    rgba = colormaps.apply(values, "reflectivity")[0]
    assert tuple(rgba[0]) == (0, 0, 0, 0)  # below 5 dBZ: clear air
    assert tuple(rgba[1]) == (4, 233, 231, 255)  # 5 dBZ cyan
    assert tuple(rgba[2]) == (2, 253, 2, 255)  # 20 dBZ green
    assert tuple(rgba[3]) == (253, 149, 0, 255)  # 45 dBZ orange
    assert tuple(rgba[4]) == (248, 0, 253, 255)  # 65 dBZ magenta
    assert tuple(rgba[5]) == (152, 84, 198, 255)  # 70 dBZ purple
    assert tuple(rgba[6]) == (253, 253, 253, 255)  # 75 dBZ white


def test_precip_is_transparent_below_quarter_inch_threshold() -> None:
    values = np.array([[0.0, 0.005, 0.0099, 0.01, 0.30]], dtype=np.float32)
    rgba = colormaps.apply(values, "precip_accum")[0]
    assert tuple(rgba[0]) == (0, 0, 0, 0)
    assert tuple(rgba[1]) == (0, 0, 0, 0)
    assert tuple(rgba[2]) == (0, 0, 0, 0)
    assert rgba[3][3] == 255, "0.01 in exactly must be painted"
    assert rgba[4][3] == 255


def test_snow_and_ice_thresholds_and_over_colour() -> None:
    snow = colormaps.apply(np.array([[0.05, 0.5, 60.0]], np.float32), "snow_accum")[0]
    assert tuple(snow[0]) == (0, 0, 0, 0)  # < 0.1 in
    assert tuple(snow[1]) == (189, 215, 231, 255)  # light blue
    assert tuple(snow[2]) == (43, 0, 46, 255)  # > 48 in: dark violet

    ice = colormaps.apply(np.array([[0.005, 0.05, 3.0]], np.float32), "ice_accum")[0]
    assert tuple(ice[0]) == (0, 0, 0, 0)  # < 0.01 in
    assert tuple(ice[1]) == (244, 234, 59, 255)  # amber
    assert tuple(ice[2]) == (37, 4, 91, 255)  # > 2 in: deep violet


def test_probabilities_ramp_from_transparent_to_saturated() -> None:
    values = np.array([[0.0, 5.0, 10.0, 50.0, 100.0]], np.float32)
    rgba = colormaps.apply(values, "probabilities")[0]
    assert rgba[0][3] == 0
    assert rgba[1][3] == 0  # under the 10 % threshold
    assert rgba[2][3] == 25  # 10 % -> alpha ~ 0.1 * 255
    assert rgba[3][3] == 127 or rgba[3][3] == 128
    assert tuple(rgba[4]) == (255, 9, 209, 255)  # 100 %: full magenta


def test_temperature_ramp_is_ordered_cold_to_hot() -> None:
    """The AWIPS NDFD ramp, cold to hot.

    The NDFD scale is *not* a simple blue->red gradient: the extreme cold end
    is pink/magenta, it deepens to violet and blue through the freezing range,
    then runs cyan -> green -> yellow -> orange -> deep red as it warms.
    """
    values = np.array([[-40.0, 0.0, 32.0, 70.0, 120.0]], np.float32)
    rgba = colormaps.apply(values, "temperature")[0]
    cold, freezing, mild, warm, hot = (tuple(int(v) for v in row[:3]) for row in rgba)

    assert cold[0] > 200 and cold[2] > 200 and cold[1] < 180, "cold end is magenta"
    assert freezing[2] > freezing[0] and freezing[2] > 150, "0 degF is deep violet"
    assert mild[1] > mild[0] and mild[1] > mild[2], "32 degF is teal/green"
    assert warm[0] == 255 and warm[1] > 200 and warm[2] < 60, "70 degF is yellow"
    assert hot[0] > 140 and hot[1] < 20 and hot[2] < 60, "hot end is deep red"

    # Out-of-range sentinels come straight from the AWIPS table.
    sentinels = colormaps.apply(np.array([[-99.0, 199.0]], np.float32), "temperature")[0]
    assert tuple(int(v) for v in sentinels[0]) == (255, 255, 255, 255)
    assert tuple(int(v) for v in sentinels[1]) == (147, 6, 43, 255)


def test_nan_is_transparent_not_black() -> None:
    values = np.array([[np.nan, 32.0]], np.float32)
    rgba = colormaps.apply(values, "temperature")[0]
    assert tuple(rgba[0]) == (0, 0, 0, 0)
    assert rgba[1][3] == 255


def test_metric_and_imperial_produce_identical_pixels() -> None:
    """Units change the legend, never the rendered tile."""
    imperial = np.array([[32.0, 68.0, 104.0]], np.float32)  # degF
    metric = colormaps.convert_units(imperial, "temperature", "imperial", "metric")
    np.testing.assert_allclose(metric, [[0.0, 20.0, 40.0]], atol=1e-4)

    # apply() always receives canonical (imperial) values; `units` converts the
    # data and the breakpoints together, so the pixels cannot differ.
    left = colormaps.apply(imperial, "temperature", units="imperial")
    right = colormaps.apply(imperial, "temperature", units="metric")
    assert np.array_equal(left, right)

    precip = np.array([[0.5, 2.0]], np.float32)  # inches
    mm = colormaps.convert_units(precip, "length", "imperial", "metric")
    np.testing.assert_allclose(mm, [[12.7, 50.8]], atol=1e-3)
    assert np.array_equal(
        colormaps.apply(precip, "precip_accum", units="imperial"),
        colormaps.apply(precip, "precip_accum", units="metric"),
    )

    # The legend, however, must follow the requested system.
    assert colormaps.legend("precip_accum", units="metric")["units"] == "mm"


def test_opacity_scales_alpha_only() -> None:
    values = np.array([[32.0, 68.0]], np.float32)
    full = colormaps.apply(values, "temperature", opacity=1.0)
    half = colormaps.apply(values, "temperature", opacity=0.5)
    np.testing.assert_array_equal(full[:, :, :3], half[:, :, :3])
    np.testing.assert_array_equal(half[:, :, 3], (full[:, :, 3] // 2).astype(np.uint8))


def test_apply_returns_uint8_rgba() -> None:
    out = colormaps.apply(np.zeros((4, 5), np.float32), "temperature")
    assert out.shape == (4, 5, 4)
    assert out.dtype == np.uint8


def test_legend_reports_requested_units() -> None:
    imperial = colormaps.legend("precip_accum", units="imperial")
    metric = colormaps.legend("precip_accum", units="metric")
    assert imperial["units"] == "in"
    assert metric["units"] == "mm"
    assert imperial["stops"][0]["value"] == pytest.approx(0.01)
    assert metric["stops"][0]["value"] == pytest.approx(0.254, abs=1e-3)


def test_unknown_colormap_raises() -> None:
    with pytest.raises(KeyError, match="unknown colormap"):
        colormaps.get("not_a_ramp")


# ── Cache tiers ───────────────────────────────────────────────────────────────


def test_tile_key_format() -> None:
    key = tile_key("co", "2026091900", "tmp", 24, 5, 7, 12, "imperial")
    assert key == "co:2026091900:tmp:f024:5:7:12:imperial"
    varied = tile_key(
        "co",
        "2026091900",
        "tmp",
        24,
        5,
        7,
        12,
        "metric",
        opacity=0.5,
        smooth=False,
        colormap="temperature",
        tilesize=512,
    )
    assert varied != key
    assert "metric" in varied and "o0.50" in varied and "raw" in varied and "s512" in varied


def test_grid_cache_evicts_by_byte_budget() -> None:
    cache = GridCache(max_bytes=1000, max_entries=10)
    small = np.zeros((10, 10), np.float32)  # 400 B each
    for index in range(4):
        cache.put(f"k{index}", small)
    stats = cache.stats()
    assert stats.entries == 2, "a 1000 B budget only holds two 400 B grids"
    assert stats.bytes <= 1000
    assert stats.evictions == 2
    assert cache.get("k0") is None  # least-recently-stored went first
    assert cache.get("k3") is not None


def test_grid_cache_oversized_value_is_not_cached() -> None:
    cache = GridCache(max_bytes=100)
    cache.put("huge", np.zeros((100, 100), np.float32))
    assert cache.get("huge") is None
    assert cache.stats().entries == 0


def test_grid_cache_get_or_load_calls_loader_once() -> None:
    cache = GridCache(max_bytes=10_000)
    calls = []

    def loader():
        calls.append(1)
        return np.zeros((4, 4), np.float32)

    first = cache.get_or_load("k", loader)
    second = cache.get_or_load("k", loader)
    assert len(calls) == 1
    assert first is second


def test_tile_cache_roundtrip_and_stats() -> None:
    cache = TileCache(backend_name="memory", ttl_seconds=60)
    payload = b"RIFF\x00\x00\x00\x00WEBP"
    assert cache.get("a:b") is None
    cache.set("a:b", payload)
    assert cache.get("a:b") == payload
    stats = cache.stats().as_dict()
    assert stats["hits"] == 1 and stats["misses"] == 1


def test_tile_cache_survives_backend_failure() -> None:
    cache = TileCache(backend_name="memory")

    def explode(_key):
        raise OSError("disk on fire")

    cache._backend.get = explode  # noqa: SLF001 - deliberate fault injection
    assert cache.get("x") is None
    assert cache.stats().as_dict()["errors"] == 1


def test_cache_control_headers_distinguish_latest_from_historical() -> None:
    latest = cache_control_headers(is_latest=True)
    historical = cache_control_headers(is_latest=False)
    assert latest["Cache-Control"] == "public, max-age=300"
    assert historical["Cache-Control"] == "public, max-age=86400, immutable"


def test_cycle_is_latest_uses_tolerance_window() -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    assert cycle_is_latest("2026091912", now=now)
    assert cycle_is_latest("2026091911", now=now, tolerance_hours=3)
    assert not cycle_is_latest("2026091900", now=now, tolerance_hours=3)
    assert not cycle_is_latest(datetime(2026, 9, 18, 0, tzinfo=timezone.utc), now=now)


# ── Renderer internals ────────────────────────────────────────────────────────


TILE = (7, 28, 48)


def _fixture_grid(values_fn, *, pad_deg: float = 3.0, resolution: float = 0.05) -> Grid:
    """Build a lat/lon grid that fully contains the TILE fixture."""
    west, south, east, north = tile_bounds_4326(*TILE)
    west, east = west - pad_deg, east + pad_deg
    south, north = south - pad_deg, north + pad_deg
    width = int(np.ceil((east - west) / resolution))
    height = int(np.ceil((north - south) / resolution))
    return Grid(
        values=values_fn(height, width).astype(np.float32),
        transform=from_origin(west, north, resolution, resolution),
        crs=CRS.from_epsg(4326),
    )


def test_warp_to_tile_reprojects_and_preserves_values() -> None:
    """A constant field warps to the same constant, covering the whole tile."""
    grid = _fixture_grid(lambda h, w: np.full((h, w), 42.0))
    bounds = tile_bounds_3857(*TILE)
    warped = TileRenderer.warp_to_tile(grid, bounds, 256, smooth=False)
    covered = np.isfinite(warped)
    assert covered.all(), "a fully-covered tile must have no holes"
    assert float(np.abs(warped - 42.0).max()) < 1e-3


def test_bilinear_resampling_removes_block_artifacts() -> None:
    """A sharp step in the source must come out graduated, not stepped."""

    def stepped(height: int, width: int) -> np.ndarray:
        field = np.zeros((height, width), np.float32)
        field[:, width // 2 :] = 100.0
        return field

    grid = _fixture_grid(stepped)
    bounds = tile_bounds_3857(*TILE)

    bilinear = TileRenderer.warp_to_tile(
        grid, bounds, 256, smooth=False, resampling=Resampling.bilinear
    )
    nearest = TileRenderer.warp_to_tile(
        grid, bounds, 256, smooth=False, resampling=Resampling.nearest
    )
    assert np.isfinite(bilinear).all() and np.isfinite(nearest).all()

    bilinear_levels = np.unique(np.round(bilinear, 1))
    nearest_levels = np.unique(np.round(nearest, 1))
    assert len(bilinear_levels) > 5, "bilinear must interpolate across the step"
    assert set(np.round(nearest_levels).astype(int)) <= {
        0,
        100,
    }, "nearest-neighbour must stay stepped"
    # Both must span the same range; only the transition differs.
    assert bilinear_levels.min() == pytest.approx(0.0, abs=1e-3)
    assert bilinear_levels.max() == pytest.approx(100.0, abs=1e-3)


def test_smooth_parameter_suppresses_high_frequency_noise() -> None:
    """smooth=True must damp pixel-to-pixel noise without moving the mean."""

    def noisy(height: int, width: int) -> np.ndarray:
        rng = np.random.default_rng(1234)
        base = np.linspace(20.0, 80.0, width, dtype=np.float32)[None, :].repeat(height, 0)
        return base + rng.normal(0.0, 6.0, (height, width)).astype(np.float32)

    grid = _fixture_grid(noisy)
    bounds = tile_bounds_3857(*TILE)
    raw = TileRenderer.warp_to_tile(grid, bounds, 256, smooth=False)
    smoothed = TileRenderer.warp_to_tile(grid, bounds, 256, smooth=True)

    def total_variation(a: np.ndarray) -> float:
        return float(np.abs(np.diff(a, axis=1)).sum() + np.abs(np.diff(a, axis=0)).sum())

    # The bilinear warp already damps most of the noise; the extra data-space
    # pass must still measurably reduce what is left.
    assert total_variation(smoothed) < total_variation(raw) * 0.95
    assert abs(float(smoothed.mean()) - float(raw.mean())) < 1.0
    assert float(smoothed.std()) < float(raw.std())


def test_blur_does_not_wrap_across_tile_edges() -> None:
    """np.roll would bleed the right edge into the left; edge replication must not."""
    from app.tiles.renderer import _nan_aware_blur

    field = np.full((1, 8), 10.0, dtype=np.float32)
    field[0, -1] = 100.0
    blurred = _nan_aware_blur(field, radius=1)
    assert blurred[0, 0] == pytest.approx(10.0), "left edge must not see the right edge"
    assert blurred[0, -1] > 50.0


def test_blur_ignores_nodata_instead_of_smearing_it() -> None:
    from app.tiles.renderer import _nan_aware_blur

    field = np.array([[10.0, 10.0, np.nan, 10.0]], dtype=np.float32)
    blurred = _nan_aware_blur(field, radius=1)
    assert np.isnan(blurred[0, 2])
    np.testing.assert_allclose(blurred[0, [0, 1, 3]], [10.0, 10.0, 10.0])


def test_empty_tile_payload_is_a_transparent_webp() -> None:
    blob = empty_tile_payload()
    assert blob[:4] == b"RIFF"
    image = Image.open(io.BytesIO(blob))
    assert image.size == (1, 1)
    assert np.asarray(image.convert("RGBA"))[0, 0, 3] == 0


def test_domain_grid_conus_is_lambert_conformal() -> None:
    grid = domain_grid("co")
    assert grid.crs.to_epsg() == 6372
    assert "+proj=lcc" in grid.crs.to_proj4()
    assert grid.resolution_m == pytest.approx(2500.0)
    assert grid.width > 2000 and grid.height > 1000


# ── Element binding ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("element", "expected"),
    [
        ("tmp", "temperature"),
        ("max", "temperature"),
        ("qpf_24h", "precip_accum"),
        ("snow_24h", "snow_accum"),
        ("ice_24h", "ice_accum"),
        ("wind", "wind_speed"),
        ("gust", "wind_gust"),
        ("sbcape", "cape"),
        ("refc", "reflectivity"),
        ("pop12", "probabilities"),
        ("qpf_gt_025", "probabilities"),
    ],
)
def test_render_plan_binding(element: str, expected: str) -> None:
    assert render_plan(element).colormap == expected


def test_unrenderable_element_raises() -> None:
    with pytest.raises(ElementNotRenderable, match="no colour ramp"):
        render_plan("wdir")
    with pytest.raises(ElementNotRenderable, match="not a known NBM element"):
        render_plan("definitely_not_an_element")


def test_every_renderable_element_uses_a_registered_ramp() -> None:
    registered = set(colormaps.names())
    for element, ramp in renderable_elements().items():
        assert ramp in registered, f"{element} -> unknown ramp {ramp}"
    assert REQUIRED_RAMPS.issubset(set(renderable_elements().values()))


# ── Endpoint ──────────────────────────────────────────────────────────────────


def test_tile_returns_valid_webp(client: TestClient) -> None:
    z, x, y = INSIDE
    response = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.content[:4] == b"RIFF"

    image = Image.open(io.BytesIO(response.content))
    assert image.size == (256, 256)
    assert image.format == "WEBP"


def test_empty_tile_is_negative_cached(client: TestClient) -> None:
    """A transparent tile must not re-run the warp on every request.

    Sparse fields (24 h QPF, snow, reflectivity) are empty over most of CONUS,
    so without a negative cache entry each of those tiles would cost a full
    reprojection every single time it is asked for.
    """
    # Discovery runs on a throwaway cycle: probing a URL warms its cache entry,
    # which would make the "cold" request below look warm.
    sparse = ["qpf_24h", "snow_24h", "ice_24h", "refc", "sbcape"]
    element = next(
        candidate
        for candidate in sparse
        if client.get(_url("co", "2020031500", candidate, "f024", *INSIDE)).status_code == 204
    )

    empty_url = _url("co", "2021070400", element, "f024", *INSIDE)
    cold = client.get(empty_url)
    assert cold.status_code == 204
    assert cold.headers["X-Tile-Empty"] == "1"
    assert cold.headers["X-Tile-Cache"] == "miss"

    warm = client.get(empty_url)
    assert warm.status_code == 204, "the negative cache must not change the status"
    assert warm.headers["X-Tile-Empty"] == "1"
    assert warm.headers["X-Tile-Cache"] == "hit", "empty tiles must be cached too"


def test_negative_cache_marker_round_trips() -> None:
    from app.tiles.cache import EMPTY_TILE_MARKER, TileCache, is_empty_marker, tile_key

    assert not EMPTY_TILE_MARKER.startswith(b"RIFF"), "must never collide with a WebP"
    assert is_empty_marker(EMPTY_TILE_MARKER)
    assert not is_empty_marker(b"RIFF....WEBP")
    assert not is_empty_marker(None)

    cache = TileCache(backend_name="memory")
    key = tile_key(
        "co",
        "2020031500",
        "qpf_24h",
        24,
        5,
        7,
        12,
        "imperial",
        opacity=1.0,
        smooth=True,
        colormap="precip_accum",
        tilesize=256,
    )
    cache.set(key, EMPTY_TILE_MARKER)
    assert cache.get(key) == EMPTY_TILE_MARKER

    # A genuinely empty buffer is still refused: only the marker is storable.
    other = key + ":other"
    cache.set(other, b"")
    assert cache.get(other) is None


def test_latest_cycle_tiles_expire_sooner(client: TestClient) -> None:
    """The live cycle must not be held on disk for the historical TTL."""
    from datetime import datetime, timedelta, timezone

    from app.config import settings
    from app.tiles.cache import cycle_is_latest

    fresh = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%d%H")
    assert cycle_is_latest(fresh)
    assert not cycle_is_latest(old)
    assert settings.tile_cache_ttl_latest_seconds < settings.tile_cache_ttl_seconds

    latest = client.get(_url("co", fresh, "tmp", "f024", *INSIDE))
    stale = client.get(_url("co", old, "tmp", "f024", *INSIDE))
    assert (
        latest.headers["cache-control"] == f"public, max-age={settings.tile_cache_max_age_latest}"
    )
    assert stale.headers["cache-control"] == (
        f"public, max-age={settings.tile_cache_max_age_historical}, immutable"
    )


def test_warm_tile_is_served_from_cache(client: TestClient) -> None:
    # A cycle nothing else in this module touches, so the first request is cold.
    url = _url("co", "2019010100", "tmp", "f024", *INSIDE)
    cold = client.get(url)
    assert cold.headers["X-Tile-Cache"] == "miss"

    warm = client.get(url)
    assert warm.status_code == 200
    assert warm.headers["X-Tile-Cache"] == "hit"
    assert warm.content == cold.content


def test_layer1_grid_is_shared_across_tiles(client: TestClient) -> None:
    """Different tiles of the same variable must not re-decode the GRIB."""
    from app.tiles.cache import get_grid_cache

    before = get_grid_cache().stats().as_dict()["entries"]
    for x in (6, 7, 8):
        response = client.get(_url("co", "2026091900", "max", "f012", 5, x, 12))
        assert response.status_code in (200, 204)
    after = get_grid_cache().stats().as_dict()["entries"]
    assert after - before <= 1, "one decoded grid should back all three tiles"


def test_out_of_bounds_tile_short_circuits(client: TestClient) -> None:
    """A tile outside the domain must not reach the data source at all."""

    calls: list[GridRequest] = []

    class Recording:
        name = "recording"

        async def message(self, request: GridRequest) -> bytes:
            calls.append(request)
            raise AssertionError("the data source must not be contacted")

        async def close(self) -> None:
            return None

    set_data_source(Recording())
    try:
        z, x, y = OUTSIDE
        response = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y))
        assert response.status_code == 204
        assert response.headers["X-Tile-Empty"] == "1"
        assert calls == []

        with_body = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y, empty="image"))
        assert with_body.status_code == 200
        assert Image.open(io.BytesIO(with_body.content)).size == (1, 1)
        assert calls == []
    finally:
        set_data_source(SyntheticGribSource())


def test_historical_cycle_is_immutable_latest_is_not(client: TestClient) -> None:
    z, x, y = INSIDE
    stale = "2020010100"
    historical = client.get(_url("co", stale, "tmp", "f024", z, x, y))
    assert historical.headers["Cache-Control"] == "public, max-age=86400, immutable"

    fresh = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y%m%d%H")
    latest = client.get(_url("co", fresh, "tmp", "f024", z, x, y))
    assert latest.headers["Cache-Control"] == "public, max-age=300"
    assert "immutable" not in latest.headers["Cache-Control"]


def test_query_parameters_change_the_render(client: TestClient) -> None:
    z, x, y = INSIDE
    base = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y))
    half = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y, opacity=0.5))
    raw = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y, smooth="false"))
    metric = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y, units="metric"))
    big = client.get(_url("co", "2026091900", "tmp", "f024", z, x, y, tilesize=512))

    assert base.status_code == half.status_code == raw.status_code == 200
    assert half.content != base.content, "opacity must be part of the cache key"
    assert raw.content != base.content, "smooth must be part of the cache key"
    assert big.status_code == 200
    assert Image.open(io.BytesIO(big.content)).size == (512, 512)
    # Units change the legend, not the pixels — but they are still a distinct
    # cache entry because the client asked for a different representation.
    assert metric.status_code == 200


def test_transparency_mask_reaches_the_encoded_tile(client: TestClient) -> None:
    """QPF below 0.01 in must come back fully transparent in the WebP."""
    z, x, y = INSIDE
    response = client.get(_url("co", "2026091900", "qpf_24h", "f024", z, x, y))
    if response.status_code == 204:
        pytest.skip("this tile is entirely below the QPF threshold")
    alpha = np.asarray(Image.open(io.BytesIO(response.content)).convert("RGBA"))[:, :, 3]
    assert (alpha == 0).any(), "below-threshold pixels must be transparent"
    assert (alpha == 255).any(), "wet pixels must be opaque"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        (("zz", "2026091900", "tmp", "f024", 5, 7, 12), 404),
        (("co", "2026091900", "nope", "f024", 5, 7, 12), 404),
        (("co", "2026091900", "wdir", "f024", 5, 7, 12), 422),
        (("co", "2026091999", "tmp", "f024", 5, 7, 12), 422),
        (("co", "2026091900", "tmp", "f999", 5, 7, 12), 422),
        (("co", "2026091900", "tmp", "f024", 40, 7, 12), 422),
        (("co", "2026091900", "tmp", "f024", 5, 99, 12), 422),
        (("co", "2026091900", "tmp", "f024", 5, 7, 99), 422),
    ],
)
def test_validation_errors(client: TestClient, path: tuple, status: int) -> None:
    assert client.get(_url(*path)).status_code == status


def test_capabilities_describes_the_service(client: TestClient) -> None:
    response = client.get(f"{settings.api_prefix}/tiles/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "webp"
    assert body["tileSizes"] == [256, 512]
    assert "{domain}" in body["urlTemplate"] and "{y}.webp" in body["urlTemplate"]
    assert REQUIRED_RAMPS.issubset({c["name"] for c in body["colormaps"]})
    assert "tmp" in body["elements"]
    assert body["cache"]["historical"].endswith("immutable")


def test_colormap_legend_endpoint(client: TestClient) -> None:
    ok = client.get(f"{settings.api_prefix}/tiles/colormaps/reflectivity?units=imperial")
    assert ok.status_code == 200
    assert ok.json()["units"] == "dBZ"
    assert ok.json()["stops"][0]["hex"] == "#04e9e7"

    missing = client.get(f"{settings.api_prefix}/tiles/colormaps/nope")
    assert missing.status_code == 404


def test_cache_stats_endpoint(client: TestClient) -> None:
    body = client.get(f"{settings.api_prefix}/tiles/cache").json()
    assert set(body) == {"grids", "tiles", "tileBackend"}
    assert body["tileBackend"] in ("disk", "redis", "memory")
    assert body["grids"]["bytes"] >= 0


def test_tile_bounds_helpers() -> None:
    west, south, east, north = tile_bounds_4326(0, 0, 0)
    assert (west, south, east, north) == pytest.approx((-180.0, -85.0511, 180.0, 85.0511), abs=1e-3)
    left, bottom, right, top = tile_bounds_3857(0, 0, 0)
    assert left == pytest.approx(-20037508.34, abs=1.0)
    assert top == pytest.approx(20037508.34, abs=1.0)


def test_renderer_singleton() -> None:
    assert get_tile_renderer() is get_tile_renderer()
