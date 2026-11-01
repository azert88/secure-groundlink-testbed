"""Shared evaluation metrics (DETECTOR_SPEC rules 3 and 9). Reads no files; used by eval scripts only.

- threshold_for_fpr: smallest threshold (alert if score >= tau) whose false-alarm rate on not-attacked
  calib samples is <= alpha, pooled AND inside every group (e.g. satellite).
- binary: TP / FP / FN / TN, precision, recall, F1, FPR.
- per_obs + bootstrap_ci: metrics per observation, averaged; percentile bootstrap over observations.
- eer: equal error rate from the ROC curve (linear interpolation).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve


def threshold_for_fpr(neg: np.ndarray, alpha: float, groups: np.ndarray | None = None,
                      extra_candidates: np.ndarray | None = None) -> float:
    """tau such that mean(neg >= tau) <= alpha pooled and per group; smallest such candidate."""
    neg = np.nan_to_num(np.asarray(neg, dtype=float), nan=-np.inf)
    cands = np.unique(np.concatenate([neg[np.isfinite(neg)], np.asarray(extra_candidates if extra_candidates is not None
                                                                        else [], dtype=float)]))
    top = neg[np.isfinite(neg)].max() if np.isfinite(neg).any() else 0.0
    cands = np.append(cands, np.nextafter(top, np.inf))
    g = np.zeros(len(neg), dtype=int) if groups is None else pd.factorize(np.asarray(groups))[0]
    for c in np.sort(cands):
        al = neg >= c
        if al.mean() > alpha:
            continue
        if all(al[g == k].mean() <= alpha for k in np.unique(g)):
            return float(c)
    return float(np.inf)


def binary(y: np.ndarray, yhat: np.ndarray) -> dict:
    y, yhat = np.asarray(y, dtype=bool), np.asarray(yhat, dtype=bool)
    tp, fp = int((y & yhat).sum()), int((~y & yhat).sum())
    fn, tn = int((y & ~yhat).sum()), int((~y & ~yhat).sum())
    prec = tp / (tp + fp) if tp + fp else math.nan
    rec = tp / (tp + fn) if tp + fn else math.nan
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else math.nan
    fpr = fp / (fp + tn) if fp + tn else math.nan
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec, "f1": f1, "fpr": fpr}


def per_obs(df: pd.DataFrame, y: str, yhat: str, obs: str = "obs_id") -> pd.DataFrame:
    """One row of binary() metrics per observation."""
    rows = [{obs: k, **binary(g[y].to_numpy(), g[yhat].to_numpy())} for k, g in df.groupby(obs)]
    return pd.DataFrame(rows)


def bootstrap_ci(values: np.ndarray, n: int = 2000, ci: float = 0.95, seed: int = 0) -> tuple[float, float, float]:
    """(nan-mean, lo, hi) of per-observation values; percentile bootstrap over observations."""
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return math.nan, math.nan, math.nan
    rng = np.random.default_rng(seed)
    means = rng.choice(v, size=(n, len(v)), replace=True).mean(axis=1)
    a = (1 - ci) / 2
    return float(v.mean()), float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def bootstrap_stat(df: pd.DataFrame, fn, obs: str = "obs_id", n: int = 2000, ci: float = 0.95,
                   seed: int = 0) -> tuple[float, float, float]:
    """(value, lo, hi) of a pooled statistic fn(df), resampling whole observations."""
    val = fn(df)
    ids = df[obs].unique()
    groups = {k: g for k, g in df.groupby(obs)}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = rng.choice(ids, size=len(ids), replace=True)
        vals.append(fn(pd.concat([groups[k] for k in pick], ignore_index=True)))
    vals = np.asarray([x for x in vals if not (isinstance(x, float) and math.isnan(x))], dtype=float)
    if len(vals) == 0:
        return val, math.nan, math.nan
    a = (1 - ci) / 2
    return val, float(np.quantile(vals, a)), float(np.quantile(vals, 1 - a))


def eer(y: np.ndarray, s: np.ndarray) -> float:
    """Equal error rate: point where FPR = 1 - TPR on the ROC curve."""
    y, s = np.asarray(y, dtype=int), _finite(s)
    if y.min() == y.max():
        return math.nan
    fpr, tpr, _ = roc_curve(y, s)
    fnr = 1 - tpr
    d = fpr - fnr
    k = int(np.argmax(d >= 0))
    if k == 0:
        return float(fpr[0])
    x0, x1 = d[k - 1], d[k]
    w = x0 / (x0 - x1) if x0 != x1 else 0.0
    return float(fpr[k - 1] + w * (fpr[k] - fpr[k - 1]))


def auc(y: np.ndarray, s: np.ndarray) -> float:
    y = np.asarray(y, dtype=int)
    if y.min() == y.max():
        return math.nan
    return float(roc_auc_score(y, _finite(s)))


def _finite(s) -> np.ndarray:
    """Missing scores (NaN, e.g. no audio) rank below every real score; infinities are clipped."""
    s = np.asarray(s, dtype=float)
    ok = np.isfinite(s)
    lo = s[ok].min() if ok.any() else 0.0
    hi = s[ok].max() if ok.any() else 0.0
    return np.where(np.isnan(s), lo - 1.0, np.clip(s, lo - 1.0, hi + 1.0))
