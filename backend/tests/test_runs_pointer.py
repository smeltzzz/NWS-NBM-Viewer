"""``/api/v1/runs/latest`` pointer semantics + poller ops endpoints."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import pointer as pointer_mod
from main import app


class DictPointerCache:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl_seconds=None):
        self.store[key] = value

    def delete(self, key):
        return self.store.pop(key, None) is not None


@pytest.fixture()
def pointer_cache(monkeypatch):
    cache = DictPointerCache()
    monkeypatch.setattr(pointer_mod, "_cache", lambda: cache)
    pointer_mod.reset_pointer()
    yield cache
    pointer_mod.reset_pointer()


def _fresh_pointer(**updates):
    base = dict(
        date="20260101",
        cycle=12,
        domain="co",
        product="core",
        available_forecast_hours=list(range(1, 37)),
        complete=True,
        state="complete",
        source="poller",
    )
    base.update(updates)
    return pointer_mod.RunPointer(**base)


def test_latest_reads_fresh_pointer_without_live_discovery(pointer_cache, monkeypatch):
    async def explode(*_a, **_k):  # live discovery must NOT run
        raise AssertionError("must not hit NOAA while the pointer is fresh")

    monkeypatch.setattr("app.api.endpoints.runs.discover_latest_run", explode)
    pointer_mod.publish_pointer(_fresh_pointer())

    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/runs/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "20260101"
    assert body["cycle"] == 12
    assert body["run"]["available_forecast_hours"] == list(range(1, 37))
    assert body["pointer"]["cycle"] == "2026010112"
    assert body["pointer"]["state"] == "complete"
    assert body["pointer"]["stale"] is False


def test_latest_falls_back_to_live_discovery_when_pointer_stale(pointer_cache, monkeypatch):
    from app.api.endpoints.runs import RunMetadata

    aged = _fresh_pointer(
        observed_at=datetime.fromtimestamp(time.time() - 40 * 60, tz=timezone.utc)
    )
    pointer_mod.publish_pointer(aged)

    async def fake_discover(*_a, **_k):
        return RunMetadata(
            date="20260101",
            cycle=13,
            cycle_time="2026-01-01T13:00:00Z",
            domain="co",
            product="core",
            available_forecast_hours=[1, 2, 3],
        )

    monkeypatch.setattr("app.api.endpoints.runs.discover_latest_run", fake_discover)
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/runs/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["cycle"] == 13
    assert body["pointer"]["source"] == "live"
    # The live result re-publishes the pointer for the next O(1) read.
    stored = pointer_mod.fetch_pointer("co", "core")
    assert stored is not None and stored.cycle == 13


def test_latest_force_bypasses_pointer(pointer_cache, monkeypatch):
    from app.api.endpoints.runs import RunMetadata

    pointer_mod.publish_pointer(_fresh_pointer(cycle=5))

    async def fake_discover(*_a, **_k):
        return RunMetadata(
            date="20260101",
            cycle=23,
            cycle_time="2026-01-01T23:00:00Z",
            domain="co",
            product="core",
            available_forecast_hours=list(range(1, 37)),
        )

    monkeypatch.setattr("app.api.endpoints.runs.discover_latest_run", fake_discover)
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/runs/latest?force=1")
    assert response.json()["cycle"] == 23


def test_latest_degrades_to_last_known_good_on_upstream_failure(pointer_cache, monkeypatch):
    pointer_mod.publish_pointer(_fresh_pointer())

    async def explode(*_a, **_k):
        raise ConnectionError("noaa unreachable")

    monkeypatch.setattr("app.api.endpoints.runs.discover_latest_run", explode)
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/runs/latest?force=1")
    assert response.status_code == 200
    body = response.json()
    assert body["cycle"] == 12  # last known-good still served
    assert body["pointer"]["stale"] is True
    assert body["pointer"]["degraded"] is True


def test_latest_without_pointer_or_upstream_returns_null_run(pointer_cache, monkeypatch):
    async def none_discover(*_a, **_k):
        return None

    monkeypatch.setattr("app.api.endpoints.runs.discover_latest_run", none_discover)
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/runs/latest?force=1")
    assert response.status_code == 200
    assert response.json() == {"run": None}


# ── /api/v1/poller/* ─────────────────────────────────────────────────────────
def test_poller_status_endpoint(pointer_cache):
    pointer_mod.publish_pointer(_fresh_pointer())
    with TestClient(app) as client:
        response = client.get(f"{settings.api_prefix}/poller/status")
    assert response.status_code == 200
    body = response.json()
    assert "interval_minutes" in body
    assert body["interval_minutes"] == settings.poller_interval_minutes
    assert body["pointer"]["date"] == "20260101" and body["pointer"]["cycle"] == 12
    assert "s3" in body


def test_poller_evict_endpoint_runs_sweep():
    with TestClient(app) as client:
        response = client.post(f"{settings.api_prefix}/poller/evict")
    assert response.status_code == 200
    body = response.json()
    assert body["max_age_hours"] == settings.cache_max_age_hours


def test_manual_poll_is_rate_limited(monkeypatch):
    async def none_discover(*_a, **_k):
        return None

    monkeypatch.setattr("app.api.endpoints.runs.discover_latest_run", none_discover)
    import app.api.endpoints.poller_ops as ops

    monkeypatch.setattr(ops, "_last_manual_poll", 0.0)
    with TestClient(app) as client:
        first = client.post(f"{settings.api_prefix}/poller/poll")
        second = client.post(f"{settings.api_prefix}/poller/poll")
    assert first.status_code == 200
    assert second.status_code == 429
