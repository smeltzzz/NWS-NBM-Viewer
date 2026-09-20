"""Anonymous, asynchronous access to the public NBM GRIB2 S3 bucket.

NBM GRIB2 objects are large.  The ingestion path therefore fetches the small
``.idx`` sidecar first and then requests only the byte range containing the
selected GRIB message.  Both sidecars and message slices are persisted in a
DiskCache so a map request does not repeatedly spend bandwidth on NOAA data.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.config import settings
from app.services.retention import cap_ttl, evict_diskcache_older_than

log = logging.getLogger(__name__)

try:  # botocore is already a runtime dependency; retain a clear unsigned config.
    from botocore import UNSIGNED
    from botocore.config import Config as BotocoreConfig

    ANONYMOUS_S3_CONFIG = BotocoreConfig(signature_version=UNSIGNED)
except ImportError:  # pragma: no cover - useful for lightweight parser-only installs
    ANONYMOUS_S3_CONFIG = None


S3_BASE_URL = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
DEFAULT_BUCKET = "noaa-nbm-grib2-pds"
DEFAULT_TIMEOUT_SECONDS = 30.0
_DATE_RE = re.compile(r"^(?P<date>\d{8})(?:[-T]?\d{2})?$")
_RANGE_RE = re.compile(r"(?P<start>\d+)\s*-\s*(?P<end>\d+)\s+hour", re.IGNORECASE)


class S3ClientError(RuntimeError):
    """Base error raised by :class:`S3Client`."""


class S3ObjectNotFound(S3ClientError):
    """The requested NBM object has not been published."""

    def __init__(self, key: str, status: int = 404) -> None:
        self.key = key
        self.status = status
        super().__init__(f"S3 object is not available ({status}): {key}")


# More descriptive name for callers performing forecast-hour fallback.
ForecastNotAvailable = S3ObjectNotFound


class S3HTTPError(S3ClientError):
    """A non-404 HTTP error from the public bucket."""

    def __init__(
        self,
        status: int,
        url: str,
        detail: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self.detail = detail
        self.headers = headers or {}
        suffix = f": {detail}" if detail else ""
        super().__init__(f"S3 HTTP {status} for {url}{suffix}")


#: Statuses that mean "the bucket is unhappy right now", not "the object is
#: missing" — S3 answers 503 SlowDown / 429 when an anonymous consumer
#: exceeds its request rate, and proxies emit 500/502/504 on NOAA hiccups.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class S3Throttled(S3HTTPError):
    """Upstream is rate-limiting or unhealthy.

    Raised once the bounded retry loop is exhausted.  Callers distinguish:

    * ``S3ObjectNotFound`` → the object is genuinely not published (normal —
      a delayed forecast hour),
    * ``S3Throttled``      → *unknown*; probes must keep the previous answer
      and the poller applies exponential backoff instead of concluding NOAA
      has nothing new.
    """

    def __init__(
        self, status: int, url: str, detail: str = "", retry_after: float | None = None
    ) -> None:
        super().__init__(status, url, detail)
        self.retry_after = retry_after


#: ``Retry-After`` is honoured as asked — up to a sane ceiling, because
#: waiting less than the server requested re-triggers the rate limit and
#: waiting forever would stall the request path.
RETRY_AFTER_CAP_SECONDS = 60.0


def _parse_retry_after(error: S3HTTPError) -> float | None:
    """Extract a sane ``Retry-After`` hint (delta-seconds only, bounded)."""
    headers = getattr(error, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after") or ""
    match = re.fullmatch(r"\s*(\d+)\s*", str(raw))
    if not match:
        return None
    return min(float(match.group(1)), RETRY_AFTER_CAP_SECONDS)


class IdxParseError(S3ClientError, ValueError):
    """A non-empty line in an index file is not valid wgrib2 inventory data."""


class IdxEntry(BaseModel):
    """One line of a GRIB2 ``.idx`` file.

    ``byte_end`` is inclusive, matching the HTTP Range header.  It is ``None``
    only when a caller parses an index without providing the GRIB object's EOF;
    :meth:`S3Client.fetch_idx` obtains that EOF with a HEAD request whenever
    NOAA permits it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_num: int = Field(ge=1)
    byte_start: int = Field(ge=0)
    byte_end: int | None = Field(default=None, ge=0)
    date: str
    variable: str
    level: str
    forecast_step: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def forecast_hour(self) -> int | None:
        """End hour parsed from an accumulation/forecast-step descriptor."""
        if not self.forecast_step:
            return None
        match = _RANGE_RE.search(self.forecast_step)
        if match:
            return int(match.group("end"))
        return None

    @property
    def byte_range(self) -> tuple[int, int] | None:
        if self.byte_end is None:
            return None
        return self.byte_start, self.byte_end


def _normalise_date(date_str: str) -> str:
    """Convert common date forms to the S3 ``YYYYMMDD`` path component."""
    value = str(date_str).strip().replace("-", "")
    match = _DATE_RE.match(value)
    if not match:
        # Accept a datetime-ish string only when its first eight characters are
        # an unambiguous YYYYMMDD value.
        if re.match(r"^\d{8}", value):
            return value[:8]
        raise ValueError("date_str must contain a YYYYMMDD date")
    return match.group("date")


def _normalise_cycle(cycle: int) -> int:
    value = int(cycle)
    if not 0 <= value <= 23:
        raise ValueError("cycle must be a UTC hour between 0 and 23")
    return value


def _normalise_hour(fhour: int) -> int:
    value = int(fhour)
    if not 0 <= value <= 999:
        raise ValueError("forecast hour must be between 0 and 999")
    return value


def build_grib_key(
    date_str: str,
    cycle: int,
    file_type: str,
    fhour: int,
    domain: str,
    *,
    template: str | None = None,
) -> str:
    """Build the configured NODD key for a GRIB2 object.

    The default NODD layout is ``blend.YYYYMMDD/CC/core|qmd/<filename>``.
    Keeping this in one function also makes alternate mirrors and unit tests
    straightforward: pass a custom ``template`` with the same format fields.
    """
    date = _normalise_date(date_str)
    cycle_value = _normalise_cycle(cycle)
    hour = _normalise_hour(fhour)
    product = str(file_type).strip().lower()
    if product not in {"core", "qmd"}:
        raise ValueError("file_type must be 'core' or 'qmd'")
    region = str(domain).strip().lower()
    if region not in {"co", "ak", "hi", "pr", "gu", "oc"}:
        raise ValueError("domain must be one of co, ak, hi, pr, gu, or oc")

    selected = template or settings.s3_grib_key_template
    # The repository's original template uses an unformatted ``{cycle}``,
    # while custom templates often use ``{cycle:02d}``.  Preserve the former's
    # leading zero without breaking integer format specifications.
    cycle_argument: int | str = cycle_value
    if "{cycle}" in selected:
        cycle_argument = f"{cycle_value:02d}"
    try:
        return selected.format(
            date=date,
            cycle=cycle_argument,
            product=product,
            file_type=product,
            hour=hour,
            domain=region,
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid S3 GRIB key template: {selected!r}") from exc


def build_idx_key(*args: Any, **kwargs: Any) -> str:
    """Build the sidecar key for a GRIB2 object."""
    return f"{build_grib_key(*args, **kwargs)}.idx"


def _parse_idx_parts(line: str, line_number: int) -> tuple[int, int, str, str, str, str | None]:
    # A normal wgrib2 inventory line is:
    #   message:offset:d=YYYYMMDDHH:SHORT_NAME:level:forecast-step:
    # Split only semantically significant positions.  Joining the remaining
    # pieces allows a level description to contain a colon in a future table.
    parts = line.rstrip("\r\n").split(":")
    while parts and not parts[-1].strip():
        parts.pop()
    if len(parts) < 5:
        raise IdxParseError(f"invalid .idx line {line_number}: {line!r}")

    try:
        message_num = int(parts[0])
        byte_start = int(parts[1])
    except ValueError as exc:
        raise IdxParseError(f"invalid message/offset on .idx line {line_number}: {line!r}") from exc

    date_field = parts[2].strip()
    if not date_field.startswith("d=") or not date_field[2:]:
        raise IdxParseError(f"missing d= timestamp on .idx line {line_number}: {line!r}")
    variable = parts[3].strip()
    level = parts[4].strip()
    if not variable or not level:
        raise IdxParseError(f"missing variable/level on .idx line {line_number}: {line!r}")
    forecast_step = ":".join(part.strip() for part in parts[5:]).strip() or None
    return message_num, byte_start, date_field[2:], variable, level, forecast_step


def calculate_byte_ranges(entries: list[IdxEntry], file_size: int | None = None) -> list[IdxEntry]:
    """Fill inclusive byte ends from the next message offset and EOF.

    ``file_size`` is a byte count, not the last byte number.  Thus the final
    message ends at ``file_size - 1``.  The function returns new frozen models
    and does not mutate its input list.
    """
    if not entries:
        return []
    ordered = list(entries)
    for current, following in zip(ordered, ordered[1:]):
        if following.byte_start <= current.byte_start:
            raise IdxParseError(".idx byte offsets must be strictly increasing")
    if file_size is not None:
        if file_size < 0:
            raise ValueError("file_size cannot be negative")
        if file_size <= ordered[-1].byte_start:
            raise IdxParseError("GRIB EOF precedes the final .idx message")

    ranged: list[IdxEntry] = []
    for index, entry in enumerate(ordered):
        if index + 1 < len(ordered):
            end = ordered[index + 1].byte_start - 1
        elif file_size is not None:
            end = file_size - 1
        else:
            end = None
        ranged.append(entry.model_copy(update={"byte_end": end}))
    return ranged


def parse_idx(index_text: str | bytes, file_size: int | None = None) -> list[IdxEntry]:
    """Parse representative NOAA ``.idx`` text into typed entries.

    Blank lines and comment lines are ignored.  The parser deliberately keeps
    the date and forecast-step tokens as strings because NBM has published
    both instantaneous and accumulation descriptors over its version history.
    ``forecast_hour`` on :class:`IdxEntry` provides a convenient numeric view
    when a ``0-N hour`` descriptor is present.
    """
    if isinstance(index_text, bytes):
        text = index_text.decode("utf-8", errors="replace")
    else:
        text = index_text

    entries: list[IdxEntry] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        message_num, byte_start, date, variable, level, forecast_step = _parse_idx_parts(
            line, line_number
        )
        entries.append(
            IdxEntry(
                message_num=message_num,
                byte_start=byte_start,
                date=date,
                variable=variable,
                level=level,
                forecast_step=forecast_step,
            )
        )
    return calculate_byte_ranges(entries, file_size=file_size)


class S3Client:
    """Async anonymous HTTP client for NBM indexes and GRIB byte ranges."""

    def __init__(
        self,
        *,
        base_url: str = S3_BASE_URL,
        bucket: str = DEFAULT_BUCKET,
        key_template: str | None = None,
        cache_dir: str | Path | None = None,
        cache: Any | None = None,
        timeout_seconds: float | None = None,
        max_connections: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = bucket
        self.key_template = key_template
        self.timeout_seconds = timeout_seconds or max(
            settings.s3_connect_timeout_seconds,
            settings.s3_read_timeout_seconds,
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.max_connections = max_connections or settings.s3_max_pool_connections
        self._session: aiohttp.ClientSession | None = None
        self._owns_cache = cache is None
        # NB: `cache or _make_cache(...)` would silently discard a passed-in
        # *empty* cache — diskcache's Cache implements __len__ and is falsy
        # with zero entries.  Compare against None explicitly.
        self._cache = cache if cache is not None else self._make_cache(cache_dir)
        # Monotonic instant until which cheap probes answer "unknown" because
        # the bucket rate-limited us (graceful-degradation back-off).
        self._throttle_until = 0.0
        # Exposed for callers/tests wanting to assert public unsigned mode. The
        # aiohttp path itself never adds Authorization headers.
        self.botocore_config = ANONYMOUS_S3_CONFIG

    @staticmethod
    def _make_cache(cache_dir: str | Path | None) -> Any:
        from diskcache import Cache

        directory = Path(cache_dir) if cache_dir is not None else settings.tile_cache_path / "s3"
        directory.mkdir(parents=True, exist_ok=True)
        return Cache(
            directory=str(directory),
            size_limit=settings.cache_size_limit_bytes,
            eviction_policy="least-recently-stored",
        )

    def _cache_key(self, namespace: str, *parts: object) -> str:
        raw = "|".join([namespace, *(str(part) for part in parts)])
        return f"nbm-s3:{namespace}:{hashlib.sha256(raw.encode()).hexdigest()}"

    def _cache_get(self, key: str) -> Any | None:
        if self._cache is None:
            return None
        try:
            return self._cache.get(key, default=None)
        except Exception:
            return None

    def _cache_set(self, key: str, value: Any, ttl: int) -> None:
        if self._cache is None:
            return
        try:
            # Bound every sidecar/fragment to the retention window so the S3
            # cache dir cannot grow without limit on 24/7 hosts.
            self._cache.set(
                key, value, expire=cap_ttl(ttl, settings.cache_max_age_seconds), retry=True
            )
        except Exception:
            # A cache outage must never make public data unavailable.
            return

    def purge_expired(self) -> dict[str, Any]:
        """Age-sweep the sidecar cache (called by the retention job)."""
        if self._cache is None:
            return {"removed": 0}
        try:
            report = evict_diskcache_older_than(self._cache, settings.cache_max_age_seconds)
            return report.as_dict()
        except Exception as exc:  # noqa: BLE001
            return {"removed": 0, "error": f"{type(exc).__name__}: {exc}"}

    def throttle_state(self) -> dict[str, Any]:
        """Snapshot of the degradation back-off, for status/health endpoints."""
        remaining = self._throttle_until - time.monotonic()
        return {
            "throttled": remaining > 0,
            "cooldown_remaining_seconds": round(max(0.0, remaining), 1),
            "retry_max_attempts": settings.s3_retry_max_attempts,
            "retry_base_delay_seconds": settings.s3_retry_base_delay_seconds,
            "retry_max_delay_seconds": settings.s3_retry_max_delay_seconds,
        }

    def grib_key(self, date_str: str, cycle: int, file_type: str, fhour: int, domain: str) -> str:
        return build_grib_key(date_str, cycle, file_type, fhour, domain, template=self.key_template)

    def idx_key(self, date_str: str, cycle: int, file_type: str, fhour: int, domain: str) -> str:
        return f"{self.grib_key(date_str, cycle, file_type, fhour, domain)}.idx"

    def object_url(self, s3_key: str) -> str:
        """Turn a key into an HTTPS URL, supporting path-style test endpoints."""
        if s3_key.startswith(("http://", "https://")):
            return s3_key
        key = s3_key.removeprefix("s3://")
        if "/" in key and key.split("/", 1)[0] == self.bucket:
            key = key.split("/", 1)[1]
        base = self.base_url
        host_has_bucket = base.endswith(f"/{self.bucket}") or f"{self.bucket}.s3." in base
        prefix = "" if host_has_bucket else f"/{self.bucket}"
        return f"{base}{prefix}/{quote(key, safe='/._-')}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            connector = aiohttp.TCPConnector(limit=self.max_connections, ssl=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    # ── Throttle state ──────────────────────────────────────────────────────
    @property
    def throttled(self) -> bool:
        """True while a transient-outage cooldown is active."""
        return time.monotonic() < self._throttle_until

    def _note_throttle(self, seconds: float | None = None) -> None:
        cooldown = seconds if seconds is not None else settings.s3_throttle_cooldown_seconds
        self._throttle_until = max(self._throttle_until, time.monotonic() + cooldown)

    async def _request(
        self, method: str, url: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        """One S3 request with bounded, jittered retries for transient errors.

        * ``404``        → :class:`S3ObjectNotFound`, immediately (terminal).
        * ``429/500/...``→ retried up to ``S3_RETRY_MAX_ATTEMPTS`` with
          exponential backoff honouring ``Retry-After``; when exhausted,
          :class:`S3Throttled` opens a cooldown so cheap probes answer
          "unknown" instead of hammering the bucket (graceful degradation for
          NODD rate limits).
        * network errors → same path as retryable statuses.
        """
        attempts = max(1, int(settings.s3_retry_max_attempts))
        delay = max(0.0, float(settings.s3_retry_base_delay_seconds))
        max_delay = max(delay, float(settings.s3_retry_max_delay_seconds))

        for attempt in range(attempts):
            try:
                return await self._request_once(method, url, headers)
            except S3ObjectNotFound:
                raise
            except S3HTTPError as exc:
                if exc.status not in RETRYABLE_STATUSES:
                    raise
                if attempt + 1 >= attempts:
                    self._note_throttle()
                    raise S3Throttled(
                        exc.status, exc.url, exc.detail, retry_after=_parse_retry_after(exc)
                    ) from exc
                wait = _parse_retry_after(exc)
                log.warning(
                    "S3 %s %s → HTTP %s; retry %d/%d in %.2fs",
                    method,
                    url.rsplit("/", 1)[-1],
                    exc.status,
                    attempt + 1,
                    attempts - 1,
                    wait if wait is not None else -1,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt + 1 >= attempts:
                    self._note_throttle()
                    raise S3Throttled(0, url, f"{type(exc).__name__}: {exc}") from exc
                wait = None
                log.warning(
                    "S3 %s %s → %s; retry %d/%d",
                    method,
                    url.rsplit("/", 1)[-1],
                    type(exc).__name__,
                    attempt + 1,
                    attempts - 1,
                )
            if wait is None:
                wait = min(max_delay, delay * (2**attempt)) + random.uniform(0, delay)
            await asyncio.sleep(max(0.0, float(wait)))

        # Unreachable: the loop raises or returns on every path.
        raise S3Throttled(0, url, "retry loop exhausted")

    async def _request_once(
        self, method: str, url: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        session = await self._get_session()
        async with session.request(method, url, headers=headers or {}) as response:
            body = await response.read()
            response_headers = {str(key): str(value) for key, value in response.headers.items()}
            if response.status == 404:
                raise S3ObjectNotFound(url, response.status)
            if response.status < 200 or response.status >= 300:
                detail = body[:200].decode("utf-8", errors="replace")
                raise S3HTTPError(response.status, url, detail, headers=response_headers)
            return response.status, response_headers, body

    async def _head_size(self, s3_key: str) -> int | None:
        url = self.object_url(s3_key)
        try:
            _status, headers, _body = await self._request("HEAD", url)
        except S3ObjectNotFound:
            return None
        except S3HTTPError:
            # Some object stores allow GET but reject HEAD.  A one-byte range
            # request still exposes the total in Content-Range and avoids
            # downloading the GRIB object merely to determine its EOF.
            try:
                _status, range_headers, _body = await self._request(
                    "GET", url, headers={"Range": "bytes=0-0"}
                )
                content_range = range_headers.get("Content-Range") or range_headers.get(
                    "content-range", ""
                )
                match = re.search(r"/([0-9]+)$", content_range)
                if match:
                    return int(match.group(1))
            except (S3ClientError, aiohttp.ClientError):
                pass
            return None
        value = headers.get("Content-Length") or headers.get("content-length")
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    async def object_exists(self, s3_key: str) -> bool:
        """Return false for a not-yet-posted NOAA object (including 404).

        Semantics engineered for 24/7 polling:

        * ``404``        → definitively "not published" → ``False``;
        * active throttle cooldown → cheap existence probes answer ``False``
          immediately without touching the network (the poller tracks
          throttles separately via :class:`S3Throttled` on real fetches);
        * exhausted retries on a *request the caller needs* → ``S3Throttled``
          propagates so discovery can mark the tick degraded instead of
          mistaking rate-limits for a silent NOAA.
        """
        if self.throttled:
            return False
        try:
            url = self.object_url(s3_key)
            await self._request("HEAD", url)
            return True
        except S3ObjectNotFound:
            return False
        except S3Throttled:
            raise
        except S3HTTPError:
            return False

    async def fetch_idx(
        self, date_str: str, cycle: int, file_type: str, fhour: int, domain: str
    ) -> list[IdxEntry]:
        """Download, cache, and parse one NBM ``.idx`` sidecar."""
        grib_key = self.grib_key(date_str, cycle, file_type, fhour, domain)
        idx_key = f"{grib_key}.idx"
        idx_url = self.object_url(idx_key)
        idx_cache_key = self._cache_key("idx", self.base_url, self.bucket, idx_key)
        idx_bytes = self._cache_get(idx_cache_key)
        if idx_bytes is None:
            _status, _headers, idx_bytes = await self._request("GET", idx_url)
            self._cache_set(idx_cache_key, idx_bytes, settings.inventory_cache_ttl_seconds)

        size_cache_key = self._cache_key("size", self.base_url, self.bucket, grib_key)
        file_size = self._cache_get(size_cache_key)
        if file_size is None:
            file_size = await self._head_size(grib_key)
            if file_size is not None:
                self._cache_set(size_cache_key, file_size, settings.inventory_cache_ttl_seconds)
        return parse_idx(idx_bytes, file_size=file_size)

    async def fetch_grib_message(self, s3_key: str, byte_start: int, byte_end: int) -> bytes:
        """Fetch exactly one inclusive GRIB message byte range."""
        start = int(byte_start)
        end = int(byte_end)
        if start < 0 or end < start:
            raise ValueError("invalid inclusive GRIB byte range")
        cache_key = self._cache_key("range", self.base_url, self.bucket, s3_key, start, end)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return bytes(cached)

        expected_length = end - start + 1
        url = self.object_url(s3_key)
        status, _headers, body = await self._request(
            "GET", url, headers={"Range": f"bytes={start}-{end}"}
        )
        if len(body) != expected_length:
            # A compliant S3 range response is 206.  Treat a proxy that strips
            # Range as an error instead of silently handing a decoder a 200MB
            # object or the wrong part of it.
            raise S3HTTPError(
                status,
                url,
                f"Range response contained {len(body)} bytes; expected {expected_length}",
            )
        self._cache_set(cache_key, body, settings.tile_cache_ttl_seconds)
        return body

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        if self._owns_cache and self._cache is not None:
            close = getattr(self._cache, "close", None)
            if close is not None:
                close()

    async def __aenter__(self) -> "S3Client":
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        await self.close()


__all__ = [
    "ANONYMOUS_S3_CONFIG",
    "DEFAULT_BUCKET",
    "ForecastNotAvailable",
    "IdxEntry",
    "IdxParseError",
    "RETRYABLE_STATUSES",
    "S3_BASE_URL",
    "S3Client",
    "S3ClientError",
    "S3HTTPError",
    "S3ObjectNotFound",
    "S3Throttled",
    "build_grib_key",
    "build_idx_key",
    "calculate_byte_ranges",
    "parse_idx",
]
