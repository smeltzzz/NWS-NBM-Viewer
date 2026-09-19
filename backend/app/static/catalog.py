"""
Static NBM element catalog — deterministic variables, statistical flags and
exceedance families — served by ``/api/v1/nbm/*``.

Contents are curated from the NCEP Product Management Branch / MDL VLab tables
for NBM v4.x. Fully dynamic per-cycle discovery (cycles, forecast-hour layouts)
arrives with the GRIB inventory milestone; this module provides the stable,
build-time taxonomy the UI needs on first paint.
"""

from __future__ import annotations

from app.domains import DOMAINS
from app.logging_config import get_logger

log = get_logger(__name__)

# ── Core (deterministic) elements ─────────────────────────────────────────────
# (key, units, description). Statistical "deterministic" columns exposed by the
# core files typically carry a P1/P2 interval-style statistical process.
CORE_ELEMENTS: dict[str, dict[str, str]] = {
    # Temperature
    "tmp": {"units": "degC", "description": "Temperature at 2 m (deterministic)"},
    "mint": {"units": "degC", "description": "Min temperature, 00z-18z (Guam 06z-00z)"},
    "maxt": {"units": "degC", "description": "Max temperature, 12z-06z (Guam 18z-12z)"},
    "tdp": {"units": "degC", "description": "Dew point temperature at 2 m"},
    "appt": {"units": "degC", "description": "Apparent temperature (derived)"},
    # Moisture
    "rh": {"units": "percent", "description": "Relative humidity (derived)"},
    "maxrh": {"units": "percent", "description": "Maximum relative humidity"},
    "minrh": {"units": "percent", "description": "Minimum relative humidity"},
    # Precipitation
    "qpf01": {"units": "mm", "description": "1-hour quantitative precipitation"},
    "qpf06": {"units": "mm", "description": "6-hour quantitative precipitation"},
    "pop01": {"units": "percent", "description": "1-hour probability of precipitation"},
    "pop06": {"units": "percent", "description": "6-hour probability of precipitation"},
    "pop12": {"units": "percent", "description": "12-hour probability of precipitation"},
    "snow": {"units": "mm", "description": "Total snow accumulation (water equivalent)"},
    "snowlev": {"units": "m", "description": "Snow level"},
    "dur": {"units": "h", "description": "Precipitation duration within 12 h period"},
    # Wind
    "wind": {"units": "m/s", "description": "10-m wind speed (u/v magnitude)"},
    "gust": {"units": "m/s", "description": "10-m wind gust speed"},
    "winddir": {"units": "deg", "description": "10-m wind direction"},
    # Sky / clouds
    "sky": {"units": "percent", "description": "Total sky cover"},
    "ceil": {"units": "m", "description": "Ceiling height"},
    "vis": {"units": "m", "description": "Surface visibility"},
    # Pressure
    "mslp": {"units": "Pa", "description": "Mean sea level pressure"},
    "snowc": {"units": "percent", "description": "Snow cover"},
}

# ── Statistical-process / percentile suffixes ─────────────────────────────────
# NBM encodes probabilistic columns as statistical-processing metadata on the
# GRIB message (NCEP local use), e.g. TMP:percentile value 10 / TMP:spread.
STATISTICAL_PROCESSES: dict[str, dict[str, str]] = {
    "mean": {"aggregate": "mean", "description": "Ensemble mean"},
    "spread": {"aggregate": "spread", "description": "Ensemble standard deviation"},
    "percentile": {"aggregate": "percentile", "description": "Member percentile (1-99)"},
    "probability": {"aggregate": "probability", "description": "Exceedance / threshold probability"},
}

# ── Direct percentile families ────────────────────────────────────────────────
# Explicit percentile variables (1-99) published in the core files.
PERCENTILE_FAMILIES: dict[str, dict[str, object]] = {
    "pmaxt": {"units": "degC", "percentiles": list(range(1, 100)),
              "description": "Max temperature percentiles (1-99, ~171 members)"},
    "pmint": {"units": "degC", "percentiles": list(range(1, 100)),
              "description": "Min temperature percentiles (1-99, ~171 members)"},
    "pqpf06": {"units": "mm", "percentiles": list(range(1, 100)),
               "description": "6-hour QPF percentiles (1-99)"},
    "pqpf12": {"units": "mm", "percentiles": list(range(1, 100)),
               "description": "12-hour QPF percentiles (1-99)"},
    "pqpf24": {"units": "mm", "percentiles": list(range(1, 100)),
               "description": "24-hour QPF percentiles (1-99)"},
    "pmslp": {"units": "Pa", "percentiles": [10, 25, 50, 75, 90],
              "description": "MSLP percentiles (oceanic)"},
}

# ── Exceedance / threshold families ───────────────────────────────────────────
# Threshold-probability products. Thresholds are illustrative common breakpoints;
# the authoritative per-cycle set lives in the GRIB inventory (see inventory
# milestone). Values are pydantic-validated at request time.
EXCEEDANCE_FAMILIES: dict[str, dict[str, object]] = {
    "probmaxt": {
        "units": "percent",
        "variable": "maxt",
        "comparison": ">",
        "thresholds": [-17.8, -12.2, 0.0, 32.2, 35.0, 37.8],
        "description": "Probability max temperature exceeds threshold (F-derived)",
    },
    "probmint": {
        "units": "percent",
        "variable": "mint",
        "comparison": "<",
        "thresholds": [-17.8, -12.2, -6.7, 0.0],
        "description": "Probability min temperature below threshold",
    },
    "probqpf06": {
        "units": "percent",
        "variable": "qpf06",
        "comparison": ">",
        "thresholds": [2.54, 6.35, 12.7, 25.4, 50.8],
        "description": "Probability 6-hour QPF exceeds threshold (in-derived)",
    },
    "probqpf12": {
        "units": "percent",
        "variable": "qpf12",
        "comparison": ">",
        "thresholds": [2.54, 6.35, 12.7, 25.4, 50.8],
        "description": "Probability 12-hour QPF exceeds threshold",
    },
    "probqpf24": {
        "units": "percent",
        "variable": "qpf24",
        "comparison": ">",
        "thresholds": [2.54, 6.35, 12.7, 25.4, 50.8, 76.2],
        "description": "Probability 24-hour QPF exceeds threshold",
    },
    "probwind": {
        "units": "percent",
        "variable": "wind",
        "comparison": ">",
        "thresholds": [10.3, 13.4, 17.5, 20.6],
        "description": "Probability 10-m sustained wind exceeds threshold (kt-derived)",
    },
    "probgust": {
        "units": "percent",
        "variable": "gust",
        "comparison": ">",
        "thresholds": [13.4, 17.5, 20.6, 25.7],
        "description": "Probability 10-m gust exceeds threshold",
    },
}

# ── QMD variables ─────────────────────────────────────────────────────────────
QMD_VARIABLES: dict[str, dict[str, str]] = {
    "qmd_pop06": {"units": "percent", "description": "Calibrated 6-hour probability of precipitation"},
    "qmd_pop12": {"units": "percent", "description": "Calibrated 12-hour probability of precipitation"},
    "qmd_qpf06": {"units": "mm", "description": "Calibrated 6-hour QPF"},
    "qmd_qpf12": {"units": "mm", "description": "Calibrated 12-hour QPF"},
    "qmd_qpf24": {"units": "mm", "description": "Calibrated 24-hour QPF"},
}
