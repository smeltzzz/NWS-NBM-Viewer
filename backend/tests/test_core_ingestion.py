"""Unit tests for index parsing, range math, and delayed NOAA publications."""

from __future__ import annotations

import pytest

from app.api.endpoints.runs import _available_hours
from app.core.s3_client import S3Client, calculate_byte_ranges, parse_idx

REPRESENTATIVE_IDX = """\
1:0:d=2025010112:TMP:2 m above ground:
2:1048576:d=2025010112:DPT:2 m above ground:
3:2097152:d=2025010112:APCP:surface:0-6 hour acc fcst:
4:3145728:d=2025010112:GUST:10 m above ground:
"""


def test_idx_parser_extracts_fields_and_inclusive_ranges() -> None:
    entries = parse_idx(REPRESENTATIVE_IDX, file_size=4_000_000)

    assert len(entries) == 4
    assert entries[0].message_num == 1
    assert entries[0].byte_start == 0
    assert entries[0].byte_end == 1_048_575
    assert entries[0].date == "2025010112"
    assert entries[0].variable == "TMP"
    assert entries[0].level == "2 m above ground"
    assert entries[2].forecast_step == "0-6 hour acc fcst"
    assert entries[2].forecast_hour == 6
    assert entries[-1].byte_end == 3_999_999


def test_byte_range_calculation_uses_next_offset_minus_one() -> None:
    entries = parse_idx(REPRESENTATIVE_IDX)
    ranged = calculate_byte_ranges(entries, file_size=3_500_000)

    assert [(entry.byte_start, entry.byte_end) for entry in ranged] == [
        (0, 1_048_575),
        (1_048_576, 2_097_151),
        (2_097_152, 3_145_727),
        (3_145_728, 3_499_999),
    ]


@pytest.mark.asyncio
async def test_fetch_idx_and_message_use_cache_and_exact_range(tmp_path) -> None:
    client = S3Client(cache_dir=tmp_path, base_url="https://example.test")
    requests: list[tuple[str, str, dict[str, str]]] = []

    async def fake_request(method: str, url: str, headers: dict[str, str] | None = None):
        requests.append((method, url, headers or {}))
        if method == "HEAD":
            return 200, {"Content-Length": "4000000"}, b""
        if url.endswith(".idx"):
            return 200, {}, REPRESENTATIVE_IDX.encode()
        return 206, {}, b"G" * 8

    client._request = fake_request  # type: ignore[method-assign]
    entries = await client.fetch_idx("2025-01-01", 12, "core", 6, "co")
    assert entries[-1].byte_end == 3_999_999

    body = await client.fetch_grib_message(
        "blend.grib2", entries[0].byte_start, entries[0].byte_start + 7
    )
    assert body == b"G" * 8
    assert requests[-1][2]["Range"] == "bytes=0-7"

    # The second read is served by DiskCache and does not create another GET.
    before = len(requests)
    assert await client.fetch_grib_message("blend.grib2", 0, 7) == b"G" * 8
    assert len(requests) == before
    await client.close()


class DelayedForecastClient:
    """Minimal object-existence adapter representing NOAA's partial upload."""

    def idx_key(self, date: str, cycle: int, product: str, hour: int, domain: str) -> str:
        return f"{date}/{cycle}/{product}/{hour:03d}/{domain}.idx"

    async def object_exists(self, key: str) -> bool:
        # f001 exists, but f003 has not reached the public bucket yet.
        return "/001/" in key or ("/006/" in key and "/003/" not in key)


@pytest.mark.asyncio
async def test_missing_forecast_step_is_omitted_not_fatal() -> None:
    hours = await _available_hours(DelayedForecastClient(), "20250101", 12, "core", "co")
    assert 1 in hours
    assert 3 not in hours
    assert 6 in hours
