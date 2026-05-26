"""
Generate biologically plausible *synthetic* lncRNA datasets for TANRIC 2.0 prototype.

Why synthetic? Real TCGA data is large and access-controlled. For an architecture
prototype we only need data with the right *shape* and *statistical structure* so
that the analysis engine returns meaningful, non-trivial results. Nothing here is
real patient data.

Each dataset is written as a self-contained directory:
    <dataset_id>/
        manifest.json        # metadata: name, source, n_samples, cancer_type, columns
        expression.parquet   # genes (rows) x samples (cols), log2(RPKM+0.001)
        clinical.parquet     # one row per sample: group, survival time, event

This on-disk layout is deliberately storage-agnostic: the exact same three
objects can live on a local filesystem (on-prem) or in a cloud object store.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# A small fixed panel of Ensembl-style lncRNA IDs. A handful of dozen is plenty
# to demonstrate the analysis engine.
LNCRNA_IDS = [
    "ENSG00000228630",  # HOTAIR-like
    "ENSG00000245532",  # NEAT1-like
    "ENSG00000251562",  # MALAT1-like
    "ENSG00000272172",  # HRD-associated (from literature)
    "ENSG00000234741",  # GAS5-like
    "ENSG00000226950",  # DANCR-like
    "ENSG00000260032",  # NORAD-like
    "ENSG00000270066",  # SNHG-like
    "ENSG00000229807",  # XIST-like
    "ENSG00000233429",  # HOTAIRM1-like
    "ENSG00000269900",  # generic
    "ENSG00000257261",  # generic
    "ENSG00000245164",  # generic
    "ENSG00000247556",  # generic
    "ENSG00000231672",  # generic
    "ENSG00000259976",  # generic
    "ENSG00000253352",  # generic
    "ENSG00000272512",  # generic
    "ENSG00000224167",  # generic
    "ENSG00000280206",  # generic
]


def _simulate_dataset(
    rng: np.random.Generator,
    n_tumor: int,
    n_normal: int,
    cancer_type: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (expression_df, clinical_df) with planted biological signal."""
    n_genes = len(LNCRNA_IDS)
    tumor_ids = [f"{cancer_type}-T{i:03d}" for i in range(n_tumor)]
    normal_ids = [f"{cancer_type}-N{i:03d}" for i in range(n_normal)]
    sample_ids = tumor_ids + normal_ids

    # Baseline log2 expression per gene (some lowly, some highly expressed).
    base = rng.normal(loc=2.0, scale=2.0, size=n_genes)

    expr = np.zeros((n_genes, len(sample_ids)))
    for g in range(n_genes):
        expr[g, :] = rng.normal(loc=base[g], scale=0.8, size=len(sample_ids))

    # Plant differential expression in a subset of genes: tumor samples get a shift.
    # Genes 0-4 strongly up in tumor, gene 5 down in tumor, rest unchanged.
    de_effects = {0: +2.2, 1: +1.8, 2: +1.5, 3: +2.5, 4: +1.2, 5: -1.6}
    for g, effect in de_effects.items():
        expr[g, :n_tumor] += effect

    expression_df = pd.DataFrame(expr, index=LNCRNA_IDS, columns=sample_ids)

    # Clinical table. Survival only meaningful for tumor samples; normals get NaN.
    # Plant a survival association: high expression of gene 3 -> worse survival.
    driver_gene = 3
    driver_expr_tumor = expr[driver_gene, :n_tumor]
    # Higher expression -> higher hazard -> shorter time.
    z = (driver_expr_tumor - driver_expr_tumor.mean()) / (driver_expr_tumor.std() + 1e-9)
    baseline_months = 60.0
    surv_time = baseline_months * np.exp(-0.45 * z) * rng.uniform(0.6, 1.4, size=n_tumor)
    surv_time = np.clip(surv_time, 1.0, 180.0)
    # Censoring: ~40% of patients censored (event not observed by end of study).
    event = rng.binomial(1, 0.6, size=n_tumor)

    clinical = pd.DataFrame(
        {
            "sample_id": sample_ids,
            "group": ["tumor"] * n_tumor + ["normal"] * n_normal,
            "cancer_type": cancer_type,
            "survival_months": list(surv_time) + [np.nan] * n_normal,
            "event_observed": list(event) + [np.nan] * n_normal,
        }
    ).set_index("sample_id")

    return expression_df, clinical


def write_dataset(
    out_root: Path,
    dataset_id: str,
    name: str,
    source: str,
    cancer_type: str,
    n_tumor: int,
    n_normal: int,
    seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    expression_df, clinical = _simulate_dataset(rng, n_tumor, n_normal, cancer_type)

    ds_dir = out_root / dataset_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    expression_df.to_parquet(ds_dir / "expression.parquet")
    clinical.to_parquet(ds_dir / "clinical.parquet")

    manifest = {
        "dataset_id": dataset_id,
        "name": name,
        "source": source,
        "cancer_type": cancer_type,
        "n_samples": int(n_tumor + n_normal),
        "n_tumor": int(n_tumor),
        "n_normal": int(n_normal),
        "n_genes": len(LNCRNA_IDS),
        "genes": LNCRNA_IDS,
        "expression_units": "log2(RPKM+0.001)",
        "visibility": "public",
    }
    (ds_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"  wrote {dataset_id}: {n_tumor} tumor + {n_normal} normal -> {ds_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic TANRIC datasets")
    parser.add_argument("--out", default="data/public", help="output root directory")
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"Generating synthetic public datasets in {out_root} ...")
    write_dataset(out_root, "tcga-brca", "TCGA Breast Carcinoma (synthetic)",
                  "TCGA", "BRCA", n_tumor=120, n_normal=30, seed=1)
    write_dataset(out_root, "tcga-luad", "TCGA Lung Adenocarcinoma (synthetic)",
                  "TCGA", "LUAD", n_tumor=90, n_normal=20, seed=2)
    write_dataset(out_root, "tcga-kirc", "TCGA Kidney Renal Clear Cell (synthetic)",
                  "TCGA", "KIRC", n_tumor=80, n_normal=25, seed=3)
    print("Done.")


if __name__ == "__main__":
    main()
