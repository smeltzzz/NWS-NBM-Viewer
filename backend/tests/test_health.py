"""Health / version endpoint contract tests (no network required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from main import app


def test_root_landing() -> None:
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert settings.app_name in response.text


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == settings.app_version


def test_version_metadata() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/version")
        assert response.status_code == 200
        body = response.json()
        assert body["service"] == settings.app_name
        assert body["nbm"]["defaultVariable"] == settings.nbm_default_variable


def test_openapi_schema_present() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        # Operational health is unversioned; the versioned API carries meta + nbm.
        assert "/health/live" in schema["paths"]
        assert f"{settings.api_prefix}/nbm/domains" in schema["paths"]
        assert f"{settings.api_prefix}/version" in schema["paths"]
