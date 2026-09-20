"""Versioned API router: /api/v1/* namespace."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import poller_ops, probe, runs
from app.api.endpoints import tiles as nbm_tiles
from app.api.routes import colormaps_route, meta, nbm, tiles
from app.config import settings

# Router mounted at the configured prefix (default /api/v1). Each sub-router
# carries its own path + tags so the OpenAPI schema stays organised.
# NOTE: health router is mounted separately (unversioned) in main.py.
api_router = APIRouter(prefix=settings.api_prefix)

api_router.include_router(meta.router)
api_router.include_router(nbm.router)
# The GRIB/WebP tile pipeline owns /tiles/*; the legacy demo PNG surface is
# mounted after it so its narrower routes cannot shadow the tile pattern.
api_router.include_router(nbm_tiles.router)
api_router.include_router(tiles.router)
api_router.include_router(colormaps_route.router)
api_router.include_router(runs.router)
api_router.include_router(probe.router)
# Ops surface for the 24/7 poller (status / manual tick / retention sweep).
api_router.include_router(poller_ops.router)
