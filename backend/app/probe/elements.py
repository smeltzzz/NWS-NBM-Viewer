"""Element metadata for point/meteogram probes (no colormap required).

The tile pipeline refuses elements without a colour ramp (e.g. wind direction).
Point inspection still needs those fields, so this module resolves unit kind,
GRIB native unit, and display labels from the catalog alone.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Literal

from app.core.catalog import ELEMENT_CATALOG, ElementMetadata

UnitKind = Literal[
    "temperature",
    "length",
    "speed",
    "percent",
    "direction",
    "energy",
    "reflectivity",
    "index",
    "distance",
    "duration",
    "pressure",
    "unknown",
]

# Catalog variable → (unit_kind, GRIB native unit).
_VARIABLE_UNITS: dict[str, tuple[UnitKind, str]] = {
    "TMP": ("temperature", "K"),
    "MAXT": ("temperature", "K"),
    "MINT": ("temperature", "K"),
    "DPT": ("temperature", "K"),
    "APTMP": ("temperature", "K"),
    "HEAT": ("temperature", "K"),
    "WCHILL": ("temperature", "K"),
    "APCP": ("length", "mm"),
    "ASNOW": ("length", "mm"),
    "ICEACCR": ("length", "mm"),
    "WIND": ("speed", "m/s"),
    "GUST": ("speed", "m/s"),
    "TRANSPWIND": ("speed", "m/s"),
    "WDIR": ("direction", "degree"),
    "RH": ("percent", "%"),
    "POP12": ("percent", "%"),
    "POP06": ("percent", "%"),
    "POP01": ("percent", "%"),
    "TCDC": ("percent", "%"),
    "CAPE": ("energy", "J/kg"),
    "REFC": ("reflectivity", "dBZ"),
    "PTYPE": ("index", "index"),
    "CIG": ("distance", "m"),
    "LCB": ("distance", "m"),
    "VIS": ("distance", "m"),
    "MHT": ("distance", "m"),
    "RETOP": ("distance", "m"),
    "HTSGW": ("distance", "m"),
    "HINDEX": ("index", "index"),
    "VIL": ("index", "kg/m^2"),
    "DPTSTM": ("percent", "%"),
    "WETTSTM": ("percent", "%"),
}

# Display symbols per unit kind.
_DISPLAY: dict[UnitKind, tuple[str, str]] = {
    "temperature": ("°F", "°C"),
    "length": ("in", "mm"),
    "speed": ("kt", "m/s"),
    "percent": ("%", "%"),
    "direction": ("°", "°"),
    "energy": ("J/kg", "J/kg"),
    "reflectivity": ("dBZ", "dBZ"),
    "index": ("", ""),
    "distance": ("ft", "m"),
    "duration": ("h", "h"),
    "pressure": ("mb", "hPa"),
    "unknown": ("", ""),
}

# Precipitation type categorical codes commonly used by NBM/PTYPE.
PTYPE_LABELS: dict[int, str] = {
    0: "none",
    1: "rain",
    2: "snow",
    3: "ice_pellets",
    4: "freezing_rain",
    5: "mixed",
}


@dataclass(frozen=True)
class ProbeElementPlan:
    """How one catalog element is sampled and displayed at a point."""

    element: str
    name: str
    unit_kind: UnitKind
    grib_unit: str
    product: str  # core | qmd
    imperial_unit: str
    metric_unit: str
    variable: str
    statistical_process: str
    precision: int = 1

    @property
    def label(self) -> str:
        return self.name


def _precision_for(kind: UnitKind) -> int:
    if kind in ("temperature", "speed", "percent", "direction", "energy", "reflectivity"):
        return 1 if kind != "percent" else 0
    if kind == "length":
        return 2
    if kind == "index":
        return 0
    return 1


@functools.lru_cache(maxsize=512)
def probe_element_plan(element: str) -> ProbeElementPlan:
    """Resolve a catalog element for point/meteogram probing.

    Raises
    ------
    KeyError
        If ``element`` is not in the NBM catalog.
    """
    code = element.strip().lower()
    meta: ElementMetadata | None = ELEMENT_CATALOG.get(code)
    if meta is None:
        raise KeyError(f"{element!r} is not a known NBM element")

    variable = (meta.variable or "").upper()
    if meta.statistical_process == "probability":
        kind: UnitKind = "percent"
        grib_unit = "%"
    else:
        match = _VARIABLE_UNITS.get(variable)
        if match is None:
            # Fall back on display-unit heuristics from the catalog.
            imperial = meta.display_units.imperial.lower()
            if "°" in imperial or "f" == imperial or "c" == imperial:
                kind, grib_unit = "temperature", "K"
            elif imperial in ("in", "mm"):
                kind, grib_unit = "length", "mm"
            elif imperial in ("kt", "m/s", "mph"):
                kind, grib_unit = "speed", "m/s"
            elif imperial in ("%", "percent"):
                kind, grib_unit = "percent", "%"
            else:
                kind, grib_unit = "unknown", meta.display_units.metric
        else:
            kind, grib_unit = match

    imperial_u, metric_u = _DISPLAY[kind]
    # Prefer catalog symbols when they are more specific.
    if meta.display_units.imperial:
        imperial_u = meta.display_units.imperial
    if meta.display_units.metric:
        metric_u = meta.display_units.metric

    return ProbeElementPlan(
        element=code,
        name=meta.name,
        unit_kind=kind,
        grib_unit=grib_unit,
        product=meta.master_source,
        imperial_unit=imperial_u,
        metric_unit=metric_u,
        variable=variable,
        statistical_process=meta.statistical_process,
        precision=_precision_for(kind),
    )


def probeable_elements() -> dict[str, str]:
    """``{code: display name}`` for every catalog element the probe can sample."""
    return {code: meta.name for code, meta in sorted(ELEMENT_CATALOG.items())}


# ── Default element sets ──────────────────────────────────────────────────────

#: Instant point-inspection defaults (deterministic core).
DEFAULT_POINT_ELEMENTS: tuple[str, ...] = (
    "tmp",
    "dpt",
    "wind",
    "wdir",
    "gust",
    "qpf_1h",
    "sky",
    "rh",
    "ptype",
)

#: Meteogram series keys → catalog element codes (and optional percentile set).
METEOGRAM_SERIES: dict[str, object] = {
    "temperature": "tmp",
    "dewpoint": "dpt",
    "max_temperature": "max",
    "min_temperature": "min",
    "qpf": "qpf_1h",
    "qpf_p10": "pqpf_10",
    "qpf_p50": "pqpf_50",
    "qpf_p90": "pqpf_90",
    "snow": "snow_6h",
    "snow_p10": "snow_p10",
    "snow_p50": "snow_p50",
    "snow_p90": "snow_p90",
    "ice": "ice_6h",
    "wind_speed": "wind",
    "wind_direction": "wdir",
    "wind_gust": "gust",
    "sky_cover": "sky",
    "precip_type": "ptype",
    "pop": "pop01",
}

__all__ = [
    "DEFAULT_POINT_ELEMENTS",
    "METEOGRAM_SERIES",
    "PTYPE_LABELS",
    "ProbeElementPlan",
    "UnitKind",
    "probe_element_plan",
    "probeable_elements",
]
