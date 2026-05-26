"""
Storage factory  ->  the single swap point between deployments.

The entire cloud-vs-on-prem decision collapses to one setting, TANRIC_STORAGE,
read from config (which loads it from .env or the real environment):

    TANRIC_STORAGE=local   TANRIC_DATA_ROOT=/data
    TANRIC_STORAGE=object  TANRIC_S3_BUCKET=tanric  TANRIC_S3_ENDPOINT=...

Application code calls get_storage() and never branches on deployment type
again. This is the concrete payoff of the storage abstraction.
"""
from __future__ import annotations

from .. import config
from .base import StorageBackend
from .local import LocalStorageBackend
from .object_store import ObjectStorageBackend


def get_storage() -> StorageBackend:
    kind = config.STORAGE

    if kind == "local":
        return LocalStorageBackend(config.DATA_ROOT)

    if kind == "object":
        if not config.S3_BUCKET:
            raise RuntimeError("TANRIC_S3_BUCKET must be set for object storage.")
        return ObjectStorageBackend(
            bucket=config.S3_BUCKET,
            endpoint_url=config.S3_ENDPOINT,
            region=config.S3_REGION,
        )

    raise RuntimeError(f"Unknown TANRIC_STORAGE='{kind}' (use 'local' or 'object').")
