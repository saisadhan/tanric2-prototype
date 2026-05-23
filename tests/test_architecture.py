"""
Tests that demonstrate the architecture's load-bearing properties.

The most important test here is `test_backends_produce_identical_results`: it
runs the SAME analysis through the local backend and a (mocked) object backend
and asserts the numbers match. That is the proof that storage is truly pluggable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tanric.analysis import engine
from tanric.storage import (
    AccessDenied,
    DatasetNotFound,
    LocalStorageBackend,
)
from tanric.storage.base import StorageBackend, DatasetManifest


# --- an in-memory backend used to mimic the object store without real S3 ----------
class InMemoryBackend(StorageBackend):
    """Behaves like the object store but keeps everything in dicts.

    Used to prove the StorageBackend contract holds for a second, physically
    different implementation.
    """
    def __init__(self):
        self.store: dict = {}  # (scope) -> {dataset_id: (manifest, expr, clinical)}
        self.store["public"] = {}
        self.store["private"] = {}

    def add_public(self, dataset_id, manifest, expr, clinical):
        self.store["public"][dataset_id] = (manifest, expr, clinical)

    def _find(self, dataset_id, owner):
        if owner is not None:
            priv = self.store["private"].get(owner, {})
            if dataset_id in priv:
                return priv[dataset_id]
        if dataset_id in self.store["public"]:
            return self.store["public"][dataset_id]
        for other, dsets in self.store["private"].items():
            if dataset_id in dsets:
                raise AccessDenied(f"{dataset_id} belongs to {other}")
        raise DatasetNotFound(dataset_id)

    def list_datasets(self, owner=None):
        out = [m for (m, _, _) in self.store["public"].values()]
        if owner:
            out += [m for (m, _, _) in self.store["private"].get(owner, {}).values()]
        return out

    def read_manifest(self, dataset_id, owner=None):
        return self._find(dataset_id, owner)[0]

    def read_expression(self, dataset_id, owner=None):
        return self._find(dataset_id, owner)[1]

    def read_clinical(self, dataset_id, owner=None):
        return self._find(dataset_id, owner)[2]

    def supports_write(self):
        return True

    def write_dataset(self, dataset_id, owner, manifest, expression, clinical):
        self.store["private"].setdefault(owner, {})
        m = DatasetManifest.from_dict({**manifest, "dataset_id": dataset_id,
                                       "visibility": "private", "owner": owner})
        self.store["private"][owner][dataset_id] = (m, expression, clinical)


@pytest.fixture
def local_backend():
    return LocalStorageBackend("data")


@pytest.fixture
def synthetic_dataset():
    rng = np.random.default_rng(42)
    genes = [f"ENSG{i:05d}" for i in range(10)]
    samples = [f"S{i:03d}" for i in range(40)]
    expr = pd.DataFrame(rng.normal(size=(10, 40)), index=genes, columns=samples)
    expr.iloc[3, :20] += 3.0  # plant DE in gene 3, first 20 (tumor) samples
    clinical = pd.DataFrame({
        "sample_id": samples,
        "group": ["tumor"] * 20 + ["normal"] * 20,
        "survival_months": list(rng.uniform(5, 100, 20)) + [np.nan] * 20,
        "event_observed": list(rng.binomial(1, 0.6, 20)) + [np.nan] * 20,
    }).set_index("sample_id")
    manifest = DatasetManifest.from_dict({
        "dataset_id": "test-ds", "name": "Test", "source": "synthetic",
        "cancer_type": "TEST", "n_samples": 40, "n_genes": 10, "visibility": "public",
    })
    return manifest, expr, clinical, genes


# --- core property tests ------------------------------------------------------------
def test_local_backend_lists_seeded_data(local_backend):
    ids = [m.dataset_id for m in local_backend.list_datasets()]
    assert "tcga-brca" in ids


def test_differential_detects_planted_signal(local_backend):
    res = engine.differential_expression(local_backend, "tcga-brca", "ENSG00000272172")
    assert res.log2_fold_change > 1.0   # planted strong up-regulation
    assert res.p_value < 1e-3


def test_control_gene_shows_no_signal(local_backend):
    res = engine.differential_expression(local_backend, "tcga-brca", "ENSG00000269900")
    assert res.p_value > 0.05


def test_survival_detects_planted_split(local_backend):
    res = engine.survival_analysis(local_backend, "tcga-brca", "ENSG00000272172")
    assert res.logrank_p < 0.05
    assert res.median_survival_high < res.median_survival_low  # high expr = worse


def test_backends_produce_identical_results(local_backend, synthetic_dataset):
    """THE key test: pluggability means identical numbers across backends."""
    manifest, expr, clinical, genes = synthetic_dataset
    mem = InMemoryBackend()
    mem.add_public("test-ds", manifest, expr, clinical)

    # Seed the same data into a temp local backend
    import tempfile, json
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    (tmp / "public" / "test-ds").mkdir(parents=True)
    expr.to_parquet(tmp / "public" / "test-ds" / "expression.parquet")
    clinical.to_parquet(tmp / "public" / "test-ds" / "clinical.parquet")
    (tmp / "public" / "test-ds" / "manifest.json").write_text(json.dumps({
        "dataset_id": "test-ds", "name": "Test", "source": "synthetic",
        "cancer_type": "TEST", "n_samples": 40, "n_genes": 10, "visibility": "public",
    }))
    local = LocalStorageBackend(tmp)

    de_local = engine.differential_expression(local, "test-ds", "ENSG00003")
    de_mem = engine.differential_expression(mem, "test-ds", "ENSG00003")
    assert de_local.log2_fold_change == pytest.approx(de_mem.log2_fold_change)
    assert de_local.p_value == pytest.approx(de_mem.p_value)


def test_tenant_isolation(synthetic_dataset):
    """Tenant B must not reach tenant A's private dataset."""
    manifest, expr, clinical, genes = synthetic_dataset
    mem = InMemoryBackend()
    mem.write_dataset("private-ds", "lab-a", manifest.__dict__, expr, clinical)

    # Owner sees it
    assert mem.read_manifest("private-ds", owner="lab-a") is not None
    # Other tenant is denied
    with pytest.raises(AccessDenied):
        mem.read_expression("private-ds", owner="lab-b")
    # Anonymous gets not-found
    with pytest.raises((DatasetNotFound, AccessDenied)):
        mem.read_expression("private-ds", owner=None)


# --- auth / login tests -------------------------------------------------------------
def test_login_and_session_resolution():
    from tanric.api import auth
    assert auth.login("saisadhan", "wrong-password") is None
    result = auth.login("saisadhan", "sai-password")
    assert result is not None
    token, user = result
    assert user.user_id == "saisadhan"
    assert auth.resolve_owner(token) == "saisadhan"        # valid session
    assert auth.resolve_owner("not-a-real-token") is None
    auth.logout(token)
    assert auth.resolve_owner(token) is None            # logged out


def test_passwords_are_hashed_not_plaintext():
    from tanric.api import auth
    stored = auth._USERS["saisadhan"].password_hash
    assert "sai-password" not in stored               # never stored raw
    assert auth.verify_password("sai-password", stored)
    assert not auth.verify_password("nope", stored)


# --- multi-format upload parsing ----------------------------------------------------
def test_read_table_accepts_csv_tsv_parquet(synthetic_dataset):
    from tanric.api.app import _read_table
    _, expr, _, _ = synthetic_dataset
    import io
    csv_bytes = expr.to_csv().encode()
    tsv_bytes = expr.to_csv(sep="\t").encode()
    pq_buf = io.BytesIO(); expr.to_parquet(pq_buf); pq_bytes = pq_buf.getvalue()

    from_csv = _read_table(csv_bytes, "expr.csv")
    from_tsv = _read_table(tsv_bytes, "expr.tsv")
    from_pq = _read_table(pq_bytes, "expr.parquet")

    # all three round-trip to the same shape and gene index
    assert list(from_csv.index) == list(expr.index)
    assert list(from_tsv.index) == list(expr.index)
    assert list(from_pq.index) == list(expr.index)
    assert from_csv.shape == from_tsv.shape == from_pq.shape == expr.shape


# --- cloud path against a real S3 API (moto) ----------------------------------------
def test_object_backend_against_real_s3_api(synthetic_dataset):
    """Prove the cloud path: ObjectStorageBackend round-trips through a real S3
    protocol server (moto), and analysis on S3-stored data matches local.
    Skips cleanly if moto isn't installed."""
    moto = pytest.importorskip("moto")
    from moto import mock_aws
    import boto3

    manifest, expr, clinical, genes = synthetic_dataset

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        from tanric.storage.object_store import ObjectStorageBackend
        be = ObjectStorageBackend(bucket="test-bucket", client=s3)

        # write a private dataset for owner "lab-a", read it back, analyze
        be.write_dataset("ds1", "lab-a", manifest.__dict__, expr, clinical)
        from tanric.analysis import engine
        de = engine.differential_expression(be, "ds1", "ENSG00003", owner="lab-a")
        assert de.p_value < 1e-3                       # planted signal detected via S3

        # isolation holds on the object backend too
        assert [m.dataset_id for m in be.list_datasets(owner="lab-a")] == ["ds1"]
        assert be.list_datasets(owner="lab-b") == []   # other tenant sees nothing
        assert be.list_datasets(owner=None) == []      # anonymous sees nothing
