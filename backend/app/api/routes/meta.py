"""Application metadata endpoint (versioned under /api/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["meta"])


@router.get("/version", summary="Service metadata")
def version() -> dict[str, object]:
    """Static build/capability metadata. Safe to render in the UI."""
    return {
        "service": settings.app_name,
        "apiVersion": "v1",
        "appVersion": settings.app_version,
        "environment": settings.environment,
        "apiPrefix": settings.api_prefix,
        "nbm": {
            "defaultDomain": settings.nbm_default_domain,
            "defaultProduct": settings.nbm_default_product,
            "defaultVariable": settings.nbm_default_variable,
            "defaultForecastHour": settings.nbm_default_forecast_hour,
            "maxForecastHour": settings.nbm_max_forecast_hour,
        },
    }
