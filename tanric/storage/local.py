"""
Local filesystem storage backend  ->  the ON-PREM deployment target.

Datasets live under a root directory:

    <root>/
        public/
            tcga-brca/{manifest.json,expression.parquet,clinical.parquet}
            ...
        private/
            <owner_id>/
                <dataset_id>/{manifest.json,expression.parquet,clinical.parquet}

A research group running TANRIC 2.0 inside their own firewall points this at a
mounted volume and they are done. No cloud account, no external dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .base import (
    AccessDenied,
    DatasetManifest,
    DatasetNotFound,
    StorageBackend,
)


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "public").mkdir(parents=True, exist_ok=True)
        (self.root / "private").mkdir(parents=True, exist_ok=True)

    # --- path resolution ------------------------------------------------------------
    def _public_dir(self, dataset_id: str) -> Path:
        return self.root / "public" / dataset_id

    def _private_dir(self, owner: str, dataset_id: str) -> Path:
        return self.root / "private" / owner / dataset_id

    def _resolve(self, dataset_id: str, owner: str | None) -> Path:
        """Find a dataset, enforcing isolation.

        Look in the caller's private space first, then fall back to public.
        A caller can NEVER reach another tenant's private directory because we
        only ever build a private path from *their own* owner id.
        """
        if owner is not None:
            p = self._private_dir(owner, dataset_id)
            if p.exists():
                return p
        p = self._public_dir(dataset_id)
        if p.exists():
            return p
        # Existence-hiding policy:
        #   - Anonymous callers (owner is None) get a plain 404. We never reveal
        #     that a private dataset exists to an unauthenticated client.
        #   - Authenticated callers who hit *another* tenant's dataset get 403,
        #     which is safe because they have already proven an identity and the
        #     audit log can attribute the access attempt.
        if owner is not None:
            private_root = self.root / "private"
            if private_root.exists():
                for other in private_root.iterdir():
                    if (other.is_dir() and other.name != owner
                            and (other / dataset_id).exists()):
                        raise AccessDenied(
                            f"Dataset '{dataset_id}' belongs to another tenant."
                        )
        raise DatasetNotFound(f"Dataset '{dataset_id}' not found.")

    # --- interface ------------------------------------------------------------------
    def list_datasets(self, owner: str | None = None) -> list[DatasetManifest]:
        manifests: list[DatasetManifest] = []

        pub = self.root / "public"
        if pub.exists():
            for d in sorted(pub.iterdir()):
                mf = d / "manifest.json"
                if mf.exists():
                    manifests.append(DatasetManifest.from_dict(json.loads(mf.read_text())))

        if owner is not None:
            owner_dir = self.root / "private" / owner
            if owner_dir.exists():
                for d in sorted(owner_dir.iterdir()):
                    mf = d / "manifest.json"
                    if mf.exists():
                        manifests.append(
                            DatasetManifest.from_dict(json.loads(mf.read_text()))
                        )
        return manifests

    def read_manifest(self, dataset_id: str, owner: str | None = None) -> DatasetManifest:
        d = self._resolve(dataset_id, owner)
        return DatasetManifest.from_dict(json.loads((d / "manifest.json").read_text()))

    def read_expression(self, dataset_id: str, owner: str | None = None) -> pd.DataFrame:
        d = self._resolve(dataset_id, owner)
        return pd.read_parquet(d / "expression.parquet")

    def read_clinical(self, dataset_id: str, owner: str | None = None) -> pd.DataFrame:
        d = self._resolve(dataset_id, owner)
        return pd.read_parquet(d / "clinical.parquet")

    # --- write path (uploads) -------------------------------------------------------
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
        target = self._private_dir(owner, dataset_id)
        target.mkdir(parents=True, exist_ok=True)
        manifest = {**manifest, "visibility": "private", "owner": owner,
                    "dataset_id": dataset_id}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
        expression.to_parquet(target / "expression.parquet")
        clinical.to_parquet(target / "clinical.parquet")
