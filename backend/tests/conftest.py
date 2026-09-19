"""Shared fixtures for the backend test suite.

Dependencies are all explicitly enabled/discovered; nothing here needs a live
NOAA connection.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Async backend: asyncio with trio's structured-concurrency utilities."""
    return "asyncio"
