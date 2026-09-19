"""Integration tests for the point-probe and meteogram API.

Exercises coordinate conversion, bilinear grid sampling, and the
``/api/v1/probe/point`` + ``/api/v1/probe/meteogram`` endpoints against the
synthetic GRIB source — no NOAA network dependency.

Coverage locations:
  * Denver, CO   — complex terrain CONUS (``co``)
  * Anchorage, AK — Alaska polar stereographic (``ak``)
  * Honolulu, HI — Hawaii Mercator (``hi``)
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from rasterio.crs import CRS

from app.config import settings
from app.probe.elements import DEFAULT_POINT_ELEMENTS, probe_element_plan
from app.probe.sampler import GridSampler, clear_sampler_cache, get_grid_sampler
from app.probe.service import (
    meteogram_forecast_hours,
    parse_cycle,
    reset_probe_service,
    valid_time_utc,
)
from app.tiles.grib import Grid
from app.tiles.source import SyntheticGribSource, domain_grid, set_data_source


# ── Fixtures ──────────────────────────────────────────────────────────────────

# Representative click locations across domains.
DENVER = {"lat": 39.7392, "lon": -104.9903, "domain": "co", "name": "Denver"}
ANCHORAGE = {"lat": 61.2181, "lon": -149.9003, "domain": "ak", "name": "Anchorage"}
HONOLULU = {"lat": 21.3069, "lon": -157.8583, "domain": "hi", "name": "Honolulu"}

CYCLE = "2026091900"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient with synthetic GRIB source and isolated caches."""
    from app.tiles import cache as cache_module

    saved = (
        settings.tile_cache_dir,
        settings.cache_size_limit_gb,
        settings.tile_data_source,
        settings.environment,
        settings.tile_synthetic_downsample,
    )
    settings.tile_cache_dir = tmp_path_factory.mktemp("probe-cache")
    settings.cache_size_limit_gb = 0.1
    settings.tile_data_source = "synthetic"
    settings.environment = "test"
    # Keep synthetic grids coarse so meteogram encode stays snappy in CI.
    settings.tile_synthetic_downsample = 8

    set_data_source(SyntheticGribSource(downsample=8))
    cache_module.reset_caches()
    clear_sampler_cache()
    reset_probe_service()

    from main import app

    with TestClient(app) as test_client:
        yield test_client

    set_data_source(None)
    cache_module.reset_caches()
    clear_sampler_cache()
    reset_probe_service()
    (
        settings.tile_cache_dir,
        settings.cache_size_limit_gb,
        settings.tile_data_source,
        settings.environment,
        settings.tile_synthetic_downsample,
    ) = saved


def _point_url(**params) -> str:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{settings.api_prefix}/probe/point?{qs}"


def _meteogram_url(**params) -> str:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{settings.api_prefix}/probe/meteogram?{qs}"


# ── Forecast-hour cadence ─────────────────────────────────────────────────────


def test_meteogram_forecast_hour_cadence() -> None:
    hours = meteogram_forecast_hours(1, 264)
    assert hours[0] == 1
    assert hours[-1] == 264
    # Hourly through 36.
    assert list(range(1, 37)) == [h for h in hours if h <= 36]
    # 3-hourly 39..72.
    three = [h for h in hours if 39 <= h <= 72]
    assert three == list(range(39, 73, 3))
    # 6-hourly 78..264.
    six = [h for h in hours if h >= 78]
    assert six == list(range(78, 265, 6))
    # Total count: 36 + 12 + 32 = 80.
    assert len(hours) == 80


def test_meteogram_forecast_hours_window_clip() -> None:
    assert meteogram_forecast_hours(30, 45) == [30, 31, 32, 33, 34, 35, 36, 39, 42, 45]
    assert meteogram_forecast_hours(200, 220) == [204, 210, 216]


def test_parse_cycle_and_valid_time() -> None:
    assert parse_cycle("2026-09-19T12") == "2026091912"
    assert parse_cycle("20260919") == "2026091900"
    vt = valid_time_utc("2026091900", 24)
    assert vt.isoformat().startswith("2026-09-20T00:00:00")


# ── Sampler unit tests ────────────────────────────────────────────────────────


def test_sampler_projects_denver_onto_conus_lcc() -> None:
    sampler = GridSampler()
    proj = sampler.projection("co")
    assert proj.crs.to_epsg() == 6372
    assert sampler.in_domain_bbox("co", DENVER["lat"], DENVER["lon"])
    assert not sampler.in_domain_bbox("co", 0.0, 0.0)


def test_bilinear_sample_matches_analytic_field() -> None:
    """A known linear field must bilinear-interpolate exactly."""
    width, height = 10, 8
    # value = col + 2*row  (in cell-index space, cell centres)
    cols = np.arange(width, dtype=np.float32)
    rows = np.arange(height, dtype=np.float32)
    xx, yy = np.meshgrid(cols, rows)
    values = (xx + 2.0 * yy).astype(np.float32)

    transform = from_origin(0.0, float(height), 1.0, 1.0)  # pixel size 1
    grid = Grid(
        values=values,
        transform=transform,
        crs=CRS.from_epsg(4326),
    )
    sampler = GridSampler()

    # Point at fractional (row=2.25, col=3.5) in index space.
    # With from_origin(0, height, 1, 1): x = col, y = height - row
    # So lon=3.5, lat = 8 - 2.25 = 5.75
    lat = float(height) - 2.25
    lon = 3.5
    index = sampler.locate("oc", lat, lon, grid=grid)
    assert index.row == pytest.approx(2.25, abs=1e-6)
    assert index.col == pytest.approx(3.5, abs=1e-6)

    result = sampler.sample(grid, index, method="bilinear")
    expected = 3.5 + 2.0 * 2.25  # col + 2*row
    assert result.value == pytest.approx(expected, abs=1e-4)
    assert result.method == "bilinear"
    assert not result.nodata


def test_nearest_sample_snaps_to_cell() -> None:
    values = np.arange(16, dtype=np.float32).reshape(4, 4)
    transform = from_origin(0.0, 4.0, 1.0, 1.0)
    grid = Grid(values=values, transform=transform, crs=CRS.from_epsg(4326))
    sampler = GridSampler()
    # Near centre of cell (1, 2) → value 1*4+2 = 6
    index = sampler.locate("oc", lat=4.0 - 1.1, lon=2.1, grid=grid)
    result = sampler.sample(grid, index, method="nearest")
    assert result.value == pytest.approx(6.0)


def test_index_cache_hits_on_repeat() -> None:
    sampler = GridSampler()
    grid = domain_grid("co", downsample=8)
    # Build a dummy Grid with the domain geometry.
    g = Grid(
        values=np.zeros(grid.shape, np.float32),
        transform=grid.transform,
        crs=grid.crs,
    )
    a = sampler.locate("co", DENVER["lat"], DENVER["lon"], grid=g)
    b = sampler.locate("co", DENVER["lat"], DENVER["lon"], grid=g)
    assert a == b
    assert a.inside


@pytest.mark.parametrize(
    "site",
    [DENVER, ANCHORAGE, HONOLULU],
    ids=lambda s: s["name"],
)
def test_site_projects_inside_domain_grid(site: dict) -> None:
    sampler = get_grid_sampler()
    assert sampler.in_domain_bbox(site["domain"], site["lat"], site["lon"])
    grid_meta = domain_grid(site["domain"], downsample=4)
    g = Grid(
        values=np.zeros(grid_meta.shape, np.float32),
        transform=grid_meta.transform,
        crs=grid_meta.crs,
    )
    index = sampler.locate(site["domain"], site["lat"], site["lon"], grid=g)
    assert index.inside, f"{site['name']} should land on the {site['domain']} grid"
    assert 0 <= index.row_i < grid_meta.height
    assert 0 <= index.col_i < grid_meta.width


# ── Element plans ─────────────────────────────────────────────────────────────


def test_probe_element_plan_covers_wind_direction() -> None:
    """wdir has no tile colormap but must still be probeable."""
    plan = probe_element_plan("wdir")
    assert plan.unit_kind == "direction"
    assert plan.grib_unit == "degree"
    assert plan.product == "core"


def test_probe_element_plan_temperature_units() -> None:
    plan = probe_element_plan("tmp")
    assert plan.unit_kind == "temperature"
    assert plan.grib_unit == "K"
    assert plan.imperial_unit in ("°F", "F", "degF")


# ── Point endpoint ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "site",
    [DENVER, ANCHORAGE, HONOLULU],
    ids=lambda s: s["name"],
)
def test_point_probe_across_domains(client: TestClient, site: dict) -> None:
    response = client.get(
        _point_url(
            lat=site["lat"],
            lon=site["lon"],
            domain=site["domain"],
            cycle=CYCLE,
            fhour=24,
            elements="tmp,dpt,wind,wdir,gust,qpf_1h,sky",
            units="imperial",
        )
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["domain"] == site["domain"]
    assert body["cycle"] == CYCLE
    assert body["forecast_hour"] == 24
    assert body["valid_time_utc"].endswith("Z")
    assert body["method"] == "bilinear"
    assert body["grid_index"]["inside"] is True

    values = body["values"]
    for code in ("tmp", "dpt", "wind", "wdir", "gust", "qpf_1h", "sky"):
        assert code in values, f"missing {code}"
        entry = values[code]
        assert entry["missing"] is False
        assert entry["value"] is not None
        assert math.isfinite(entry["value"])

    # Temperature in a plausible °F range for synthetic fields.
    assert -20.0 <= values["tmp"]["value"] <= 120.0
    # Wind direction wrapped to [0, 360).
    assert 0.0 <= values["wdir"]["value"] < 360.0
    # Sky cover is a percent.
    assert 0.0 <= values["sky"]["value"] <= 100.0

    summary = body["summary"]
    assert summary["temperature"] is not None
    assert "°" in summary["temperature"] or "F" in summary["temperature"]
    assert summary["wind"] is not None


def test_point_probe_default_elements(client: TestClient) -> None:
    response = client.get(
        _point_url(lat=DENVER["lat"], lon=DENVER["lon"], domain="co", cycle=CYCLE, fhour=12)
    )
    assert response.status_code == 200
    body = response.json()
    for code in DEFAULT_POINT_ELEMENTS:
        assert code in body["values"]


def test_point_probe_metric_units(client: TestClient) -> None:
    imperial = client.get(
        _point_url(
            lat=DENVER["lat"], lon=DENVER["lon"], domain="co",
            cycle=CYCLE, fhour=24, elements="tmp,wind", units="imperial",
        )
    ).json()
    metric = client.get(
        _point_url(
            lat=DENVER["lat"], lon=DENVER["lon"], domain="co",
            cycle=CYCLE, fhour=24, elements="tmp,wind", units="metric",
        )
    ).json()

    t_f = imperial["values"]["tmp"]["value"]
    t_c = metric["values"]["tmp"]["value"]
    assert t_c == pytest.approx((t_f - 32.0) * 5.0 / 9.0, abs=0.15)

    # Wind: kt → m/s
    w_kt = imperial["values"]["wind"]["value"]
    w_ms = metric["values"]["wind"]["value"]
    assert w_ms == pytest.approx(w_kt * 0.514444, abs=0.05)


def test_point_probe_out_of_domain_rejected(client: TestClient) -> None:
    # Equator / prime meridian is far outside CONUS.
    response = client.get(
        _point_url(lat=0.0, lon=0.0, domain="co", cycle=CYCLE, fhour=24)
    )
    assert response.status_code == 422
    assert "outside" in response.json()["detail"].lower()


def test_point_probe_unknown_domain(client: TestClient) -> None:
    response = client.get(
        _point_url(lat=40.0, lon=-105.0, domain="zz", cycle=CYCLE, fhour=24)
    )
    assert response.status_code == 404


def test_point_probe_unknown_element(client: TestClient) -> None:
    response = client.get(
        _point_url(
            lat=DENVER["lat"], lon=DENVER["lon"], domain="co",
            cycle=CYCLE, fhour=24, elements="not_a_real_element",
        )
    )
    assert response.status_code == 404


def test_point_probe_bad_cycle(client: TestClient) -> None:
    response = client.get(
        _point_url(
            lat=DENVER["lat"], lon=DENVER["lon"], domain="co",
            cycle="not-a-cycle", fhour=24,
        )
    )
    assert response.status_code == 422


def test_point_probe_all_elements(client: TestClient) -> None:
    response = client.get(
        _point_url(
            lat=DENVER["lat"], lon=DENVER["lon"], domain="co",
            cycle=CYCLE, fhour=6, elements="all",
        )
    )
    assert response.status_code == 200
    body = response.json()
    # Full catalog is large; just confirm we got more than the default set.
    assert len(body["values"]) > len(DEFAULT_POINT_ELEMENTS)


# ── Meteogram endpoint ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "site",
    [DENVER, ANCHORAGE, HONOLULU],
    ids=lambda s: s["name"],
)
def test_meteogram_across_domains(client: TestClient, site: dict) -> None:
    """Short-window meteogram for each domain (keeps CI fast)."""
    response = client.get(
        _meteogram_url(
            lat=site["lat"],
            lon=site["lon"],
            domain=site["domain"],
            cycle=CYCLE,
            start_fhour=1,
            end_fhour=12,
            units="imperial",
        )
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["domain"] == site["domain"]
    assert body["point_count"] == 12
    assert body["forecast_hours"] == list(range(1, 13))
    assert body["grid_index"]["inside"] is True

    series = body["series"]
    assert len(series) == 12
    first = series[0]
    assert first["forecast_hour"] == 1
    assert first["valid_time_utc"].endswith("Z")
    assert first["temperature"] is not None
    assert first["dewpoint"] is not None
    assert first["wind_speed"] is not None
    assert first["wind_direction"] is not None
    assert first["wind_gust"] is not None
    assert first["sky_cover"] is not None
    # QPF percentiles present (may be null if QMD missing, but keys exist).
    assert "qpf_percentiles" in first
    assert set(first["qpf_percentiles"]) == {"p10", "p50", "p90"}
    assert "snow_percentiles" in first
    assert "precip_type" in first

    # Temperature curve should vary (or at least be finite) across the window.
    temps = [p["temperature"] for p in series if p["temperature"] is not None]
    assert len(temps) == 12
    assert all(math.isfinite(t) for t in temps)


def test_meteogram_full_264h_structure(client: TestClient) -> None:
    """Full 264-hour meteogram returns the correct cadence and payload shape."""
    started = time.perf_counter()
    response = client.get(
        _meteogram_url(
            lat=DENVER["lat"],
            lon=DENVER["lon"],
            domain="co",
            cycle=CYCLE,
            start_fhour=1,
            end_fhour=264,
            units="imperial",
        )
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert response.status_code == 200, response.text
    body = response.json()

    expected_hours = meteogram_forecast_hours(1, 264)
    assert body["forecast_hours"] == expected_hours
    assert body["point_count"] == len(expected_hours) == 80
    assert body["start_fhour"] == 1
    assert body["end_fhour"] == 264

    # Cadence checkpoints.
    hours = [p["forecast_hour"] for p in body["series"]]
    assert 1 in hours and 36 in hours
    assert 39 in hours and 72 in hours
    assert 78 in hours and 264 in hours
    assert 37 not in hours  # no hour between 36 and 39
    assert 73 not in hours
    assert 79 not in hours  # 6-hourly from 78

    # Every point carries the required keys.
    required = {
        "forecast_hour",
        "valid_time_utc",
        "temperature",
        "dewpoint",
        "max_temperature",
        "min_temperature",
        "qpf",
        "qpf_percentiles",
        "snow",
        "snow_percentiles",
        "ice",
        "wind_speed",
        "wind_direction",
        "wind_gust",
        "sky_cover",
        "precip_type",
        "precip_type_label",
        "pop",
    }
    for point in body["series"]:
        assert required.issubset(point.keys())

    # Warm-cache second hit should be well under the 600 ms budget.
    warm_started = time.perf_counter()
    warm = client.get(
        _meteogram_url(
            lat=DENVER["lat"],
            lon=DENVER["lon"],
            domain="co",
            cycle=CYCLE,
            start_fhour=1,
            end_fhour=264,
        )
    )
    warm_ms = (time.perf_counter() - warm_started) * 1000.0
    assert warm.status_code == 200
    assert warm_ms < 600.0, f"warm meteogram took {warm_ms:.0f} ms (budget 600 ms)"
    # Record cold timing for diagnostics (no hard fail — synthetic encode is heavy).
    assert "timings_ms" in body
    print(f"meteogram cold={elapsed_ms:.0f}ms warm={warm_ms:.0f}ms")


def test_meteogram_out_of_domain(client: TestClient) -> None:
    response = client.get(
        _meteogram_url(lat=0.0, lon=0.0, domain="co", cycle=CYCLE)
    )
    assert response.status_code == 422


def test_meteogram_bad_window(client: TestClient) -> None:
    response = client.get(
        _meteogram_url(
            lat=DENVER["lat"], lon=DENVER["lon"], domain="co",
            cycle=CYCLE, start_fhour=100, end_fhour=10,
        )
    )
    assert response.status_code == 422


# ── Capabilities ──────────────────────────────────────────────────────────────


def test_probe_capabilities(client: TestClient) -> None:
    response = client.get(f"{settings.api_prefix}/probe/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert "point" in body["endpoints"]
    assert "meteogram" in body["endpoints"]
    assert body["meteogramHourCount"] == 80
    assert "co" in body["domains"] and "ak" in body["domains"] and "hi" in body["domains"]
    assert "tmp" in body["elements"]
    assert "wdir" in body["elements"]
    assert body["methods"] == ["bilinear", "nearest"]
