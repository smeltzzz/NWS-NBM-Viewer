"""NBM metadata endpoints: domains, products, elements, layout.

Everything here is derived from the static catalog (``app/static/catalog.py``)
and ``app/domains.py``. The inventory milestone replaces the placeholder layout
with the authoritative per-cycle forecast-hour matrix read from GRIB .idx files.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.config import NbmDomain, NbmProduct
from app.domains import DOMAINS
from app.static import catalog

router = APIRouter(prefix="/nbm", tags=["nbm"])


@router.get("/domains", summary="List NBM publication domains")
def list_domains() -> dict[str, object]:
    return {"domains": list(DOMAINS.values())}


@router.get("/products", summary="List NBM product streams")
def list_products() -> dict[str, object]:
    products = [
        {
            "code": "core",
            "name": "Core / deterministic",
            "description": "Deterministic elements and statistical-process columns "
            "(mean, spread, percentiles, threshold probabilities) across CONUS, "
            "Alaska, Hawaii, Puerto Rico and Guam.",
            "cycles": "hourly 00-23z",
        },
        {
            "code": "qmd",
            "name": "Quantile Mapping & Dressing (QMD)",
            "description": "Calibrated PoP/QPF percentiles from the QMD ensemble.",
            "cycles": "00/06/12/18z",
        },
    ]
    return {"products": products}


@router.get("/elements", summary="List deterministic core elements")
def list_elements() -> dict[str, object]:
    return {"elements": catalog.CORE_ELEMENTS}


@router.get("/elements/{variable}", summary="Describe one core element")
def get_element(variable: str) -> dict[str, object]:
    element = catalog.CORE_ELEMENTS.get(variable)
    if element is None:
        raise HTTPException(status_code=404, detail=f"Unknown core element '{variable}'")
    return {"variable": variable, **element}


@router.get("/statistics", summary="Statistical processes (aggregates)")
def list_statistics() -> dict[str, object]:
    return {"statistics": catalog.STATISTICAL_PROCESSES}


@router.get("/percentiles", summary="Direct percentile families")
def list_percentiles() -> dict[str, object]:
    return {"families": catalog.PERCENTILE_FAMILIES}


@router.get("/exceedance", summary="Exceedance / threshold-probability families")
def list_exceedance() -> dict[str, object]:
    return {"families": catalog.EXCEEDANCE_FAMILIES}


@router.get("/qmd", summary="QMD variables")
def list_qmd() -> dict[str, object]:
    return {"variables": catalog.QMD_VARIABLES}


# ── Per-cycle layout (placeholder until inventory milestone) ─────────────────
CORE_LAYOUT = [(1, 36, 1), (39, 192, 3), (198, 264, 6)]
QMD_LAYOUT = [(1, 36, 1), (39, 192, 3)]


def _hours_between(start: int, end_inc: int, step: int) -> list[int]:
    return list(range(start, end_inc + 1, step))


@router.get("/layout/{domain}/{product}", summary="Forecast-hour layout")
def get_layout(
    domain: NbmDomain,
    product: NbmProduct,
    include: Literal["hours", "windows", "all"] = Query(
        default="all", description="hours = flat list, windows = step ranges, all = both"
    ),
) -> dict[str, object]:
    """Approximate forecast-hour layout for a domain/product.

    TODO(inventory-milestone): read the authoritative matrix from the latest
    cycle's .idx instead of this static approximation.
    """
    galaxy = DOMAINS[domain]
    segments = CORE_LAYOUT if product == "core" else QMD_LAYOUT

    windows = [{"start": s, "end": e, "step": st} for s, e, st in segments]
    hours = [h for s, e, st in segments for h in _hours_between(s, e, st)]

    body: dict[str, object] = {
        "domain": domain,
        "domainName": galaxy["name"],
        "resolutionKm": galaxy["resolution_km"],
        "product": product,
        "isStaticApproximation": True,
        "maxForecastHour": max(hours),
    }
    if include in ("hours", "all"):
        body["hours"] = hours
    if include in ("windows", "all"):
        body["windows"] = windows
    return body
