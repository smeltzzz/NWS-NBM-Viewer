"""NBM metadata endpoint contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from main import app


def test_domains_include_conus() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/nbm/domains")
        assert response.status_code == 200
        codes = {d["code"] for d in response.json()["domains"]}
        assert "co" in codes and "ak" in codes


def test_elements_lookup() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/nbm/elements/tmp")
        assert response.status_code == 200
        assert response.json()["variable"] == "tmp"


def test_elements_unknown_404() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/nbm/elements/doesnotexist")
        assert response.status_code == 404


def test_layout_hours_ascending() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/nbm/layout/co/core?include=hours")
        assert response.status_code == 200
        hours = response.json()["hours"]
        assert hours == sorted(hours)
        assert hours[0] == 1


def test_layout_rejects_bad_domain() -> None:
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/nbm/layout/xx/core")
        assert response.status_code == 422
