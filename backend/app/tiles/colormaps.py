"""Official NWS / WPC / SPC meteorological colour scales + palette mapping.

This module is the single source of truth for the colour ramps painted onto
map tiles.  Every ramp declares:

* ``kind``        – ``"step"`` (classified, one flat colour per breakpoint bin,
                    as used for radar reflectivity and QPF) or ``"linear"``
                    (smoothly interpolated between stops, as used for
                    temperature, wind and CAPE).
* ``breaks``      – value breakpoints.  For ``step`` ramps these are the
                    *N+1* bin edges; for ``linear`` ramps they are the *N*
                    control values, one per colour stop.
* ``colors``      – exact RGBA quadruplets, 0-255.
* ``below`` / ``above`` – colours used outside the covered range.  ``None``
                    means "fully transparent", which is what makes e.g. QPF
                    below 0.01 in and reflectivity below 5 dBZ disappear
                    instead of painting the basemap.

Provenance
----------
``reflectivity`` is the NWS/NEXRAD 5-75 dBZ table, transcribed verbatim from
the AWIPS ``NWSReflectivityExpanded`` colortable (also shipped by MetPy as
``NWSReflectivityExpanded.tbl``).  ``snow_accum`` and ``ice_accum`` are the NWS
snowfall and ice-accretion ramps; ``precip_accum`` is the NWS National Total
Precipitation (NTP) ramp used by WPC QPF graphics.  ``temperature`` is the
AWIPS ``NDFD Min Max Temp`` colormap, embedded below as its exact 150-stop
RGBA ramp.  ``probabilities`` uses the AWIPS probability-of-excessive-rainfall
breakpoints (transparent / green / orange / red / magenta at 10/25/50/75 %).

The mapping entry point is :func:`apply`, which takes a 2-D ``float`` array and
returns a ``uint8`` ``(H, W, 4)`` RGBA image.  It is fully vectorised: a
per-ramp look-up table is built once and cached, so the hot path is a single
``np.searchsorted`` plus a fancy index — no Python loops, no per-pixel work.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Final, Literal

import numpy as np

__all__ = [
    "COLORMAPS",
    "Colormap",
    "Rgba",
    "UnitKind",
    "apply",
    "convert_units",
    "get",
    "legend",
    "names",
    "to_rio_tiler_colormap",
]

Rgba = tuple[int, int, int, int]
UnitKind = Literal["temperature", "length", "speed", "energy", "reflectivity", "percent", "index"]

TRANSPARENT: Final[Rgba] = (0, 0, 0, 0)

# ── Unit conversion ───────────────────────────────────────────────────────────
# Each entry maps (from_system, to_system) -> multiplier, except temperature
# which needs an affine transform.  "imperial" is the canonical/internal system
# because NWS operational products are published that way.
_LENGTH_MM_PER_IN = 25.4
_SPEED_MS_PER_KT = 0.5144444444444444

_CONVERSIONS: dict[str, dict[str, tuple[str, float]]] = {
    "temperature": {"imperial": ("°F", 1.0), "metric": ("°C", 1.0)},
    "length": {"imperial": ("in", 1.0), "metric": ("mm", _LENGTH_MM_PER_IN)},
    "speed": {"imperial": ("kt", 1.0), "metric": ("m/s", _SPEED_MS_PER_KT)},
    "energy": {"imperial": ("J/kg", 1.0), "metric": ("J/kg", 1.0)},
    "reflectivity": {"imperial": ("dBZ", 1.0), "metric": ("dBZ", 1.0)},
    "percent": {"imperial": ("%", 1.0), "metric": ("%", 1.0)},
    "index": {"imperial": ("index", 1.0), "metric": ("index", 1.0)},
}

# GRIB2 delivers physical quantities in SI-ish units; these bring them into the
# canonical (imperial) system each colormap is defined in.
_GIB_TO_IMPERIAL = {
    "temperature": ("K", "°F", lambda v: (v - 273.15) * 9.0 / 5.0 + 32.0),
    "length": ("mm", "in", lambda v: v / _LENGTH_MM_PER_IN),
    "speed": ("m/s", "kt", lambda v: v / _SPEED_MS_PER_KT),
    "energy": ("J/kg", "J/kg", lambda v: v),
    "reflectivity": ("dBZ", "dBZ", lambda v: v),
    "percent": ("%", "%", lambda v: v),
    "index": ("index", "index", lambda v: v),
}


def unit_label(kind: UnitKind, system: str) -> str:
    """Human-readable unit symbol for ``kind`` in ``system``."""
    return _CONVERSIONS[kind][system][0]


def convert_units(values: np.ndarray, kind: UnitKind, source: str, target: str) -> np.ndarray:
    """Convert ``values`` of ``kind`` between unit systems.

    ``source``/``target`` are system names (``"imperial"`` / ``"metric"``) or
    unit symbols (``"K"``, ``"mm"`` …).  Conversion is a no-op when both sides
    resolve to the same system.  Returns a new ``float32`` array.
    """
    if source == target:
        return np.asarray(values, dtype=np.float32)

    src_sys = _resolve_system(kind, source)
    dst_sys = _resolve_system(kind, target)
    if src_sys == dst_sys:
        return np.asarray(values, dtype=np.float32)

    if kind == "temperature":
        # °C -> °F or °F -> °C
        if dst_sys == "imperial":
            return (np.asarray(values, dtype=np.float32) * 9.0 / 5.0 + 32.0).astype(np.float32)
        return ((np.asarray(values, dtype=np.float32) - 32.0) * 5.0 / 9.0).astype(np.float32)

    factor = _CONVERSIONS[kind][dst_sys][1] / _CONVERSIONS[kind][src_sys][1]
    return (np.asarray(values, dtype=np.float32) * np.float32(factor)).astype(np.float32)


def grib_to_colormap_units(values: np.ndarray, kind: UnitKind, grib_unit: str | None):
    """Normalise a decoded GRIB field into a colormap's canonical units.

    Returns ``(array, source_system)``.  Unknown units are passed through
    untouched — a mislabelled inventory line must not silently rescale data.
    """
    table = _GIB_TO_IMPERIAL[kind]
    known = {table[0].lower()}
    if grib_unit and grib_unit.strip().lower() not in known:
        return np.asarray(values, dtype=np.float32), "imperial"
    out = table[2](np.asarray(values, dtype=np.float32))
    return np.asarray(out, dtype=np.float32), "imperial"


def _resolve_system(kind: UnitKind, unit: str) -> str:
    unit = unit.strip().lower()
    if unit in ("imperial", "metric"):
        return unit
    for system, (label, _factor) in _CONVERSIONS[kind].items():
        if label.lower() == unit:
            return system
    raise ValueError(f"{unit!r} is not a recognised {kind} unit")


# ── Embedded AWIPS NDFD temperature ramp (150 stops, exact RGBA) ──────────────
# Source: NOAA/AWIPS II "NDFD Min Max Temp.cmap" (com.raytheon.uf.common.
# dataplane.grid .../common_static/base/colormaps/Grid/).  Stop 0 is the
# below-range sentinel and stop 149 the above-range sentinel.
NDFD_TEMP_RAMP_HEX: Final[str] = (
    "fffffffffffffffffffffffff58ae5fff086e3ffeb82e1ffe67edfffe17addff"
    "dc76dbffd772d9ffd26ed7ffcd6ad5ffc866d3ffc362d1ffbe5ecfffb95acdff"
    "b456cbffaf52c9ffaa4ec7ffa54ac5ffa046c3ff9b42c1ff963ebfff913abdff"
    "8c36bbff8732b9ff822eb7ff7d2ab5ff7826b3ff7322b1ff6e1eafff691aadff"
    "6416abff5f12a9ff5a0ea7ff550aa5ff5006a3ff4b02a1ff340099ff310c9eff"
    "2e18a3ff2b24a8ff2830adff253cb2ff2248b7ff1f54bcff1c60c1ff196cc6ff"
    "1678cbff1384d0ff1090d5ff0d9cdaff0aa8dfff07b4e4ff04c0e9ff01cceeff"
    "00d8f3ff00e4f8ff01e3fcff01e2f2ff01e1e8ff01e0deff01dfd4ff01decaff"
    "01ddc0ff01dcb6ff01dbacff01daa2ff01d998ff01d88eff01d784ff01d67aff"
    "01d570ff01d466ff01d35cff01d252ff01d148ff01d03eff01cf34ff01ce2aff"
    "01cd20ff01cc16ff01cb0cff01ca02ff05cc04ff13cd04ff21ce04ff2fcf04ff"
    "3dd004ff4bd104ff59d204ff67d304ff75d404ff83d504ff91d604ff9fd704ff"
    "add804ffbbd904ffc9da04ffd7db04ffe5dc04fff3dd04ffffde04ffffe100ff"
    "ffda00ffffd300ffffcc00ffffc500ffffbe00ffffb700ffffb000ffffa900ff"
    "ffa200ffff9b00ffff9400ffff8d00ffff8600ffff7f00ffff7800ffff7100ff"
    "ff6a00ffff6a02ffff6103ffff5804ffff4f05ffff4606ffff3d07ffff3408ff"
    "ff2b09ffff220affff190bffff1807fff91709fff3160bffed150dffe7140fff"
    "e11311ffdb1213ffd51115ffcf1017ffc90f19ffc30e1bffbd0d1dffb70c1fff"
    "b10b21ffab0a23ffa50925ff9f0827ff990729ff93062bff"
)


def _parse_hex_ramp(hex_str: str) -> tuple[Rgba, ...]:
    """Parse a packed ``rrggbbaa…`` string into RGBA tuples."""
    if len(hex_str) % 8:
        raise ValueError("hex ramp length must be a multiple of 8")
    out: list[Rgba] = []
    for i in range(0, len(hex_str), 8):
        chunk = hex_str[i : i + 8]
        out.append(tuple(int(chunk[j : j + 2], 16) for j in range(0, 8, 2)))  # type: ignore[misc]
    return tuple(out)


# ── Ramp definitions ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Colormap:
    """A named, breakpoint-driven colour ramp."""

    name: str
    label: str
    kind: Literal["step", "linear"]
    unit_kind: UnitKind
    breaks: tuple[float, ...]
    colors: tuple[Rgba, ...]
    source: str
    below: Rgba | None = None
    above: Rgba | None = None
    nodata: Rgba = TRANSPARENT
    #: Values at or below this are treated as "nothing to draw" (transparent).
    threshold: float | None = None
    #: When set, alpha scales linearly with the value across the covered range
    #: (used by probability ramps: 0 % -> transparent, 100 % -> saturated).
    alpha_ramp: bool = False
    #: Decimal places used when formatting legend labels.
    precision: int = 2
    extra: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = len(self.breaks) - 1 if self.kind == "step" else len(self.breaks)
        if len(self.colors) != expected:
            raise ValueError(
                f"{self.name}: {self.kind} ramp needs {expected} colours for "
                f"{len(self.breaks)} breakpoints, got {len(self.colors)}"
            )
        if any(b <= a for a, b in zip(self.breaks, self.breaks[1:])):
            raise ValueError(f"{self.name}: breakpoints must be strictly increasing")

    # -- convenience -------------------------------------------------------
    @property
    def native_units(self) -> str:
        return _CONVERSIONS[self.unit_kind]["imperial"][0]

    def breaks_in(self, system: str) -> tuple[float, ...]:
        """Breakpoints expressed in ``system`` ("imperial"/"metric")."""
        if system == "imperial":
            return self.breaks
        converted = convert_units(
            np.asarray(self.breaks, dtype=np.float32),
            self.unit_kind,
            "imperial",
            system,
        )
        return tuple(float(v) for v in converted)

    @property
    def min_value(self) -> float:
        return float(self.breaks[0])

    @property
    def max_value(self) -> float:
        return float(self.breaks[-1])


def _hex(value: str) -> Rgba:
    """``"rrggbb"`` -> ``(r, g, b, 255)``."""
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected rrggbb, got {value!r}")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255)


# ── NWS NEXRAD base reflectivity (5..75 dBZ, 15 levels) ───────────────────────
# AWIPS "NWSReflectivityExpanded": 5 dBZ cyan -> 65 dBZ magenta -> 70 dBZ
# purple -> 75 dBZ white.  Below 5 dBZ is transparent (clear air).
_REFLECTIVITY_DBZ: Final[tuple[float, ...]] = tuple(
    float(v) for v in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75)
)
_REFLECTIVITY_RGBA: Final[tuple[Rgba, ...]] = (
    (4, 233, 231, 255),
    (1, 159, 244, 255),
    (3, 0, 244, 255),
    (2, 253, 2, 255),
    (1, 197, 1, 255),
    (0, 142, 0, 255),
    (253, 248, 2, 255),
    (229, 188, 0, 255),
    (253, 149, 0, 255),
    (253, 0, 0, 255),
    (212, 0, 0, 255),
    (188, 0, 0, 255),
    (248, 0, 253, 255),
    (152, 84, 198, 255),
    (253, 253, 253, 255),
)

# ── WPC / NWS National Total Precipitation (NTP) QPF scale ────────────────────
_PRECIP_BREAKS: Final[tuple[float, ...]] = (
    0.01,
    0.10,
    0.25,
    0.50,
    0.75,
    1.00,
    1.50,
    2.00,
    2.50,
    3.00,
    4.00,
    5.00,
    6.00,
    8.00,
    10.00,
    15.00,
    999.0,
)
_PRECIP_RGBA: Final[tuple[Rgba, ...]] = tuple(
    _hex(c)
    for c in (
        "cbcb97",
        "989865",
        "00ebe7",
        "00a0f5",
        "000df5",
        "00ff00",
        "00c600",
        "008e00",
        "fef700",
        "e5bc00",
        "ff8500",
        "ff0000",
        "af0000",
        "640000",
        "ff00fe",
        "a152bc",
    )
)

# ── NWS Winter Weather snowfall accumulation ──────────────────────────────────
_SNOW_BREAKS: Final[tuple[float, ...]] = (
    0.1,
    1.0,
    2.0,
    3.0,
    4.0,
    6.0,
    8.0,
    12.0,
    18.0,
    24.0,
    30.0,
    48.0,
)
_SNOW_RGBA: Final[tuple[Rgba, ...]] = (
    (189, 215, 231, 255),
    (107, 174, 214, 255),
    (49, 130, 189, 255),
    (8, 81, 156, 255),
    (8, 38, 148, 255),
    (255, 255, 150, 255),
    (255, 196, 0, 255),
    (255, 135, 0, 255),
    (219, 20, 0, 255),
    (158, 0, 0, 255),
    (105, 0, 0, 255),
)

# ── NWS ice accretion ─────────────────────────────────────────────────────────
_ICE_BREAKS: Final[tuple[float, ...]] = (0.01, 0.10, 0.25, 0.50, 0.75, 1.00, 2.00)
_ICE_RGBA: Final[tuple[Rgba, ...]] = tuple(
    _hex(c) for c in ("f4ea3b", "ffc000", "fe0000", "c00000", "9966ff", "730ac7")
)

# ── NWS/SPC wind speed & gust ramp (turquoise -> yellow -> red -> purple) ─────
_WIND_BREAKS: Final[tuple[float, ...]] = (
    0.0,
    5.0,
    10.0,
    15.0,
    20.0,
    25.0,
    30.0,
    35.0,
    40.0,
    45.0,
    50.0,
    55.0,
    60.0,
    65.0,
    70.0,
    80.0,
    100.0,
)
_WIND_RGBA: Final[tuple[Rgba, ...]] = tuple(
    _hex(c)
    for c in (
        "d8f0f5",
        "a1e8e0",
        "4fd8c4",
        "00c8a0",
        "7fdc4a",
        "c8e600",
        "ffe100",
        "ffb400",
        "ff8c00",
        "ff5a00",
        "ff2400",
        "e00000",
        "c00000",
        "e000a0",
        "b000c8",
        "7800a0",
        "4b0082",
    )
)

# ── Diverging wind vector component (U/V) ramp: blue (negative) -> white
#    (calm) -> red (positive), symmetric about zero in knots. ─────────────────
_WIND_COMPONENT_BREAKS: Final[tuple[float, ...]] = (
    -40.0,
    -20.0,
    -10.0,
    -5.0,
    0.0,
    5.0,
    10.0,
    20.0,
    40.0,
)
_WIND_COMPONENT_RGBA: Final[tuple[Rgba, ...]] = tuple(
    _hex(c)
    for c in (
        "0d2d8f",
        "1f60c9",
        "5aa3e8",
        "9fd2f2",
        "f4f7f8",
        "f5c2a1",
        "ef8a4c",
        "d94f1f",
        "8f140d",
    )
)

# Gusts reuse the same ramp but extend the top of the range to 120 kt.
_GUST_BREAKS: Final[tuple[float, ...]] = _WIND_BREAKS + (120.0,)
_GUST_RGBA: Final[tuple[Rgba, ...]] = _WIND_RGBA + (_hex("2b0050"),)

# ── SPC convective CAPE ───────────────────────────────────────────────────────
_CAPE_BREAKS: Final[tuple[float, ...]] = (
    0.0,
    100.0,
    500.0,
    1000.0,
    1500.0,
    2000.0,
    2500.0,
    3000.0,
    4000.0,
    5000.0,
    6000.0,
)
_CAPE_RGBA: Final[tuple[Rgba, ...]] = tuple(
    _hex(c)
    for c in (
        "f2f2f2",
        "d7d7d7",
        "c8e6c8",
        "64c864",
        "f0f000",
        "ffa000",
        "ff6400",
        "ff0000",
        "e000a0",
        "a000c8",
        "6400a0",
    )
)

# ── Probability (0 % transparent -> 100 % saturated) ──────────────────────────
# Breakpoints follow the AWIPS "Probability of Excessive Rainfall" table:
# <10 % transparent, 10-25 % green, 25-50 % orange, 50-75 % red, 75-100 % magenta.
_PROB_BREAKS: Final[tuple[float, ...]] = (0.0, 10.0, 25.0, 50.0, 75.0, 100.0)
_PROB_RGBA: Final[tuple[Rgba, ...]] = (
    (43, 155, 43, 255),
    (43, 155, 43, 255),
    (255, 175, 23, 255),
    (255, 60, 51, 255),
    (255, 9, 209, 255),
)


def _ndfd_temperature_colormap() -> Colormap:
    """Build the NDFD temperature ramp from the embedded AWIPS RGBA table.

    The 150-stop AWIPS table is resampled onto explicit 5 °F breakpoints
    between -40 °F and 120 °F, discarding the two sentinel stops at either end.
    """
    ramp = _parse_hex_ramp(NDFD_TEMP_RAMP_HEX)
    # AWIPS pads the below-range sentinel across the first few stops; strip the
    # whole leading run so -40 degF lands on the first real colour rather than
    # on white.  The trailing entry is the genuine hot end, so it is kept.
    sentinel = ramp[0]
    first_real = 0
    while first_real < len(ramp) - 1 and ramp[first_real] == sentinel:
        first_real += 1
    data_stops = ramp[first_real:]
    breaks = tuple(float(v) for v in range(-40, 121, 5))
    positions = np.linspace(0.0, len(data_stops) - 1, len(breaks))
    colors = tuple(data_stops[int(round(p))] for p in positions)
    return Colormap(
        name="temperature",
        label="Temperature",
        kind="linear",
        unit_kind="temperature",
        breaks=breaks,
        colors=colors,
        source="NOAA/AWIPS II 'NDFD Min Max Temp.cmap'",
        below=ramp[0],
        above=ramp[-1],
        precision=0,
    )


def _build_registry() -> dict[str, Colormap]:
    registry: dict[str, Colormap] = {}

    def add(cmap: Colormap) -> None:
        registry[cmap.name] = cmap

    add(_ndfd_temperature_colormap())
    add(
        Colormap(
            name="precip_accum",
            label="Precipitation accumulation",
            kind="step",
            unit_kind="length",
            breaks=_PRECIP_BREAKS,
            colors=_PRECIP_RGBA,
            source="NWS National Total Precipitation (NTP) / WPC QPF scale",
            threshold=0.01,
            above=(255, 255, 255, 255),
            precision=2,
        )
    )
    add(
        Colormap(
            name="snow_accum",
            label="Snow accumulation",
            kind="step",
            unit_kind="length",
            breaks=_SNOW_BREAKS,
            colors=_SNOW_RGBA,
            source="NWS Winter Weather snowfall ramp",
            threshold=0.1,
            above=(43, 0, 46, 255),
            precision=1,
        )
    )
    add(
        Colormap(
            name="ice_accum",
            label="Ice accumulation",
            kind="step",
            unit_kind="length",
            breaks=_ICE_BREAKS,
            colors=_ICE_RGBA,
            source="NWS ice accretion ramp",
            threshold=0.01,
            above=(37, 4, 91, 255),
            precision=2,
        )
    )
    add(
        Colormap(
            name="wind_speed",
            label="Wind speed",
            kind="linear",
            unit_kind="speed",
            breaks=_WIND_BREAKS,
            colors=_WIND_RGBA,
            source="NWS operational surface wind scale",
            above=(75, 0, 130, 255),
            precision=0,
        )
    )
    add(
        Colormap(
            name="wind_gust",
            label="Wind gust",
            kind="linear",
            unit_kind="speed",
            breaks=_GUST_BREAKS,
            colors=_GUST_RGBA,
            source="NWS operational surface wind scale (gust range)",
            above=(43, 0, 80, 255),
            precision=0,
        )
    )
    add(
        Colormap(
            name="wind_component",
            label="Wind vector component (U/V)",
            kind="linear",
            unit_kind="speed",
            breaks=_WIND_COMPONENT_BREAKS,
            colors=_WIND_COMPONENT_RGBA,
            source="Diverging zonal/meridional wind component scale",
            below=(13, 45, 143, 255),
            above=(143, 20, 13, 255),
            precision=0,
        )
    )
    add(
        Colormap(
            name="cape",
            label="Surface-based CAPE",
            kind="linear",
            unit_kind="energy",
            breaks=_CAPE_BREAKS,
            colors=_CAPE_RGBA,
            source="SPC convective CAPE analysis scale",
            threshold=100.0,
            above=(100, 0, 160, 255),
            precision=0,
        )
    )
    add(
        Colormap(
            name="reflectivity",
            label="Composite reflectivity",
            kind="step",
            unit_kind="reflectivity",
            breaks=_REFLECTIVITY_DBZ + (999.0,),
            colors=_REFLECTIVITY_RGBA,
            source="NWS NEXRAD 5-75 dBZ base reflectivity table",
            threshold=5.0,
            above=(253, 253, 253, 255),
            precision=0,
        )
    )
    add(
        Colormap(
            name="probabilities",
            label="Probability",
            kind="step",
            unit_kind="percent",
            breaks=_PROB_BREAKS,
            colors=_PROB_RGBA,
            source="AWIPS probability-of-excessive-rainfall breakpoints",
            threshold=10.0,
            alpha_ramp=True,
            precision=0,
        )
    )
    return registry


COLORMAPS: Final[dict[str, Colormap]] = _build_registry()


def names() -> list[str]:
    """All registered colormap names."""
    return sorted(COLORMAPS)


def get(name: str) -> Colormap:
    """Look up a colormap by name (raises ``KeyError`` when unknown)."""
    try:
        return COLORMAPS[name]
    except KeyError:
        raise KeyError(f"unknown colormap {name!r}; available: {', '.join(names())}") from None


# ── Look-up table construction ────────────────────────────────────────────────
# For "linear" ramps we pre-bake a dense RGBA table plus its value axis.  The
# table is quantised in value space (not index space) so that ramps with
# non-uniform breakpoints — CAPE, wind — stay accurate at the fine end.
_LUT_LEVELS = 4096


@functools.lru_cache(maxsize=64)
def _lut(name: str, units: str, levels: int) -> tuple[np.ndarray, np.ndarray]:
    """Pre-baked ``(rgba_table, value_axis)`` for a linear ramp.

    Keyed by unit system as well as name: the table's value axis must live in
    the same system as the data it will be indexed with, otherwise a metric
    field gets mapped against imperial stops.
    """
    cmap = COLORMAPS[name]
    if cmap.kind == "step":
        # Step ramps need no interpolation table; searchsorted on `breaks`
        # selects the bin directly.
        return np.empty((0, 4), np.uint8), np.empty((0,), np.float32)

    stops = np.asarray(cmap.breaks_in(units), dtype=np.float64)
    values = np.linspace(stops[0], stops[-1], levels, dtype=np.float64)
    rgba = np.asarray(cmap.colors, dtype=np.float64)
    table = np.empty((levels, 4), dtype=np.uint8)
    for channel in range(4):
        table[:, channel] = np.clip(
            np.round(np.interp(values, stops, rgba[:, channel])), 0, 255
        ).astype(np.uint8)
    return table, values.astype(np.float32)


# ── Mapping ───────────────────────────────────────────────────────────────────
def apply(
    values: np.ndarray,
    colormap: str | Colormap,
    *,
    units: str = "imperial",
    opacity: float = 1.0,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """Colour-map a 2-D field into a ``uint8`` ``(H, W, 4)`` RGBA image.

    Parameters
    ----------
    values
        2-D float array in the colormap's *canonical* units (see
        :func:`grib_to_colormap_units`) — always imperial, whatever ``units``
        says.  NaN is treated as no-data.
    colormap
        Registered colormap name or :class:`Colormap` instance.
    units
        ``"imperial"`` or ``"metric"``.  With ``"metric"`` both the data and
        the breakpoints are converted before mapping, so the two are always
        compared in the same system and the rendered pixels are identical —
        the parameter changes the legend, never the tile.
    opacity
        Global alpha multiplier in ``[0, 1]``.
    mask
        Optional boolean array; ``True`` pixels are forced transparent.
    """
    cmap = get(colormap) if isinstance(colormap, str) else colormap
    data = np.asarray(values, dtype=np.float32)
    if data.ndim != 2:
        raise ValueError(f"expected a 2-D array, got shape {data.shape}")

    if units not in ("imperial", "metric"):
        raise ValueError(f"units must be 'imperial' or 'metric', got {units!r}")
    if units == "metric":
        data = convert_units(data, cmap.unit_kind, "imperial", "metric")
        breaks = np.asarray(cmap.breaks_in("metric"), dtype=np.float32)
        threshold = (
            None
            if cmap.threshold is None
            else float(
                convert_units(
                    np.asarray([cmap.threshold], np.float32),
                    cmap.unit_kind,
                    "imperial",
                    "metric",
                )[0]
            )
        )
    else:
        breaks = np.asarray(cmap.breaks, dtype=np.float32)
        threshold = None if cmap.threshold is None else float(cmap.threshold)

    invalid = ~np.isfinite(data)
    if mask is not None:
        invalid |= np.asarray(mask, dtype=bool)

    if cmap.kind == "step":
        image = _map_step(data, cmap, breaks, threshold)
    else:
        image = _map_linear(data, cmap, units, threshold)

    # Alpha: honour no-data, the global opacity, and (for probability ramps)
    # the value itself so 0 % fades out completely.
    alpha = image[:, :, 3].astype(np.float32)
    if opacity != 1.0:
        alpha *= np.float32(min(max(opacity, 0.0), 1.0))
    if cmap.alpha_ramp:
        span = max(float(breaks[-1] - breaks[0]), 1e-6)
        normalised = np.clip((data - breaks[0]) / span, 0.0, 1.0)
        alpha *= normalised.astype(np.float32)
    alpha[invalid] = 0.0
    image[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)

    nodata = np.asarray(cmap.nodata, dtype=np.uint8)
    image[invalid] = nodata
    return image


def _map_step(
    data: np.ndarray, cmap: Colormap, breaks: np.ndarray, threshold: float | None
) -> np.ndarray:
    """Classified mapping: each breakpoint bin gets one flat RGBA.

    Bin ``i`` covers ``[breaks[i], breaks[i+1])``.  A value exactly equal to
    the top breakpoint belongs to the last bin — only values *strictly*
    outside the range fall onto the ``below``/``above`` sentinels, so e.g.
    100 % probability renders at full saturation instead of disappearing.
    """
    colors = np.asarray(cmap.colors, dtype=np.uint8)
    index = np.searchsorted(breaks, data, side="right") - 1
    np.clip(index, 0, len(colors) - 1, out=index)
    image = colors[index].copy()

    below = cmap.below if cmap.below is not None else TRANSPARENT
    image[data < breaks[0]] = below
    if cmap.above is not None:
        image[data > breaks[-1]] = np.asarray(cmap.above, dtype=np.uint8)

    if threshold is not None:
        image[data < np.float32(threshold)] = TRANSPARENT
    return image


def _map_linear(
    data: np.ndarray, cmap: Colormap, units: str, threshold: float | None
) -> np.ndarray:
    """Smooth mapping through the ramp's pre-baked look-up table."""
    if cmap.name not in COLORMAPS or COLORMAPS[cmap.name] is not cmap:
        raise KeyError(
            f"colormap {cmap.name!r} is not registered; linear ramps need a "
            f"cached look-up table keyed by name"
        )
    table, axis = _lut(cmap.name, units, _LUT_LEVELS)
    if table.size == 0:  # pragma: no cover - defensive
        raise RuntimeError(f"{cmap.name}: missing interpolation table")

    # np.clip propagates NaN, so park no-data pixels on the low stop first;
    # apply() zeroes their alpha afterwards.  Without this the cast below
    # warns and writes garbage into the alpha channel.
    safe = np.where(np.isfinite(data), data, float(axis[0]))
    clamped = np.clip(safe, float(axis[0]), float(axis[-1]))
    index = np.searchsorted(axis, clamped, side="left") - 1
    np.clip(index, 0, len(axis) - 2, out=index)
    span = axis[index + 1] - axis[index]
    span = np.where(span == 0, 1.0, span)
    frac = (clamped - axis[index]) / span

    low = table[index].astype(np.float32)
    high = table[index + 1].astype(np.float32)
    image = (low + (high - low) * frac[..., None]).astype(np.uint8)

    if cmap.below is not None:
        image[data < float(axis[0])] = np.asarray(cmap.below, np.uint8)
    if cmap.above is not None:
        image[data > float(axis[-1])] = np.asarray(cmap.above, np.uint8)
    if threshold is not None:
        image[data < np.float32(threshold)] = TRANSPARENT
    return image


# ── Legend / interop ──────────────────────────────────────────────────────────
def legend(name: str, *, units: str = "imperial") -> dict[str, object]:
    """Legend payload for the frontend, in the requested unit system."""
    cmap = get(name)
    breaks = cmap.breaks_in(units)
    label = unit_label(cmap.unit_kind, units)

    if cmap.kind == "step":
        entries = []
        for i, color in enumerate(cmap.colors):
            lo, hi = breaks[i], breaks[i + 1]
            hi_label = "∞" if hi > 900 else f"{hi:.{cmap.precision}f}"
            entries.append(
                {
                    "value": lo,
                    "label": f"{lo:.{cmap.precision}f} – {hi_label}",
                    "hex": _to_hex(color),
                    "alpha": color[3] / 255,
                }
            )
    else:
        entries = [
            {
                "value": value,
                "label": f"{value:.{cmap.precision}f}",
                "hex": _to_hex(color),
                "alpha": color[3] / 255,
            }
            for value, color in zip(breaks, cmap.colors)
        ]

    return {
        "name": cmap.name,
        "label": cmap.label,
        "units": label,
        "kind": cmap.kind,
        "source": cmap.source,
        "threshold": cmap.threshold,
        "stops": entries,
    }


def _to_hex(color: Rgba) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color[:3])


def to_rio_tiler_colormap(name: str) -> dict[int, tuple[int, int, int, int]]:
    """Quantise a linear ramp into rio-tiler's ``{0-255: rgba}`` form.

    Kept for the legacy ``/tiles/demo`` route and for clients that want a
    client-side ramp.  Step ramps cannot be represented this way and raise.
    """
    cmap = get(name)
    if cmap.kind != "linear":
        raise ValueError(f"{name} is a step ramp and has no rio-tiler equivalent")
    table, _axis = _lut(cmap.name, "imperial", _LUT_LEVELS)
    return {
        index: tuple(int(v) for v in table[int(index * (_LUT_LEVELS - 1) / 255)])
        for index in range(256)
    }


def describe() -> list[dict[str, object]]:
    """Compact machine-readable description of every registered ramp."""
    out = []
    for name in names():
        cmap = COLORMAPS[name]
        out.append(
            {
                "name": name,
                "label": cmap.label,
                "kind": cmap.kind,
                "units": cmap.native_units,
                "source": cmap.source,
                "min": cmap.min_value,
                "max": cmap.max_value,
                "threshold": cmap.threshold,
                "stops": len(cmap.colors),
            }
        )
    return out
