"""Advisory single-owner lock for the background poller.

A production API runs several uvicorn workers over the same on-disk caches.
The cycle poller must run exactly once per machine — otherwise every worker
would duplicate HEAD traffic against NOAA and race on warm-up.  Workers claim
ownership with a non-blocking ``flock`` on a file inside the cache directory;
whoever loses keeps serving API traffic only.

If the holder dies, the OS releases the flock automatically, so a restart
recovers without stale-pidfile surgery.  On platforms without ``fcntl``
(Windows dev machines) the lock degrades to "always granted", which is the
correct single-process answer.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from app.config import settings
from app.logging_config import ensure_cache_dir, get_logger

log = get_logger(__name__)

try:  # POSIX advisory locking — the production target.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

__all__ = ["PollerLock", "acquire_poller_lock"]

LOCK_FILENAME = "poller.lock"


class PollerLock:
    """Held file lock; releases on :meth:`release` or context exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:  # read-only volume etc → grant, run anyway.
            log.warning("poller lock unavailable (%s); running unpaced", exc)
            self._fd = -1  # sentinel: "granted without flock"
            return True
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"pid={os.getpid()} poller-owner\n".encode())
        except OSError:  # pragma: no cover — cosmetic; lock is already ours
            pass
        self._fd = fd
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None or fd == -1:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover
                pass

    def __enter__(self) -> "PollerLock":
        self.try_acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def acquire_poller_lock() -> PollerLock | None:
    """Try to become the poller owner.  ``None`` when another worker holds it."""
    directory = ensure_cache_dir(settings.tile_cache_path)
    lock = PollerLock(directory / LOCK_FILENAME)
    return lock if lock.try_acquire() else None
