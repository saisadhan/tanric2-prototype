"""
Storage factory  ->  the single swap point between deployments.

The entire cloud-vs-on-prem decision collapses to one environment variable:

    TANRIC_STORAGE=local   TANRIC_DATA_ROOT=/data
    TANRIC_STORAGE=object  TANRIC_S3_BUCKET=tanric  TANRIC_S3_ENDPOINT=...

Application code calls get_storage() and never branches on deployment type
again. This is the concrete payoff of the storage abstraction.
"""
from __future__ import annotations

import os

from .base import StorageBackend
from .local import LocalStorageBackend
from .object_store import ObjectStorageBackend


def get_storage() -> StorageBackend:
    kind = os.environ.get("TANRIC_STORAGE", "local").lower()

    if kind == "local":
        root = os.environ.get("TANRIC_DATA_ROOT", "data")
        return LocalStorageBackend(root)

    if kind == "object":
        bucket = os.environ.get("TANRIC_S3_BUCKET")
        if not bucket:
            raise RuntimeError("TANRIC_S3_BUCKET must be set for object storage.")
        return ObjectStorageBackend(
            bucket=bucket,
            endpoint_url=os.environ.get("TANRIC_S3_ENDPOINT"),
            region=os.environ.get("TANRIC_S3_REGION"),
        )

    raise RuntimeError(f"Unknown TANRIC_STORAGE='{kind}' (use 'local' or 'object').")
