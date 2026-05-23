"""
Object-storage backend (S3 / MinIO / GCS-compatible)  ->  the CLOUD target.

Exactly the same three-objects-per-dataset model as the local backend, but the
objects are keys in a bucket instead of files on a disk:

    public/<dataset_id>/manifest.json
    public/<dataset_id>/expression.parquet
    private/<owner_id>/<dataset_id>/...

The point of including this class is NOT to ship production cloud storage; it is
to PROVE the interface holds across two physically different storage models. If
this class and LocalStorageBackend both satisfy StorageBackend, then the
analysis engine and API genuinely don't care where they run.

In a real deployment, server-side encryption, bucket policies, and per-tenant
IAM/prefix scoping would be configured here. Those are deployment concerns,
deliberately isolated to this one file.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pandas as pd

from .base import (
    AccessDenied,
    DatasetManifest,
    DatasetNotFound,
    StorageBackend,
    deserialize_parquet,
    serialize_parquet,
)

if TYPE_CHECKING:
    pass


class ObjectStorageBackend(StorageBackend):
    def __init__(self, bucket: str, *, endpoint_url: str | None = None,
                 region: str | None = None, client=None):
        # Lazy import so that on-prem installs don't need boto3 at all.
        if client is not None:
            self.s3 = client
        else:
            import boto3  # noqa: PLC0415

            self.s3 = boto3.client(
                "s3", endpoint_url=endpoint_url, region_name=region
            )
        self.bucket = bucket

    # --- key helpers ----------------------------------------------------------------
    def _public_prefix(self, dataset_id: str) -> str:
        return f"public/{dataset_id}/"

    def _private_prefix(self, owner: str, dataset_id: str) -> str:
        return f"private/{owner}/{dataset_id}/"

    def _get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:  # ClientError 404 from non-AWS gateways
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                return None
            raise

    def _resolve_prefix(self, dataset_id: str, owner: str | None) -> str:
        if owner is not None:
            priv = self._private_prefix(owner, dataset_id)
            if self._get_bytes(priv + "manifest.json") is not None:
                return priv
        pub = self._public_prefix(dataset_id)
        if self._get_bytes(pub + "manifest.json") is not None:
            return pub
        raise DatasetNotFound(f"Dataset '{dataset_id}' not found.")

    def _list_prefix_manifests(self, prefix: str) -> list[DatasetManifest]:
        out: list[DatasetManifest] = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("manifest.json"):
                    raw = self._get_bytes(key)
                    if raw:
                        out.append(DatasetManifest.from_dict(json.loads(raw)))
        return out

    # --- interface ------------------------------------------------------------------
    def list_datasets(self, owner: str | None = None) -> list[DatasetManifest]:
        manifests = self._list_prefix_manifests("public/")
        if owner is not None:
            manifests += self._list_prefix_manifests(f"private/{owner}/")
        return manifests

    def read_manifest(self, dataset_id: str, owner: str | None = None) -> DatasetManifest:
        prefix = self._resolve_prefix(dataset_id, owner)
        raw = self._get_bytes(prefix + "manifest.json")
        return DatasetManifest.from_dict(json.loads(raw))

    def read_expression(self, dataset_id: str, owner: str | None = None) -> pd.DataFrame:
        prefix = self._resolve_prefix(dataset_id, owner)
        raw = self._get_bytes(prefix + "expression.parquet")
        return deserialize_parquet(raw)

    def read_clinical(self, dataset_id: str, owner: str | None = None) -> pd.DataFrame:
        prefix = self._resolve_prefix(dataset_id, owner)
        raw = self._get_bytes(prefix + "clinical.parquet")
        return deserialize_parquet(raw)

    # --- write path -----------------------------------------------------------------
    def supports_write(self) -> bool:
        return True

    def write_dataset(
        self,
        dataset_id: str,
        owner: str,
        manifest: dict,
        expression: pd.DataFrame,
        clinical: pd.DataFrame,
    ) -> None:
        prefix = self._private_prefix(owner, dataset_id)
        manifest = {**manifest, "visibility": "private", "owner": owner,
                    "dataset_id": dataset_id}
        self.s3.put_object(
            Bucket=self.bucket, Key=prefix + "manifest.json",
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            # ServerSideEncryption="AES256",  # enable in production
        )
        self.s3.put_object(
            Bucket=self.bucket, Key=prefix + "expression.parquet",
            Body=serialize_parquet(expression),
        )
        self.s3.put_object(
            Bucket=self.bucket, Key=prefix + "clinical.parquet",
            Body=serialize_parquet(clinical),
        )
