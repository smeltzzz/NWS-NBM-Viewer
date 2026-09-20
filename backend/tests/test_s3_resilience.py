"""Graceful degradation for S3 rate limits / transient NOAA errors."""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.core.s3_client import (
    S3Client,
    S3HTTPError,
    S3ObjectNotFound,
    S3Throttled,
)


@pytest.fixture()
def client(tmp_path):
    return S3Client(cache_dir=tmp_path, base_url="https://example.test")


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(settings, "s3_retry_base_delay_seconds", 0.01)
    monkeypatch.setattr(settings, "s3_retry_max_delay_seconds", 0.05)
    monkeypatch.setattr(settings, "s3_retry_max_attempts", 3)
    monkeypatch.setattr(settings, "s3_throttle_cooldown_seconds", 0.5)


async def _raise(exc):
    raise exc


@pytest.mark.asyncio
async def test_slowdown_retries_then_succeeds(client, monkeypatch):
    calls = {"n": 0}

    async def flaky(method, url, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise S3HTTPError(503, url, "SlowDown")
        return 200, {"Content-Length": "12"}, b"0123456789ab"

    monkeypatch.setattr(client, "_request_once", flaky)
    status, headers, body = await client._request("GET", "https://example.test/k")
    assert status == 200 and body == b"0123456789ab"
    assert calls["n"] == 2  # exactly one retry
    assert not client.throttled


@pytest.mark.asyncio
async def test_retry_exhaustion_raises_throttled_and_opens_cooldown(client, monkeypatch):
    calls = {"n": 0}

    async def always_429(method, url, headers=None):
        calls["n"] += 1
        raise S3HTTPError(429, url, "Please reduce your request rate")

    monkeypatch.setattr(client, "_request_once", always_429)
    with pytest.raises(S3Throttled):
        await client._request("GET", "https://example.test/k")
    assert calls["n"] == settings.s3_retry_max_attempts
    assert client.throttled
    state = client.throttle_state()
    assert state["throttled"] is True and state["cooldown_remaining_seconds"] > 0

    # While cooling down, existence probes answer locally (no network).
    probes = {"n": 0}

    async def counting(method, url, headers=None):
        probes["n"] += 1
        return 200, {}, b""

    monkeypatch.setattr(client, "_request_once", counting)
    assert await client.object_exists("some/key") is False
    assert probes["n"] == 0


@pytest.mark.asyncio
async def test_404_is_terminal_never_retried(client, monkeypatch):
    calls = {"n": 0}

    async def missing(method, url, headers=None):
        calls["n"] += 1
        raise S3ObjectNotFound(url, 404)

    monkeypatch.setattr(client, "_request_once", missing)
    with pytest.raises(S3ObjectNotFound):
        await client._request("GET", "https://example.test/k")
    assert calls["n"] == 1
    assert not client.throttled


@pytest.mark.asyncio
async def test_network_blips_become_throttled(client, monkeypatch):
    async def boom(method, url, headers=None):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(client, "_request_once", boom)
    with pytest.raises(S3Throttled):
        await client._request("GET", "https://example.test/k")
    assert client.throttled


@pytest.mark.asyncio
async def test_retry_after_hint_is_respected(client, monkeypatch):
    monkeypatch.setattr(settings, "s3_retry_base_delay_seconds", 0.0)
    calls = {"n": 0}
    sleeps: list[float] = []

    async def throttled_once(method, url, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise S3HTTPError(503, url, "SlowDown", headers={"Retry-After": "1"})
        return 200, {}, b"ok"

    async def no_sleep(seconds):  # capture the computed backoff, don't wait
        sleeps.append(float(seconds))

    monkeypatch.setattr(client, "_request_once", throttled_once)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    status, _headers, body = await client._request("GET", "https://example.test/k")
    assert status == 200 and body == b"ok"
    # first wait == the server's Retry-After (1 s), not the exponential default
    assert sleeps == [pytest.approx(1.0)]


def test_cap_ttl_applied_to_sidecar_cache(tmp_path, monkeypatch):
    import sqlite3
    import time

    from diskcache import Cache

    monkeypatch.setattr(settings, "cache_max_age_hours", 1)
    cache = Cache(str(tmp_path / "c"), size_limit=10**6)
    client = S3Client(cache=cache, base_url="https://example.test")
    client._cache_set("k", b"v", ttl=999_999)  # beyond the 1 h cap

    con = sqlite3.connect(str(tmp_path / "c" / "cache.db"))
    row = con.execute("select expire_time from Cache").fetchone()
    con.close()
    assert row is not None and row[0] is not None
    assert row[0] <= time.time() + 3600 + 5  # clamped to the retention window
    cache.close()
