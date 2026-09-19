"""Structured logging and disk-cache wiring."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.config import settings

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once, with a compact, container-friendly format."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved = (level or settings.log_level).upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    # Uvicorn keeps its own handlers; align them with ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # These libraries are extremely chatty at INFO/DEBUG.
    for noisy in ("botocore", "boto3", "s3transfer", "urllib3", "rasterio", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, getattr(logging, resolved)))

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def ensure_cache_dir(path: Path | None = None) -> Path:
    """Create the tile cache directory (idempotent) and return it."""
    target = path or settings.tile_cache_path
    target.mkdir(parents=True, exist_ok=True)
    return target
