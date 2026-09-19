"""
Typed application settings for the NWS NBM Viewer backend.

Every value can be supplied through the environment or the repository-root
``.env`` file (see ``.env.example``). Defaults are production-safe: anonymous
NODD access, no AWS credentials, conservative timeouts.

    from app.config import settings
    settings.s3_bucket_grib   # -> "noaa-nbm-grib2-pds"
    settings.cors_origin_list # -> ["http://localhost:3000", ...]

List-like settings are declared as comma-separated *strings* and surfaced
through typed properties. This keeps them unambiguous in shells, Docker
Compose and `.env` files (no JSON quoting required).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/app/config.py -> backend/app -> backend -> <root>
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

Environment = Literal["development", "staging", "production", "test"]
NbmProduct = Literal["core", "qmd"]
NbmDomain = Literal["co", "ak", "hi", "pr", "gu", "oc"]
Resampling = Literal[
    "nearest", "bilinear", "cubic", "cubic_spline", "lanczos", "average", "mode"
]


def _split_csv(value: str) -> list[str]:
    """'a, b ,c' -> ['a', 'b', 'c']; tolerant of JSON arrays and empties."""
    if not value:
        return []
    cleaned = value.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    return [item.strip().strip("\"'") for item in cleaned.split(",") if item.strip()]


class Settings(BaseSettings):
    """Single source of truth for backend configuration."""

    model_config = SettingsConfigDict(
        # Look for `.env` in the repo root first, then in ./backend.
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    app_name: str = "NWS NBM Viewer API"
    app_description: str = (
        "Real-time tile and metadata API for the NOAA/NWS National Blend of "
        "Models (NBM v4.2+): deterministic elements, probabilistic percentiles "
        "and exceedance thresholds."
    )
    app_version: str = "0.1.0"
    environment: Environment = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    web_concurrency: int = Field(default=1, ge=1, le=64)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    api_prefix: str = "/api/v1"
    debug: bool = False

    # ── CORS ───────────────────────────────────────────────────────────────
    # Comma-separated exact origins for cross-origin frontend deployments.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Regex covering ephemeral preview hosts (Arena/E2B/Vercel/Cloudflare).
    cors_origin_regex: str = (
        r"^https?://([a-z0-9-]+\.)*(localhost|e2b\.app|arena\.ai|vercel\.app|pages\.dev)(:\d+)?$"
    )

    # ── NOAA NODD / AWS S3 ─────────────────────────────────────────────────
    aws_default_region: str = "us-east-1"
    s3_bucket_grib: str = "noaa-nbm-grib2-pds"
    s3_bucket_cog: str = "noaa-nbm-pds"
    s3_endpoint_url: str | None = None
    s3_verify_tls: bool = True
    s3_use_signature_v4: bool = False
    s3_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    s3_read_timeout_seconds: float = Field(default=30.0, gt=0)
    s3_max_pool_connections: int = Field(default=32, ge=1)

    # NODD/NOMADS expose `{product}/blend.t{cycle}z.{product}.f{hhh}.{domain}.grib2`.
    # NODD additionally publishes a flat `grib2/` prefix; the fallback template
    # is probed automatically when the primary layout returns 404.
    s3_grib_key_template: str = (
        "blend.{date}/{cycle}/{product}/blend.t{cycle}z.{product}.f{hour:03d}.{domain}.grib2"
    )
    s3_grib_key_template_fallback: str = (
        "blend.{date}/{cycle}/grib2/blend.t{cycle}z.{product}.f{hour:03d}.{domain}.grib2"
    )
    cog_key_template: str = (
        "blendv4.2/{domain}/{date}/{cycle}/{variable}/{variable}.t{cycle}z.f{hour:03d}.{domain}.tif"
    )

    # ── NOAA NOMADS HTTP fallback ──────────────────────────────────────────
    nomads_base_url: str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"
    nomads_fallback_enabled: bool = True
    upstream_timeout_seconds: float = Field(default=20.0, gt=0)
    upstream_probe_timeout_seconds: float = Field(default=2.5, gt=0)
    upstream_probe_enabled: bool = True
    upstream_probe_cache_seconds: int = Field(default=60, ge=0)

    # ── Caching ────────────────────────────────────────────────────────────
    tile_cache_dir: Path = Path("./data/tilecache")
    cache_size_limit_gb: float = Field(default=20.0, gt=0)
    tile_cache_ttl_seconds: int = Field(default=3600, ge=0)
    #: Layer-2 lifetime for tiles of the *latest* cycle.  NBM re-publishes the
    #: live cycle as messages arrive, so it is held no longer than the
    #: ``Cache-Control`` we advertise to clients.
    tile_cache_ttl_latest_seconds: int = Field(default=300, ge=0)
    inventory_cache_ttl_seconds: int = Field(default=300, ge=0)
    cache_backend: Literal["disk", "redis"] = "disk"
    redis_url: str = "redis://redis:6379/0"

    # ── NBM model defaults ─────────────────────────────────────────────────
    nbm_default_domain: NbmDomain = "co"
    nbm_default_product: NbmProduct = "core"
    nbm_default_variable: str = "tmp"
    nbm_default_forecast_hour: int = Field(default=24, ge=0, le=264)
    nbm_max_forecast_hour: int = Field(default=264, ge=1, le=264)
    nbm_default_percentile: int = Field(default=50, ge=1, le=99)

    # ── Tiling / rendering ─────────────────────────────────────────────────
    tile_min_zoom: int = Field(default=0, ge=0, le=22)
    tile_max_zoom: int = Field(default=12, ge=1, le=22)
    tile_size: int = 256
    tile_resampling: Resampling = "bilinear"
    colormap_mode: Literal["server", "raw"] = "server"
    colormaps_default: str = "nbm_temp,nbm_precip,nbm_wind,nbm_rh,nbm_pop"

    # ── Raster tiling pipeline (/backend/app/tiles) ────────────────────────
    # Layer-1 LRU budget for *decoded* GRIB grids, in MiB.  A CONUS float32
    # field is ~9 MB, so 512 MiB holds a few dozen variables/forecast hours.
    tile_grid_cache_mb: int = Field(default=512, ge=16, le=8192)
    tile_grid_cache_entries: int = Field(default=128, ge=1)

    # Layer-2 HTTP cache policy.  A published cycle never changes, so its
    # tiles are immutable; the newest cycle may still gain forecast hours.
    tile_cache_max_age_historical: int = Field(default=86400, ge=0)
    tile_cache_max_age_latest: int = Field(default=300, ge=0)
    latest_cycle_tolerance_hours: int = Field(default=3, ge=0, le=48)

    # WebP encoder.  Lossy at q=82 is ~30% smaller than PNG at equal quality;
    # `method` trades encode CPU for size (0=fast, 6=best).
    tile_webp_quality: int = Field(default=82, ge=0, le=100)
    tile_webp_lossless: bool = False
    tile_webp_method: int = Field(default=4, ge=0, le=6)
    tile_warp_threads: int = Field(default=2, ge=1, le=16)
    tile_smooth_radius: int = Field(default=1, ge=0, le=4)
    tile_empty_response: Literal["no_content", "image"] = "no_content"

    # Which data source feeds the tiler: s3 | local | synthetic | auto.
    tile_data_source: Literal["s3", "local", "synthetic", "auto"] = "auto"
    tile_grib_dir: Path = Path("./data/grib")
    # Sampling factor for the synthetic CONUS/AK grids.  1 reproduces the real
    # NBM extent (~2100x1100 at 2.5 km); 4 keeps the test-suite snappy.
    tile_synthetic_downsample: int = Field(default=4, ge=1, le=16)
    # Force the synthetic source without touching tile_data_source (CI/offline).
    nbm_tile_offline: bool = False

    # ── Scheduler ──────────────────────────────────────────────────────────
    scheduler_enabled: bool = False
    inventory_refresh_minutes: int = Field(default=15, ge=1)
    cache_warmup_on_start: bool = False

    # ── Validators ─────────────────────────────────────────────────────────
    @field_validator("api_prefix")
    @classmethod
    def _normalise_prefix(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/")

    @field_validator("tile_cache_dir", "tile_grib_dir", mode="before")
    @classmethod
    def _expand_cache_dir(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator("tile_size")
    @classmethod
    def _validate_tile_size(cls, value: int) -> int:
        if value not in (256, 512):
            raise ValueError("tile_size must be 256 or 512")
        return value

    @field_validator("s3_endpoint_url", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Empty strings from compose/.env mean 'not configured'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # ── Derived values ─────────────────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def docs_enabled(self) -> bool:
        """OpenAPI/Swagger is disabled in production."""
        return not self.is_production

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tile_cache_path(self) -> Path:
        """Absolute, resolved cache directory (created during startup)."""
        path = self.tile_cache_dir
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        return path

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache_size_limit_bytes(self) -> int:
        return int(self.cache_size_limit_gb * 1024**3)

    @property
    def cors_origin_list(self) -> list[str]:
        return _split_csv(self.cors_origins)

    @property
    def colormap_list(self) -> list[str]:
        return _split_csv(self.colormaps_default)

    @property
    def s3_grib_uri(self) -> str:
        return f"s3://{self.s3_bucket_grib}"

    @property
    def s3_cog_uri(self) -> str:
        return f"s3://{self.s3_bucket_cog}"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — safe to use as a FastAPI dependency."""
    return Settings()


settings = get_settings()
