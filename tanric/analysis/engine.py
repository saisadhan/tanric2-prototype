"""
Analysis engine  ->  the modernized analysis core (Direction 3).

These functions reimplement the most-used parts of TANRIC's "My lncRNA" module
as a clean, testable, framework-free API:

  * query_expression      - expression of one lncRNA across samples/groups
  * differential_expression - tumor vs normal comparison (the box-plot question)
  * survival_analysis     - Kaplan-Meier + log-rank, high vs low expressors

KEY DESIGN PROPERTY: every function takes a `StorageBackend`, never a path or a
bucket. The engine asks the backend for data and computes statistics. It has no
idea whether it is running on-prem or in the cloud, on public or private data.
That decoupling is what lets the same engine serve every deployment.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter
from lifelines.statistics import logrank_test
from scipy import stats

from ..storage.base import StorageBackend


# --- result types --------------------------------------------------------------------
@dataclass
class ExpressionResult:
    dataset_id: str
    gene: str
    units: str
    by_group: dict          # group -> list of expression values
    summary: dict           # group -> {n, mean, median, std}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DifferentialResult:
    dataset_id: str
    gene: str
    mean_tumor: float
    mean_normal: float
    log2_fold_change: float
    fold_change: float
    t_statistic: float
    p_value: float
    n_tumor: int
    n_normal: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SurvivalResult:
    dataset_id: str
    gene: str
    split: str              # how high/low groups were defined
    logrank_p: float
    n_high: int
    n_low: int
    median_survival_high: float | None
    median_survival_low: float | None
    km_curves: dict         # group -> {"time": [...], "survival": [...]}

    def to_dict(self) -> dict:
        return asdict(self)


# --- helpers -------------------------------------------------------------------------
def _get_gene_series(backend: StorageBackend, dataset_id: str, gene: str,
                     owner: str | None) -> pd.Series:
    expr = backend.read_expression(dataset_id, owner=owner)
    if gene not in expr.index:
        raise KeyError(f"Gene '{gene}' not in dataset '{dataset_id}'.")
    return expr.loc[gene]


# --- public API ----------------------------------------------------------------------
def query_expression(backend: StorageBackend, dataset_id: str, gene: str,
                     owner: str | None = None) -> ExpressionResult:
    series = _get_gene_series(backend, dataset_id, gene, owner)
    clinical = backend.read_clinical(dataset_id, owner=owner)
    manifest = backend.read_manifest(dataset_id, owner=owner)

    df = pd.DataFrame({"expr": series}).join(clinical[["group"]])
    by_group: dict[str, list[float]] = {}
    summary: dict[str, dict] = {}
    for grp, sub in df.groupby("group"):
        vals = sub["expr"].dropna().tolist()
        by_group[grp] = [round(float(v), 4) for v in vals]
        summary[grp] = {
            "n": len(vals),
            "mean": round(float(np.mean(vals)), 4),
            "median": round(float(np.median(vals)), 4),
            "std": round(float(np.std(vals, ddof=1)), 4) if len(vals) > 1 else 0.0,
        }

    return ExpressionResult(
        dataset_id=dataset_id, gene=gene,
        units=manifest.__dict__.get("expression_units", "log2(RPKM+0.001)")
        if hasattr(manifest, "__dict__") else "log2(RPKM+0.001)",
        by_group=by_group, summary=summary,
    )


def differential_expression(backend: StorageBackend, dataset_id: str, gene: str,
                            owner: str | None = None) -> DifferentialResult:
    series = _get_gene_series(backend, dataset_id, gene, owner)
    clinical = backend.read_clinical(dataset_id, owner=owner)
    df = pd.DataFrame({"expr": series}).join(clinical[["group"]])

    tumor = df.loc[df["group"] == "tumor", "expr"].dropna()
    normal = df.loc[df["group"] == "normal", "expr"].dropna()
    if len(tumor) < 2 or len(normal) < 2:
        raise ValueError("Need at least 2 tumor and 2 normal samples for DE.")

    # Welch's t-test on log2 values (standard for log-expression).
    t_stat, p_val = stats.ttest_ind(tumor, normal, equal_var=False)
    mean_t, mean_n = float(tumor.mean()), float(normal.mean())
    log2fc = mean_t - mean_n  # already log2 space -> difference is log2 fold change

    return DifferentialResult(
        dataset_id=dataset_id, gene=gene,
        mean_tumor=round(mean_t, 4), mean_normal=round(mean_n, 4),
        log2_fold_change=round(log2fc, 4),
        fold_change=round(float(2 ** log2fc), 4),
        t_statistic=round(float(t_stat), 4),
        p_value=float(f"{p_val:.3e}"),
        n_tumor=int(len(tumor)), n_normal=int(len(normal)),
    )


def survival_analysis(backend: StorageBackend, dataset_id: str, gene: str,
                      owner: str | None = None,
                      split: str = "median") -> SurvivalResult:
    series = _get_gene_series(backend, dataset_id, gene, owner)
    clinical = backend.read_clinical(dataset_id, owner=owner)

    df = pd.DataFrame({"expr": series}).join(
        clinical[["group", "survival_months", "event_observed"]]
    )
    # Survival is only defined for tumor samples with recorded outcomes.
    df = df[(df["group"] == "tumor")].dropna(
        subset=["survival_months", "event_observed"]
    )
    if len(df) < 6:
        raise ValueError("Not enough tumor samples with survival data.")

    cutoff = df["expr"].median()
    high = df[df["expr"] > cutoff]
    low = df[df["expr"] <= cutoff]

    lr = logrank_test(
        high["survival_months"], low["survival_months"],
        event_observed_A=high["event_observed"],
        event_observed_B=low["event_observed"],
    )

    km_curves: dict[str, dict] = {}
    medians: dict[str, float | None] = {}
    for label, grp in [("high", high), ("low", low)]:
        kmf = KaplanMeierFitter()
        kmf.fit(grp["survival_months"], event_observed=grp["event_observed"])
        sf = kmf.survival_function_
        km_curves[label] = {
            "time": [round(float(t), 2) for t in sf.index.tolist()],
            "survival": [round(float(v), 4) for v in sf.iloc[:, 0].tolist()],
        }
        med = kmf.median_survival_time_
        medians[label] = None if (med is None or np.isinf(med)) else round(float(med), 2)

    return SurvivalResult(
        dataset_id=dataset_id, gene=gene,
        split=f"{split} expression (cutoff={round(float(cutoff), 4)})",
        logrank_p=float(f"{lr.p_value:.3e}"),
        n_high=int(len(high)), n_low=int(len(low)),
        median_survival_high=medians["high"],
        median_survival_low=medians["low"],
        km_curves=km_curves,
    )
