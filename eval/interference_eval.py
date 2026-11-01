"""Phase L evaluation of the interference detector (DETECTOR_SPEC section 3). Labels are read here only.

Stages
  cache   windows.csv + window labels of every observation of a split -> eval/out/interference/cache_<split>.pkl
  fit     TRAIN: clean template, grid (5-fold GroupKFold by observation), model A, baselines, model B
          (IsolationForest), leave-one-shape-out models, ablations. CALIB: calibration (isotonic vs Platt,
          lower Brier), thresholds for 1 % window FPR, k-of-n smoothing, permutation importances.
          Writes models/interference/{model.pkl, extras.pkl, threshold.json, fit_report.json}.
  report  metrics on calib or test; test requires the signed lock models/interference/lock.json.

Window roles: positive = attack_type interference and is_attack 1; negative = not attacked with no attack
overlap (clean, benign, sequence variants, non-attacked windows of attack variants) plus replay-audio windows
of pure replay variants; excluded = 0 < overlap_frac < 0.5 and replay windows of combo variants
(interference may lie underneath). Evasion positives (test only) are reported separately.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.features import FEATURES  # noqa: E402
from detect.interference_det import BASE, GROUPS, LEVEL, MAIN, derive_many, fit_template, k_of_n_many  # noqa: E402
from eval import metrics as M  # noqa: E402
from eval.lock import require_lock  # noqa: E402
from eval.replay_eval import load_cfg, split_ids, variant_kind  # noqa: E402

MD = Path("models/interference")
OUT = Path("eval/out/interference")
GRID = [{"learning_rate": lr, "max_leaf_nodes": ln, "min_samples_leaf": ms, "l2_regularization": l2}
        for lr in (0.1,) for ln in (15, 31) for ms in (50, 400) for l2 in (0.0, 1.0)]
SMOOTHING = [(1, 1), (2, 2), (2, 3), (3, 4), (3, 5)]
LABEL_COLS = ["is_attack", "attack_type", "subtype", "snr_db", "inr_db", "overlap_frac", "evasion"]


# ------------------------------------------------------------------------------------------- data
def load_split(split: str) -> pd.DataFrame:
    p = OUT / f"cache_{split}.pkl"
    if p.exists():
        return pd.read_pickle(p)
    parts = []
    for oid, norad in sorted(split_ids(split).items()):
        d = Path("data/features") / str(oid)
        if not (d / "windows.csv").exists():
            continue
        w = pd.read_csv(d / "windows.csv", usecols=["variant_id", "win_idx", "t_start_s"] + FEATURES)
        if w.empty:
            continue
        lab = pd.read_csv(d / "labels.csv")
        lab = lab[lab["unit"] == "window"].rename(columns={"index": "win_idx"})[["variant_id", "win_idx"] + LABEL_COLS]
        w = w.merge(lab, on=["variant_id", "win_idx"], how="left", validate="one_to_one")
        w.insert(0, "norad", norad)
        w.insert(0, "obs_id", oid)
        parts.append(w)
    df = pd.concat(parts, ignore_index=True)
    df[FEATURES] = df[FEATURES].astype(np.float32)
    df["kind"] = df["variant_id"].map(variant_kind)
    df["attack_type"] = df["attack_type"].fillna("")
    pos = (df["attack_type"] == "interference") & (df["is_attack"] == 1)
    neg = ((df["is_attack"] == 0) & (df["overlap_frac"] == 0)) | \
          ((df["kind"] == "replay") & (df["attack_type"] == "replay") & (df["is_attack"] == 1))
    df["role"] = np.select([pos & (df["evasion"] == 1), pos, neg], ["evasion", "pos", "neg"], "excluded")
    df["fp_group"] = np.select([df["kind"] == "clean", df["kind"] == "benign"], ["clean", "benign"], "other")
    df = df.sort_values(["obs_id", "variant_id", "win_idx"]).reset_index(drop=True)
    df["rec"] = pd.factorize(df["obs_id"].astype(str) + "/" + df["variant_id"])[0]
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_pickle(p)
    return df


def with_features(df: pd.DataFrame, template: dict) -> pd.DataFrame:
    f = derive_many(df, template)
    return pd.concat([df, f[[c for c in f.columns if c not in df.columns]]], axis=1)


def weights66(df: pd.DataFrame) -> np.ndarray:
    """Decision 66: inside each observation, positives and negatives weigh 0.5 each; observations weigh 1."""
    y = (df["role"] == "pos").to_numpy()
    n = df.groupby(["obs_id", y]).size()
    has_both = df.assign(y=y).groupby("obs_id")["y"].agg(lambda s: s.any() and not s.all())
    cnt = np.array([n[(o, b)] for o, b in zip(df["obs_id"], y)], dtype=float)
    share = np.where(df["obs_id"].map(has_both).to_numpy(), 0.5, 1.0)
    return share / cnt


# ------------------------------------------------------------------------------------------- helpers
def X(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return df[cols].to_numpy(np.float32)


def hgb(params: dict, seed: int) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=200, early_stopping=True, validation_fraction=0.1,
                                          n_iter_no_change=10, random_state=seed, **params)


def pauc(y, s, w=None) -> float:
    return float(roc_auc_score(y, s, max_fpr=0.01, sample_weight=w))


def tau_for(neg: pd.DataFrame, s: np.ndarray, alpha: float) -> float:
    """Rule 3: threshold for <= alpha FPR on not-attacked calib windows, pooled and per satellite."""
    return M.threshold_for_fpr(s, alpha, groups=neg["norad"].to_numpy())


def smoothed_tau(df: pd.DataFrame, s: np.ndarray, k: int, n: int, alpha: float) -> float:
    """Smallest tau whose k-of-n alerts give <= alpha FPR on calib negatives (pooled and per satellite)."""
    neg = (df["role"] == "neg").to_numpy()
    rec, sat = df["rec"].to_numpy(), df["norad"].to_numpy()
    cands = np.unique(np.quantile(s[neg], np.linspace(0.8, 1.0, 2001)))
    cands = np.append(cands, np.nextafter(s[neg].max(), np.inf))

    def ok(t):
        a = k_of_n_many(s >= t, rec, k, n)[neg]
        return a.mean() <= alpha and all(a[sat[neg] == g].mean() <= alpha for g in np.unique(sat))
    lo, hi = 0, len(cands) - 1            # FPR is non-increasing in tau: binary search
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if ok(cands[mid]) else (mid + 1, hi)
    return float(cands[lo])


def rate(a: np.ndarray) -> float:
    return float(a.mean()) if len(a) else float("nan")


# ------------------------------------------------------------------------------------------- fit
def fit(cfg: dict) -> dict:
    seed, alpha, strict = cfg["seed"], cfg["detectors"]["target_fpr"], cfg["detectors"]["strict_fpr"]
    tr, ca = load_split("train"), load_split("calib")
    template = fit_template(tr[tr["kind"] == "clean"])
    tr, ca = with_features(tr, template), with_features(ca, template)
    tr = tr[tr["role"].isin(["pos", "neg"])].reset_index(drop=True)
    ytr, wtr = (tr["role"] == "pos").to_numpy(), weights66(tr)
    rep = {"n_train": {"pos": int(ytr.sum()), "neg": int((~ytr).sum()), "obs": int(tr["obs_id"].nunique())}}

    # grid on every 4th window (hop = half a window: neighbours are near copies; CPU budget), 5-fold GroupKFold by obs
    sub = (tr["win_idx"] % 4 == 0).to_numpy()
    ts, ys, ws, gs = tr[sub], ytr[sub], wtr[sub], tr["obs_id"].to_numpy()[sub]
    folds = list(GroupKFold(n_splits=5).split(ts, ys, gs))
    grid = []
    for prm in GRID:
        sc, oof = [], np.full(len(ts), np.nan)
        for a, b in folds:
            m = hgb(prm, seed).fit(X(ts.iloc[a], MAIN), ys[a], sample_weight=ws[a])
            oof[b] = m.predict_proba(X(ts.iloc[b], MAIN))[:, 1]
            sc.append(pauc(ys[b], oof[b], ws[b]))
        grid.append({**prm, "pauc_mean": float(np.mean(sc)), "pauc_folds": [round(x, 4) for x in sc], "_oof": oof})
        print(json.dumps({k: v for k, v in grid[-1].items() if k != "_oof"}), flush=True)
    best = max(grid, key=lambda g: g["pauc_mean"])
    params = {k: best[k] for k in GRID[0]}
    oof = best["_oof"]
    rep["grid"] = [{k: v for k, v in g.items() if k != "_oof"} for g in grid]
    rep["best_params"] = params

    def train_model(cols, mask=None):
        mk = np.ones(len(tr), bool) if mask is None else mask
        return hgb(params, seed).fit(X(tr[mk], cols), ytr[mk], sample_weight=weights66(tr[mk]))

    model = train_model(MAIN)
    rep["n_iter"] = int(model.n_iter_)

    # calibration on CALIB (all non-excluded windows, unweighted): isotonic vs Platt by 5-fold grouped Brier
    cal = ca[ca["role"].isin(["pos", "neg"])].reset_index(drop=True)
    yc = (cal["role"] == "pos").to_numpy()
    sc_ = model.predict_proba(X(cal, MAIN))[:, 1]
    brier = {"isotonic": [], "platt": []}
    for a, b in GroupKFold(n_splits=5).split(cal, yc, cal["obs_id"]):
        iso = IsotonicRegression(out_of_bounds="clip").fit(sc_[a], yc[a])
        lr = LogisticRegression(C=1e6).fit(sc_[a, None], yc[a])
        brier["isotonic"].append(np.mean((iso.predict(sc_[b]) - yc[b]) ** 2))
        brier["platt"].append(np.mean((lr.predict_proba(sc_[b, None])[:, 1] - yc[b]) ** 2))
    brier = {k: float(np.mean(v)) for k, v in brier.items()}
    method = min(brier, key=brier.get)
    if method == "isotonic":
        calib = {"method": "isotonic", "iso": IsotonicRegression(out_of_bounds="clip").fit(sc_, yc)}
    else:
        lr = LogisticRegression(C=1e6).fit(sc_[:, None], yc)
        calib = {"method": "platt", "a": float(lr.coef_[0, 0]), "b": float(lr.intercept_[0])}
    rep["calibration"] = {"brier_cv": brier, "method": method}

    # thresholds (raw score, calib negatives) and k-of-n smoothing tuned on calib
    negc = cal[cal["role"] == "neg"]
    tau = tau_for(negc, sc_[~yc], alpha)
    tau_strict = tau_for(negc, sc_[~yc], strict)
    ca_s = model.predict_proba(X(ca, MAIN))[:, 1]     # every calib window, excluded ones are neighbours too
    sm = []
    for k, n in SMOOTHING:
        t = smoothed_tau(ca, ca_s, k, n, alpha)
        al = k_of_n_many(ca_s >= t, ca["rec"].to_numpy(), k, n)
        sm.append({"k": k, "n": n, "tau": t, "calib_tpr": rate(al[(ca["role"] == "pos").to_numpy()]),
                   "calib_fpr": rate(al[(ca["role"] == "neg").to_numpy()])})
    pref = {(2, 3): 1}
    chosen = max(sm, key=lambda r: (round(r["calib_tpr"], 3), pref.get((r["k"], r["n"]), 0)))
    rep["smoothing"] = sm
    oof_neg = ~ys
    rep["oof_train_fpr_at_tau"] = rate(oof[oof_neg] >= tau)
    threshold = {"tau": tau, "tau_strict": tau_strict, "k": chosen["k"], "n": chosen["n"],
                 "tau_smoothed": chosen["tau"], "target_fpr": alpha, "strict_fpr": strict,
                 "rule": "tau on raw model score for <= target FPR on CALIB not-attacked windows, pooled and per satellite"}

    # baselines, model B, leave-one-shape-out, ablations (each thresholded on calib at the same FPR)
    extras = {}
    extras["energy"] = {"kind": "column", "col": "d_power_db"}
    extras["peak_to_median_db"] = {"kind": "column", "col": "peak_to_median_db"}
    trn = tr[~ytr]
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(trn), size=min(200_000, len(trn)), replace=False)
    iso_f = IsolationForest(n_estimators=200, random_state=seed).fit(X(trn.iloc[idx], MAIN))
    extras["model_B_isoforest"] = {"kind": "isoforest", "model": iso_f, "cols": MAIN}
    for shape in ("noise", "tone"):
        mk = ~(ytr & (tr["subtype"] == shape).to_numpy())
        extras[f"loso_without_{shape}"] = {"kind": "hgb", "model": train_model(MAIN, mk), "cols": MAIN, "held_out": shape}
    abl = {"with_level": MAIN + LEVEL, **{f"no_{g}": [c for c in MAIN if c not in cols] for g, cols in GROUPS.items()}}
    for name, cols in abl.items():
        extras[f"ablation_{name}"] = {"kind": "hgb", "model": train_model(cols), "cols": cols}
    for name, e in extras.items():
        s = extra_score(e, cal)
        e["tau"] = tau_for(negc, s[~yc], alpha)
        e["calib_auc"] = float(roc_auc_score(yc, s))
        print(name, round(e["calib_auc"], 4), flush=True)

    # permutation importances on a CALIB subsample (main model and the with_level ablation)
    pi_idx = rng.choice(len(cal), size=min(40_000, len(cal)), replace=False)
    imp = {}
    for name, m, cols in [("main", model, MAIN), ("with_level", extras["ablation_with_level"]["model"], MAIN + LEVEL)]:
        r = permutation_importance(m, X(cal.iloc[pi_idx], cols), yc[pi_idx], scoring="roc_auc", n_repeats=3,
                                   random_state=seed)
        imp[name] = dict(sorted(zip(cols, np.round(r.importances_mean, 5).tolist()), key=lambda kv: -kv[1]))
    rep["importances_calib"] = imp

    MD.mkdir(parents=True, exist_ok=True)
    bundle = {"model": model, "features": MAIN, "template": template, "calibration": calib, "threshold": threshold,
              "params": params}
    with open(MD / "model.pkl", "wb") as fh:
        pickle.dump(bundle, fh)
    with open(MD / "extras.pkl", "wb") as fh:
        pickle.dump(extras, fh)
    (MD / "threshold.json").write_text(json.dumps(threshold, indent=1) + "\n", encoding="utf-8")
    (MD / "template.json").write_text(json.dumps(template, indent=1) + "\n", encoding="utf-8")
    rep["threshold"] = threshold
    rep["extras_calib"] = {k: {"tau": v["tau"], "calib_auc": v["calib_auc"]} for k, v in extras.items()}
    (OUT / "fit_report.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    return rep


def extra_score(e: dict, df: pd.DataFrame) -> np.ndarray:
    if e["kind"] == "column":
        return df[e["col"]].to_numpy(float)
    if e["kind"] == "isoforest":
        return -e["model"].score_samples(X(df, e["cols"]))
    return e["model"].predict_proba(X(df, e["cols"]))[:, 1]


# ------------------------------------------------------------------------------------------- report
def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> tuple[float, list]:
    edges = np.linspace(0, 1, bins + 1)
    b = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows, e = [], 0.0
    for i in range(bins):
        m = b == i
        if m.any():
            rows.append({"bin": i, "n": int(m.sum()), "mean_p": float(p[m].mean()), "frac_pos": float(y[m].mean())})
            e += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(e), rows


def op_metrics(df: pd.DataFrame, alert: np.ndarray, bs: dict, seed: int) -> dict:
    """Metrics of one operating point (alert per window). Main metrics exclude evasion positives."""
    d = df.assign(alert=alert)
    pos, neg = d[d["role"] == "pos"], d[d["role"] == "neg"]
    out = {"tpr": rate(pos["alert"].to_numpy()), "fpr": rate(neg["alert"].to_numpy()),
           **{f"fpr_{g}": rate(neg.loc[neg["fp_group"] == g, "alert"].to_numpy()) for g in ("clean", "benign", "other")}}
    ev = d[d["role"].isin(["pos", "neg"])]
    po = []
    for oid, g in ev.groupby("obs_id"):
        y, a = (g["role"] == "pos").to_numpy(), g["alert"].to_numpy()
        tp, fp, fn = (y & a).sum(), (~y & a).sum(), (y & ~a).sum()
        po.append({"tpr": tp / y.sum() if y.any() else np.nan, "fpr": fp / (~y).sum() if (~y).any() else np.nan,
                   "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else np.nan})
    po = pd.DataFrame(po)
    for c in po.columns:
        v = po[c].dropna().to_numpy()
        out[f"per_obs_{c}"] = list(M.bootstrap_ci(v, n=bs["n"], ci=bs["ci"], seed=seed)) if len(v) else None
    delays, det, n_ev = [], 0, 0
    for _, g in d[d["role"] == "pos"].groupby("rec"):
        n_ev += 1
        hit = g[g["alert"]]
        if len(hit):
            det += 1
            delays.append(float(hit["t_start_s"].iloc[0] - g["t_start_s"].iloc[0]))
    out["events"] = {"n": n_ev, "detected": det,
                     "median_delay_s": float(np.median(delays)) if delays else None,
                     "p90_delay_s": float(np.percentile(delays, 90)) if delays else None}
    out["tpr_by_satellite"] = {str(k): rate(g["alert"].to_numpy()) for k, g in pos.groupby("norad")}
    out["fpr_by_satellite"] = {str(k): rate(g["alert"].to_numpy()) for k, g in neg.groupby("norad")}
    out["tpr_by_shape"] = {k: rate(g["alert"].to_numpy()) for k, g in pos.groupby("subtype")}
    evd = d[d["role"] == "evasion"]
    if len(evd):
        out["evasion_tpr_overall"] = rate(evd["alert"].to_numpy())
    return out


def tpr_at(y, s, fpr) -> float:
    f, t, _ = roc_curve(y, s)
    return float(np.interp(fpr, f, t))


def report(cfg: dict, split: str) -> dict:
    if split == "test":
        require_lock("interference")
    bs, seed = cfg["detectors"]["bootstrap"], cfg["seed"]
    with open(MD / "model.pkl", "rb") as fh:
        b = pickle.load(fh)
    with open(MD / "extras.pkl", "rb") as fh:
        extras = pickle.load(fh)
    df = with_features(load_split(split), b["template"])
    th = b["threshold"]
    s = b["model"].predict_proba(X(df, b["features"]))[:, 1]
    c = b["calibration"]
    p = c["iso"].predict(s) if c["method"] == "isotonic" else 1 / (1 + np.exp(-(c["a"] * s + c["b"])))
    rec = df["rec"].to_numpy()
    m = df["role"].isin(["pos", "neg"]).to_numpy()
    y = (df["role"] == "pos").to_numpy()
    r = {"split": split, "n_obs": int(df["obs_id"].nunique()),
         "n_windows": {k: int(v) for k, v in df["role"].value_counts().items()},
         "auc": float(roc_auc_score(y[m], s[m])), "tpr_at_1pct_fpr": tpr_at(y[m], s[m], 0.01),
         "tpr_at_0.1pct_fpr": tpr_at(y[m], s[m], 0.001), "threshold": th}
    r["raw"] = op_metrics(df, s >= th["tau"], bs, seed)
    r["smoothed"] = op_metrics(df, k_of_n_many(s >= th["tau_smoothed"], rec, th["k"], th["n"]), bs, seed)
    r["strict"] = op_metrics(df, s >= th["tau_strict"], bs, seed)
    ev = df["role"] == "evasion"
    if ev.any():
        al = k_of_n_many(s >= th["tau_smoothed"], rec, th["k"], th["n"])
        r["evasion_smoothed_tpr"] = {k: rate(al[(ev & (df["variant_id"] == k)).to_numpy()])
                                     for k in sorted(df.loc[ev, "variant_id"].unique())}
        r["evasion_raw_tpr_overall"] = rate((s >= th["tau"])[ev.to_numpy()])
    # Pd vs SNR (snrgrid variants) and vs INR (all positives), raw operating point
    a_raw = s >= th["tau"]
    pos = df[df["role"] == "pos"].assign(a=a_raw[y])
    grid = pos[pos["kind"].str.startswith("snrgrid")]
    src = grid if len(grid) else pos
    curve = src.groupby((src["snr_db"] / 5).round() * 5)["a"].agg(["mean", "size"])
    r["pd_vs_snr"] = {f"{k:g}": {"pd": float(v["mean"]), "n": int(v["size"])} for k, v in curve.iterrows()}
    ok = [k for k, v in curve.iterrows() if all(curve.loc[curve.index <= k, "mean"] >= 0.9)]
    r["min_detectable_snr_db"] = float(max(ok)) if ok else None
    r["pd_vs_snr_source"] = "snrgrid variants" if len(grid) else "all positives (5 dB bins)"
    ic = pos.groupby((pos["inr_db"] / 5).round() * 5)["a"].agg(["mean", "size"])
    r["pd_vs_inr"] = {f"{k:g}": {"pd": float(v["mean"]), "n": int(v["size"])} for k, v in ic.iterrows()}
    e, rows = ece(y[m].astype(float), p[m])
    r["ece"], r["reliability"] = e, rows
    # baselines, model B, leave-one-shape-out, ablations
    r["others"] = {}
    for name, e_ in extras.items():
        sc = extra_score(e_, df)
        o = {"auc": float(roc_auc_score(y[m], sc[m])), "tpr_at_1pct_fpr": tpr_at(y[m], sc[m], 0.01)}
        al = sc >= e_["tau"]
        o.update({k: v for k, v in op_metrics(df, al, bs, seed).items()
                  if k in ("tpr", "fpr_clean", "fpr_benign", "fpr_other", "per_obs_f1", "tpr_by_shape")})
        if "held_out" in e_:
            o["held_out_shape_tpr"] = rate(al[(df["role"] == "pos").to_numpy() & (df["subtype"] == e_["held_out"]).to_numpy()])
        r["others"][name] = o
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"report_{split}.json").write_text(json.dumps(r, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / f"report_{split}.md").write_text(markdown(r), encoding="utf-8")
    reliability_png(rows, OUT / f"reliability_{split}.png")
    return r


def reliability_png(rows: list, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.plot([0, 1], [0, 1], color="0.6", lw=1, ls="--")
    ax.plot([q["mean_p"] for q in rows], [q["frac_pos"] for q in rows], "o-", color="#1f6fb4")
    ax.set_xlabel("calibrated probability")
    ax.set_ylabel("fraction of interference windows")
    ax.set_title("Interference detector: reliability")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _ci(v) -> str:
    return "n/a" if not v else f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"


def markdown(r: dict) -> str:
    th = r["threshold"]
    L = [f"# Interference detector - {r['split']}", "",
         f"{r['n_obs']} observations; windows {r['n_windows']}. tau = {th['tau']:.4g} (strict {th['tau_strict']:.4g}); "
         f"smoothing {th['k']} of {th['n']} at tau {th['tau_smoothed']:.4g}.", "",
         f"ROC AUC {r['auc']:.4f}; TPR at 1 % FPR {r['tpr_at_1pct_fpr']:.3f}; at 0.1 % FPR {r['tpr_at_0.1pct_fpr']:.3f}; "
         f"ECE {r['ece']:.4f}; min detectable snr_db (Pd >= 0.9) {r['min_detectable_snr_db']}", "",
         "| operating point | TPR | FPR clean | FPR benign | FPR other | per-obs F1 | events | median delay |",
         "|---|---|---|---|---|---|---|---|"]
    for k in ("raw", "smoothed", "strict"):
        o = r[k]
        L.append(f"| {k} | {o['tpr']:.3f} | {o['fpr_clean']:.4f} | {o['fpr_benign']:.4f} | {o['fpr_other']:.4f} | "
                 f"{_ci(o['per_obs_f1'])} | {o['events']['detected']} / {o['events']['n']} | {o['events']['median_delay_s']} s |")
    o = r["raw"]
    L += ["", f"Per satellite TPR {o['tpr_by_satellite']}, FPR {o['fpr_by_satellite']}; per shape TPR {o['tpr_by_shape']}", "",
          "Pd vs snr_db (" + r["pd_vs_snr_source"] + "): " +
          ", ".join(f"{k}: {v['pd']:.2f}" for k, v in r["pd_vs_snr"].items()), "",
          "| detector | AUC | TPR@1%FPR | TPR at tau | FPR clean | FPR benign | per-obs F1 | held-out shape TPR |",
          "|---|---|---|---|---|---|---|---|"]
    for n, o in r["others"].items():
        L.append(f"| {n} | {o['auc']:.4f} | {o['tpr_at_1pct_fpr']:.3f} | {o['tpr']:.3f} | {o['fpr_clean']:.4f} | "
                 f"{o['fpr_benign']:.4f} | {_ci(o['per_obs_f1'])} | {o.get('held_out_shape_tpr', '')} |")
    if "evasion_smoothed_tpr" in r:
        L += ["", f"Evasion (smoothed) TPR: {r['evasion_smoothed_tpr']}"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase L interference detector: cache / fit / report")
    ap.add_argument("stage", choices=["cache", "fit", "report"])
    ap.add_argument("--split", default="calib", choices=["train", "calib", "test"])
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.stage == "cache":
        for sp in ("train", "calib", "test") if a.split == "calib" else (a.split,):
            d = load_split(sp)
            print(json.dumps({"split": sp, "rows": len(d), "roles": d["role"].value_counts().to_dict()}))
    elif a.stage == "fit":
        rep = fit(cfg)
        print(json.dumps({k: rep[k] for k in ("best_params", "calibration", "threshold", "smoothing")}, indent=1))
    else:
        r = report(cfg, a.split)
        print(markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
