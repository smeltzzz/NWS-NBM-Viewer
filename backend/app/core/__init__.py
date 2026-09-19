"""Core NBM data-ingestion primitives.

The modules in this package deliberately do not import FastAPI.  They can be
used by the API, a background materialiser, or a command-line ingestion job.
"""

from app.core.catalog import NBM_CATALOG
from app.core.s3_client import IdxEntry, S3Client

__all__ = ["IdxEntry", "NBM_CATALOG", "S3Client"]
