"""
Storage abstraction layer.

THIS MODULE IS THE SPINE OF THE PROTOTYPE.

The single most important architectural decision for TANRIC 2.0 is to stop
letting analysis code know *where* data physically lives. Every dataset is a
small bundle of three objects (manifest.json, expression.parquet,
clinical.parquet). A `StorageBackend` knows how to list datasets and read those
objects. Nothing else in the system does.

Consequences of this one decision:
  - On-prem deployment   -> LocalStorageBackend (a directory on disk)
  - Cloud deployment      -> ObjectStorageBackend (S3 / MinIO / GCS-compatible)
  - Private user uploads  -> just another namespace handed to the same backend
  - Maintainability        -> analysis + API are written once, against this interface

To add a new deployment target (e.g. Azure Blob) you implement ONE class with
four methods. No analysis code changes. That is the property the faculty asked
for: "evolve it without rewriting everything."
"""
from __future__ import annotations

import abc
import io
import json
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    name: str
    source: str
    cancer_type: str
    n_samples: int
    n_genes: int
    visibility: str  # "public" or "private"
    owner: str | None = None  # tenant id for private datasets; None for public

    @classmethod
    def from_dict(cls, d: dict) -> "DatasetManifest":
        return cls(
            dataset_id=d["dataset_id"],
            name=d["name"],
            source=d.get("source", "unknown"),
            cancer_type=d.get("cancer_type", "unknown"),
            n_samples=int(d.get("n_samples", 0)),
            n_genes=int(d.get("n_genes", 0)),
            visibility=d.get("visibility", "public"),
            owner=d.get("owner"),
        )


class StorageBackend(abc.ABC):
    """Abstract interface every storage implementation must satisfy.

    The contract is intentionally tiny: list datasets, read a manifest, read the
    expression matrix, read the clinical table. Read-only for public data; the
    write path for uploads is a separate, explicit method so that read-only
    backends can refuse it.
    """

    @abc.abstractmethod
    def list_datasets(self, owner: str | None = None) -> list[DatasetManifest]:
        """Return manifests visible to `owner`.

        owner=None  -> public datasets only.
        owner="x"   -> public datasets PLUS datasets owned by tenant x.
        This single rule is what enforces multi-tenant isolation (Direction 2).
        """

    @abc.abstractmethod
    def read_manifest(self, dataset_id: str, owner: str | None = None) -> DatasetManifest:
        ...

    @abc.abstractmethod
    def read_expression(self, dataset_id: str, owner: str | None = None) -> pd.DataFrame:
        """Genes (index) x samples (columns), log2(RPKM+0.001)."""

    @abc.abstractmethod
    def read_clinical(self, dataset_id: str, owner: str | None = None) -> pd.DataFrame:
        """One row per sample_id (index): group, survival_months, event_observed."""

    # Optional write path. Backends that support uploads override this.
    def supports_write(self) -> bool:
        return False

    def write_dataset(
        self,
        dataset_id: str,
        owner: str,
        manifest: dict,
        expression: pd.DataFrame,
        clinical: pd.DataFrame,
    ) -> None:
        raise NotImplementedError(
            f"{type(self).__name__} is read-only and does not support uploads."
        )


class DatasetNotFound(Exception):
    pass


class AccessDenied(Exception):
    """Raised when a tenant tries to reach a dataset they do not own."""


# Helpers shared by concrete backends -------------------------------------------------

def serialize_parquet(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def deserialize_parquet(raw: bytes) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(raw))


def parse_manifest_bytes(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))
