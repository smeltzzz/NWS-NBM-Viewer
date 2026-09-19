"""Versioned API router: /api/v1/* namespace."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import colormaps_route, meta, nbm, tiles
from app.config import settings

# Router mounted at the configured prefix (default /api/v1). Each sub-router
# carries its own path + tags so the OpenAPI schema stays organised.
# NOTE: health router is mounted separately (unversioned) in main.py.
api_router = APIRouter(prefix=settings.api_prefix)

api_router.include_router(meta.router)
api_router.include_router(nbm.router)
api_router.include_router(tiles.router)
api_router.include_router(colormaps_route.router)
