"""In-memory GRIB2 decode / encode for the tiling pipeline.

The tile renderer needs one GRIB2 *message* — not a whole product — and it
needs it without ever touching the filesystem.  Both directions here run
entirely through :class:`rasterio.io.MemoryFile`:

* :func:`decode_message` hands the byte range straight to GDAL's GRIB driver
  and returns a :class:`Grid` (values + geotransform + CRS).
* :func:`encode_message` is the inverse.  It exists so the synthetic data
  source and the test-suite can produce *real* GRIB2 bytes that travel the
  identical code path as a NOAA byte range — no test doubles, no fakes.

Everything is single-band: NBM publishes one field per message, and the tile
endpoint asks for exactly one.

Note on the codec: GDAL's GRIB driver is used rather than a hand-rolled
section parser.  It is what production already links against, it handles the
packing templates NBM actually ships (simple, complex with spatial
differencing, JPEG2000, PNG) and it is the only option in environments where
the optional ``eccodes`` Python bindings are absent — see ``requirements.txt``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import Iterator

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.transform import Affine

from app.logging_config import get_logger

__all__ = [
    "GribDecodeError",
    "Grid",
    "decode_message",
    "encode_message",
    "iter_messages",
    "magic",
]

log = get_logger(__name__)

#: GRIB2 edition marker in the indicator section.
GRIB_MAGIC = b"GRIB"
END_MAGIC = b"7777"
INDICATOR_LENGTH = 16


class GribDecodeError(RuntimeError):
    """The payload is not a decodable GRIB2 message."""


@dataclass(frozen=True)
class Grid:
    """A decoded single-field raster plus everything needed to reproject it."""

    values: np.ndarray
    transform: Affine
    crs: CRS
    nodata: float | None = None
    #: Free-form descriptors carried through from the GRIB product section.
    parameter: str = ""
    units: str = ""
    level: str = ""
    step: str = ""
    #: Byte length of the source message; useful for cache budgeting.
    source_bytes: int = 0

    @property
    def height(self) -> int:
        return int(self.values.shape[0])

    @property
    def width(self) -> int:
        return int(self.values.shape[1])

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(left, bottom, right, top)`` in the native CRS."""
        return rasterio.transform.array_bounds(
            self.height, self.width, self.transform
        )

    @property
    def nbytes(self) -> int:
        return int(self.values.nbytes)

    def with_values(self, values: np.ndarray) -> "Grid":
        """Copy with a new data array (same georeferencing)."""
        return replace(self, values=values)

    def mask_invalid(self) -> np.ndarray:
        """Boolean array that is ``True`` where the field carries no data."""
        invalid = ~np.isfinite(self.values)
        if self.nodata is not None and np.isfinite(self.nodata):
            invalid |= self.values == np.float32(self.nodata)
        return invalid


def magic(payload: bytes) -> bytes:
    """First four bytes of a payload — cheap sanity check before decoding."""
    return bytes(payload[:4])


def iter_messages(payload: bytes) -> Iterator[bytes]:
    """Split a buffer into individual GRIB2 messages.

    A byte range normally contains exactly one message; this handles the case
    where NOAA packs several and an ``.idx`` range spans them.
    """
    offset = 0
    total = len(payload)
    while offset < total:
        if payload[offset : offset + 4] != GRIB_MAGIC:
            raise GribDecodeError(
                f"expected {GRIB_MAGIC!r} at offset {offset}, found "
                f"{payload[offset : offset + 4]!r}"
            )
        if offset + INDICATOR_LENGTH > total:
            raise GribDecodeError("truncated GRIB2 indicator section")
        (length,) = struct.unpack_from(">Q", payload, offset + 8)
        if length < INDICATOR_LENGTH or offset + length > total:
            raise GribDecodeError(
                f"GRIB2 message declares {length} bytes; only "
                f"{total - offset} available at offset {offset}"
            )
        yield payload[offset : offset + length]
        offset += length


def decode_message(payload: bytes, *, band: int = 1) -> Grid:
    """Decode one GRIB2 message entirely in memory.

    Parameters
    ----------
    payload
        The raw bytes of a single GRIB2 message — exactly what an S3
        ``Range`` request for one ``.idx`` entry returns.
    band
        Band index (1-based) when the message carries more than one field.

    Raises
    ------
    GribDecodeError
        If the payload is not GRIB2, is truncated, or GDAL cannot read it.
    """
    if magic(payload) != GRIB_MAGIC:
        raise GribDecodeError(
            f"payload does not start with {GRIB_MAGIC!r} (got {magic(payload)!r})"
        )

    try:
        with MemoryFile(payload) as memfile:
            with memfile.open(driver="GRIB") as src:
                if src.count < 1:
                    raise GribDecodeError("GRIB2 message contains no bands")
                if band > src.count:
                    raise GribDecodeError(
                        f"requested band {band} of {src.count} in GRIB2 message"
                    )
                values = src.read(band)
                tags = src.tags(band)
                profile_crs = src.crs
                transform = src.transform
                nodata = src.nodata
    except GribDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001 — GDAL raises many types
        raise GribDecodeError(f"GDAL could not decode the GRIB2 message: {exc}") from exc

    if profile_crs is None:
        raise GribDecodeError("GRIB2 message carries no coordinate reference system")

    return Grid(
        values=np.ascontiguousarray(values, dtype=np.float32),
        transform=transform,
        crs=profile_crs,
        nodata=float(nodata) if nodata is not None else None,
        parameter=tags.get("GRIB_ELEMENT", "") or tags.get("GRIB_SHORT_NAME", ""),
        units=tags.get("GRIB_UNIT", ""),
        level=tags.get("GRIB_COMMENT", ""),
        step=tags.get("GRIB_FORECAST_SECONDS", ""),
        source_bytes=len(payload),
    )


def encode_message(
    values: np.ndarray,
    *,
    crs: CRS | str | int,
    transform: Affine,
    nodata: float | None = None,
    dtype: str = "float32",
    parameter: str | None = None,
) -> bytes:
    """Encode a 2-D array as a single-message GRIB2 buffer, in memory.

    Used by the synthetic data source and the test-suite.  The output is a
    genuine GRIB2 file: :func:`decode_message` reads it back bit-for-bit.
    """
    array = np.ascontiguousarray(values, dtype=dtype)
    if array.ndim != 2:
        raise ValueError(f"expected a 2-D array, got shape {array.shape}")

    resolved_crs = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GRIB",
            height=array.shape[0],
            width=array.shape[1],
            count=1,
            dtype=dtype,
            crs=resolved_crs,
            transform=transform,
            nodata=nodata,
        ) as dst:
            dst.write(array, 1)
            if parameter:
                dst.update_tags(1, GRIB_ELEMENT=parameter)
        return memfile.read()
