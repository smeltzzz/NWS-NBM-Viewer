"""Colormap catalogue endpoint — consumed by the legend and client renderer."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import colormaps as palettes
from app.config import settings

router = APIRouter(prefix="/colormaps", tags=["colormaps"])


@router.get("", summary="List available colour maps")
def list_colormaps() -> dict[str, object]:
    return {"colormaps": palettes.COLOURMAPS, "default": settings.colormap_list[0]}


@router.get("/{name}", summary="Describe one colour map")
def get_colormap(name: str) -> dict[str, object]:
    cmap = palettes.get_colormap(name)
    if cmap is None:
        raise HTTPException(status_code=404, detail=f"Unknown colour map '{name}'")
    return cmap
