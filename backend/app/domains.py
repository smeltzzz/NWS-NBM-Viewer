"""
Static catalog of NBM domains (publication regions).

Each domain maps to a two-letter region code, the resolution of its native
Lambert Conformal grid, the cycle cadence, and a compact bounding box. This
drives validation and the initial UI state; per-cycle forecast-layout detail
comes from the on-demand GRIB inventory service.
"""

from __future__ import annotations

from typing import TypedDict


class Domain(TypedDict):
    code: str
    name: str
    resolution_km: float
    cycles_per_day: int
    cycles: list[int]
    notes: str
    bbox: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat


# Core products publish every hour (00-23z) for CONUS/AK/HI/PR/GU; QMD runs
# four times daily (00/06/12/18z). Oceanic is 6-hourly deterministic.
DOMAINS: dict[str, Domain] = {
    "co": {
        "code": "co",
        "name": "CONUS",
        "resolution_km": 2.5,
        "cycles_per_day": 24,
        "cycles": list(range(0, 24)),
        "notes": "Hourly core + 4x daily QMD; Lambert Conformal, 2.5 km",
        "bbox": (-126.0, 20.0, -66.0, 50.0),
    },
    "ak": {
        "code": "ak",
        "name": "Alaska",
        "resolution_km": 3.0,
        "cycles_per_day": 24,
        "cycles": list(range(0, 24)),
        "notes": "Hourly core + 4x daily QMD; 3 km",
        "bbox": (-180.0, 50.0, -125.0, 72.0),
    },
    "hi": {
        "code": "hi",
        "name": "Hawaii",
        "resolution_km": 2.5,
        "cycles_per_day": 24,
        "cycles": list(range(0,24)),
        "notes": "Hourly core + 4x daily QMD; 2.5 km",
        "bbox": (-161.0, 18.0, -154.0, 23.0),
    },
    "pr": {
        "code": "pr",
        "name": "Puerto Rico",
        "resolution_km": 1.25,
        "cycles_per_day": 24,
        "cycles": list(range(0, 24)),
        "notes": "Hourly core + 4x daily QMD; 1.25 km",
        "bbox": (-69.0, 17.0, -64.0, 19.0),
    },
    "gu": {
        "code": "gu",
        "name": "Guam",
        "resolution_km": 2.5,
        "cycles_per_day": 24,
        "cycles": list(range(0, 24)),
        "notes": "Hourly core; 2.5 km",
        "bbox": (140.0, 10.0, 150.0, 18.0),
    },
    "oc": {
        "code": "oc",
        "name": "Oceanic",
        "resolution_km": 10.0,
        "cycles_per_day": 4,
        "cycles": [0, 6, 12, 18],
        "notes": "PMSLP percentiles & wave elements; 6-hourly",
        "bbox": (-180.0, -80.0, 180.0, 80.0),
    },
}
