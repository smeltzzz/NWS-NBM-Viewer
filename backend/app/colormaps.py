"""NBM colour ramp catalog + rio-tiler interop.

Stops are normalised to [0, 1] and shared verbatim with the frontend package so
the legend and the rendered tiles always agree. Values settle on the classic
NWS/IEM palettes for temperature, precipitation, wind, humidity and PoP.
"""

from __future__ import annotations

from typing import TypedDict


class ColourStop(TypedDict):
    value: float  # normalised 0..1
    hex: str


COLOURMAPS: dict[str, dict] = {
    "nbm_temp": {
        "label": "Temperature",
        "units_hint": "degC",
        "stops": [
            {"value": 0.00, "hex": "#5E4FA2"},
            {"value": 0.20, "hex": "#3288BD"},
            {"value": 0.45, "hex": "#ABDDA4"},
            {"value": 0.60, "hex": "#FEE08B"},
            {"value": 0.80, "hex": "#FDAE61"},
            {"value": 1.00, "hex": "#D53E4F"},
        ],
    },
    "nbm_precip": {
        "label": "Precipitation",
        "units_hint": "mm",
        "stops": [
            {"value": 0.00, "hex": "#EAF7FF"},
            {"value": 0.20, "hex": "#A6E3A1"},
            {"value": 0.40, "hex": "#38BC5C"},
            {"value": 0.60, "hex": "#FFE14A"},
            {"value": 0.80, "hex": "#FF7B00"},
            {"value": 1.00, "hex": "#7A0177"},
        ],
    },
    "nbm_wind": {
        "label": "Wind speed",
        "units_hint": "m/s",
        "stops": [
            {"value": 0.00, "hex": "#FFFFFF"},
            {"value": 0.20, "hex": "#BEE8FF"},
            {"value": 0.40, "hex": "#6DD3CE"},
            {"value": 0.60, "hex": "#FFE14A"},
            {"value": 0.80, "hex": "#FF7B00"},
            {"value": 1.00, "hex": "#B02A63"},
        ],
    },
    "nbm_rh": {
        "label": "Relative humidity",
        "units_hint": "percent",
        "stops": [
            {"value": 0.00, "hex": "#A6611A"},
            {"value": 0.35, "hex": "#DFC27D"},
            {"value": 0.60, "hex": "#F5F5BD"},
            {"value": 0.80, "hex": "#80CDC1"},
            {"value": 1.00, "hex": "#045A8D"},
        ],
    },
    "nbm_pop": {
        "label": "Probability of precipitation",
        "units_hint": "percent",
        "stops": [
            {"value": 0.00, "hex": "#EAF2FB"},
            {"value": 0.25, "hex": "#B2DF8A"},
            {"value": 0.50, "hex": "#33A02C"},
            {"value": 0.70, "hex": "#FDBF6F"},
            {"value": 0.85, "hex": "#F58025"},
            {"value": 1.00, "hex": "#CB181D"},
        ],
    },
}


def get_colormap(name: str) -> dict | None:
    return COLOURMAPS.get(name)


def to_rio_tiler_colormap(name: str) -> dict[int, tuple[int, int, int, int]]:
    """Build a rio-tiler ``ColorMapParams``-style dict {value(0-255): rgba}.

    Safe to cache; quantised to the 0-255 index space. Raises KeyError for
    unknown ramps; imports rio-tiler lazily so the palette module stays
    importable in lightweight (frontend-sync) contexts.
    """
    from rio_tiler.colormap import parse_color

    cmap = COLOURMAPS[name]
    out: dict[int, tuple[int, int, int, int]] = {}
    for stop in cmap["stops"]:
        idx = int(round(stop["value"] * 255))
        out[idx] = parse_color(stop["hex"])  # (r, g, b, a)
    return out
