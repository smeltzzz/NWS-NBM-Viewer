"""Tile endpoint contract tests (rio-tiler optional in CI).

The demo surface renders only when rasterio/rio-tiler are installed (they are
in the production image). These tests skip gracefully otherwise, so a
geospatial-free dev venv can still run the full contract suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.tiles import get_tile_renderer
from main import app

pytestmark = pytest.mark.skipif(
    not get_tile_renderer().rio_tiler_available,
    reason="rasterio/rio-tiler not installed in this environment",
)


def test_capabilities() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/tiles/demo/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert body["format"] == "png"
        assert body["source"] == "demo"


def test_demo_tile_renders_png() -> None:
    with TestClient(app) as client:
        # z=2, x=1, y=1 is valid and comfortably inside the demo surface.
        response = client.get(f"{settings.api_prefix}/tiles/demo/2/1/1.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")
        assert len(response.content) > 100


def test_demo_tile_invalid_zoom_rejected() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/tiles/demo/40/0/0.png")
        assert response.status_code == 422


def test_demo_tile_unknown_colormap_404() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/tiles/demo/2/1/1.png?colormap=nope")
        assert response.status_code == 404
