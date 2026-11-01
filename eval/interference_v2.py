"""Phase L interference detector, version 2 (post hoc, decision 92). Labels are read here only.

Changes from version 1, decided after the v1 diagnosis (decision 89) and before any v2 test look:
  (a) no occupied-bandwidth features; features whose per-recording clean median follows the receiver band edge
      (|Spearman| >= 0.75 on train + calib: peak_width_hz, flatness, spectral_entropy, spectral_kurtosis) are
      used only as within-recording deviations d_<f> (the band edge is fixed inside a recording);
  (b) template scale floored at the spread (SD) of per-station clean medians (pooled when < 3 stations) and at
      a physical minimum (1 dB, 1 window, 1 frequency bin);
  (c) station-grouped cross-validation on train + calib (6 folds, GroupKFold by station): every window is
      scored by a model that never saw its station; these out-of-fold scores set the calibration, the 1 %
      FPR threshold and the smoothing. v1 hyperparameters, no new grid; models trained on every 2nd window.
Gate (fixed before running): v2 replaces nothing and is not tested unless, at each version's own OOF 1 % threshold,
its worst-station FPR is lower, fewer stations exceed 2 % FPR, and its OOF TPR at 1 % FPR is at most 0.05 below v1.

Stages: loso (v1 and v2 under the same folds -> eval/out/interference_v2/loso_report.json, final v2 model in
models/interference_v2/), report --split test (requires both locks).
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.interference_det import BASE, MAIN, derive_many, fit_template, k_of_n_many  # noqa: E402
from eval import metrics as M  # noqa: E402
from eval.interference_eval import (SMOOTHING, X, ece, hgb, load_split, op_metrics, rate, smoothed_tau,  # noqa: E402
                                    tpr_at, weights66)
from eval.lock import require_lock  # noqa: E402
from eval.replay_eval import load_cfg  # noqa: E402

MD = Path("models/interference_v2")
OUT = Path("eval/out/interference_v2")
BAND_EDGE = ["peak_width_hz", "flatness", "spectral_entropy", "spectral_kurtosis", "occupied_bw_hz"]
V2 = (["peak_to_median_db", "max_dev_from_obs_median_db", "peak_persistence"]
      + ["tz_peak_to_median_db", "tz_max_dev_from_obs_median_db", "tz_peak_persistence", "peak_rarity"]
      + ["d_peak_to_median_db", "d_flatness", "d_spectral_entropy", "d_spectral_kurtosis", "d_max_dev_from_obs_median_db"])
N_FOLDS = 6
# physical minimum scales: 1 dB, 1 window, 1 frequency bin (48000 / 2048 Hz)
MIN_SCALE = {"peak_to_median_db": 1.0, "max_dev_from_obs_median_db": 1.0, "peak_persistence": 1.0,
             "peak_width_hz": 23.4375, "occupied_bw_hz": 23.4375}


def stations() -> dict[int, int]:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(o): int(v["station_id"]) for s in sp["satellites"].values() for o, v in s["obs"].items()}


def fit_template_v2(clean: pd.DataFrame) -> dict:
    """v1 template with the scale floored at the between-station spread of per-observation clean medians."""
    t = fit_template(clean)
    med = clean.groupby(["norad", "station", "obs_id"])[BASE].median().groupby(["norad", "station"]).median()
    pooled_sd = med.std()
    for key in t:
        g = med if key == "pooled" else med.loc[int(key)] if int(key) in med.index.get_level_values(0) else med
        sd = g.std() if len(g) >= 3 else pooled_sd
        t[key]["scale"] = {f: float(max(t[key]["scale"][f], np.nan_to_num(sd[f]), MIN_SCALE.get(f, 0.0))) for f in BASE}
        t[key]["scale_floor"] = "max(window MAD, between-station SD of clean medians, physical minimum)"
    return t


def features(df: pd.DataFrame, template: dict, cols: list[str]) -> np.ndarray:
    return derive_many(df, template)[cols].to_numpy(np.float32)


def calibrate_oof(s: np.ndarray, y: np.ndarray, groups: np.ndarray) -> dict:
    brier = {"isotonic": [], "platt": []}
    for a, b in GroupKFold(n_splits=5).split(s, y, groups):
        brier["isotonic"].append(np.mean((IsotonicRegression(out_of_bounds="clip").fit(s[a], y[a]).predict(s[b]) - y[b]) ** 2))
        brier["platt"].append(np.mean((LogisticRegression(C=1e6).fit(s[a, None], y[a]).predict_proba(s[b, None])[:, 1] - y[b]) ** 2))
    brier = {k: float(np.mean(v)) for k, v in brier.items()}
    if brier["isotonic"] <= brier["platt"]:
        return {"method": "isotonic", "iso": IsotonicRegression(out_of_bounds="clip").fit(s, y), "brier_cv": brier}
    lr = LogisticRegression(C=1e6).fit(s[:, None], y)
    return {"method": "platt", "a": float(lr.coef_[0, 0]), "b": float(lr.intercept_[0]), "brier_cv": brier}


def summary(df: pd.DataFrame, s: np.ndarray, alpha: float) -> dict:
    """OOF comparison at the version's own 1 % threshold (pooled and per satellite)."""
    m = df["role"].isin(["pos", "neg"]).to_numpy()
    y = (df["role"] == "pos").to_numpy()
    neg = df["role"].to_numpy() == "neg"
    tau = M.threshold_for_fpr(s[neg], alpha, groups=df["norad"].to_numpy()[neg])
    a = s >= tau
    d = df[neg].assign(a=a[neg])
    per_st = d.groupby("station")["a"].mean()
    return {"auc": float(roc_auc_score(y[m], s[m])), "tpr_at_1pct_fpr_curve": tpr_at(y[m], s[m], alpha),
            "tau": float(tau), "tpr_at_tau": rate(a[y]),
            "fpr_clean": rate(d.loc[d["fp_group"] == "clean", "a"].to_numpy()),
            "fpr_benign": rate(d.loc[d["fp_group"] == "benign", "a"].to_numpy()),
            "worst_station_fpr": float(per_st.max()), "n_stations_fpr_above_2pct": int((per_st > 0.02).sum()),
            "per_station_fpr": {str(k): round(float(v), 4) for k, v in per_st.items()}}


def loso(cfg: dict) -> dict:
    seed, alpha = cfg["seed"], cfg["detectors"]["target_fpr"]
    with open("models/interference/model.pkl", "rb") as fh:
        params = pickle.load(fh)["params"]
    st = stations()
    df = pd.concat([load_split("train"), load_split("calib")], ignore_index=True)
    df["station"] = df["obs_id"].map(st)
    df["rec"] = pd.factorize(df["obs_id"].astype(str) + "/" + df["variant_id"])[0]
    use = df["role"].isin(["pos", "neg"]).to_numpy() & (df["win_idx"] % 2 == 0).to_numpy()
    y = (df["role"] == "pos").to_numpy()
    folds = list(GroupKFold(n_splits=N_FOLDS).split(df, y, df["station"]))
    versions = {"v1": (fit_template, MAIN), "v2": (fit_template_v2, V2)}
    oof = {v: np.zeros(len(df)) for v in versions}
    for i, (a, b) in enumerate(folds):
        tr = np.zeros(len(df), bool)
        tr[a] = True
        for v, (fit_t, cols) in versions.items():
            t = fit_t(df[tr & (df["kind"] == "clean").to_numpy()])
            sel = df[tr & use]
            mdl = hgb(params, seed).fit(features(sel, t, cols), y[tr & use], sample_weight=weights66(sel))
            oof[v][b] = mdl.predict_proba(features(df.iloc[b], t, cols))[:, 1]
        print(json.dumps({"fold": i, "held_out_stations": sorted(df.iloc[b]["station"].unique().tolist())}), flush=True)
    rep = {"folds": N_FOLDS, "stations": int(df["station"].nunique()), "params": params,
           "v1": summary(df, oof["v1"], alpha), "v2": summary(df, oof["v2"], alpha)}
    v1, v2 = rep["v1"], rep["v2"]
    rep["gate"] = {"worst_station_lower": v2["worst_station_fpr"] < v1["worst_station_fpr"],
                   "fewer_stations_above_2pct": v2["n_stations_fpr_above_2pct"] < v1["n_stations_fpr_above_2pct"],
                   "tpr_not_much_lower": v2["tpr_at_1pct_fpr_curve"] >= v1["tpr_at_1pct_fpr_curve"] - 0.05}
    rep["gate"]["pass"] = all(rep["gate"].values())
    OUT.mkdir(parents=True, exist_ok=True)
    np.save(OUT / "oof_scores.npy", np.vstack([oof["v1"], oof["v2"]]))
    if rep["gate"]["pass"]:
        rep["final"] = fit_final(df, oof["v2"], use, y, params, seed, alpha, cfg["detectors"]["strict_fpr"])
    (OUT / "loso_report.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    return rep


def fit_final(df, s, use, y, params, seed, alpha, strict) -> dict:
    """v2 model on all train + calib stations; calibration, threshold and smoothing from the OOF scores."""
    t = fit_template_v2(df[df["kind"] == "clean"])
    sel = df[use]
    model = hgb(params, seed).fit(features(sel, t, V2), y[use], sample_weight=weights66(sel))
    m = df["role"].isin(["pos", "neg"]).to_numpy()
    calib = calibrate_oof(s[m], y[m].astype(float), df["station"].to_numpy()[m])
    neg = df["role"].to_numpy() == "neg"
    g = df["norad"].to_numpy()[neg]
    tau, tau_strict = M.threshold_for_fpr(s[neg], alpha, groups=g), M.threshold_for_fpr(s[neg], strict, groups=g)
    sm = []
    for k, n in SMOOTHING:
        tk = smoothed_tau(df, s, k, n, alpha)
        al = k_of_n_many(s >= tk, df["rec"].to_numpy(), k, n)
        sm.append({"k": k, "n": n, "tau": tk, "oof_tpr": rate(al[y]), "oof_fpr": rate(al[neg])})
    ch = max(sm, key=lambda r: (round(r["oof_tpr"], 3), (r["k"], r["n"]) == (2, 3)))
    th = {"tau": tau, "tau_strict": tau_strict, "k": ch["k"], "n": ch["n"], "tau_smoothed": ch["tau"],
          "target_fpr": alpha, "strict_fpr": strict,
          "rule": "tau on station-out-of-fold scores (train + calib) for <= target FPR on not-attacked windows, pooled and per satellite"}
    MD.mkdir(parents=True, exist_ok=True)
    with open(MD / "model.pkl", "wb") as fh:
        pickle.dump({"model": model, "features": V2, "template": t, "calibration": calib, "threshold": th,
                     "params": params, "version": 2}, fh)
    (MD / "threshold.json").write_text(json.dumps(th, indent=1) + "\n", encoding="utf-8")
    (MD / "template.json").write_text(json.dumps(t, indent=1) + "\n", encoding="utf-8")
    return {"threshold": th, "calibration": {"method": calib["method"], "brier_cv": calib["brier_cv"]}, "smoothing": sm,
            "n_iter": int(model.n_iter_)}


def score_bundle(path: Path, df: pd.DataFrame):
    with open(path, "rb") as fh:
        b = pickle.load(fh)
    s = b["model"].predict_proba(features(df, b["template"], b["features"]))[:, 1]
    c = b["calibration"]
    p = c["iso"].predict(s) if c["method"] == "isotonic" else 1 / (1 + np.exp(-(c["a"] * s + c["b"])))
    return b, s, p


def report_test(cfg: dict) -> dict:
    require_lock("interference")
    require_lock("interference_v2")
    bs, seed = cfg["detectors"]["bootstrap"], cfg["seed"]
    df = load_split("test")
    df["station"] = df["obs_id"].map(stations())
    m = df["role"].isin(["pos", "neg"]).to_numpy()
    y = (df["role"] == "pos").to_numpy()
    rec = df["rec"].to_numpy()
    r = {"split": "test", "n_obs": int(df["obs_id"].nunique())}
    for v, path in [("v1", Path("models/interference/model.pkl")), ("v2", MD / "model.pkl")]:
        b, s, p = score_bundle(path, df)
        th = b["threshold"]
        neg = df["role"] == "neg"
        o = {"auc": float(roc_auc_score(y[m], s[m])), "tpr_at_1pct_fpr_curve": tpr_at(y[m], s[m], 0.01),
             "ece": ece(y[m].astype(float), p[m])[0], "threshold": th,
             "raw": op_metrics(df, s >= th["tau"], bs, seed),
             "smoothed": op_metrics(df, k_of_n_many(s >= th["tau_smoothed"], rec, th["k"], th["n"]), bs, seed),
             "strict": op_metrics(df, s >= th["tau_strict"], bs, seed),
             "per_station_fpr_raw": {str(k): round(float(x), 4) for k, x in
                                     df[neg].assign(a=(s >= th["tau"])[neg.to_numpy()]).groupby("station")["a"].mean().items()}}
        pos = df[df["role"] == "pos"].assign(a=(s >= th["tau"])[y])
        grid = pos[pos["kind"].str.startswith("snrgrid")]
        curve = grid.groupby((grid["snr_db"] / 5).round() * 5)["a"].mean()
        o["pd_vs_snr"] = {f"{k:g}": round(float(x), 3) for k, x in curve.items()}
        ok = [k for k in curve.index if (curve[curve.index <= k] >= 0.9).all()]
        o["min_detectable_snr_db"] = float(max(ok)) if ok else None
        ev = (df["role"] == "evasion").to_numpy()
        o["evasion_smoothed_tpr"] = rate(k_of_n_many(s >= th["tau_smoothed"], rec, th["k"], th["n"])[ev])
        r[v] = o
    (OUT / "report_test.json").write_text(json.dumps(r, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "report_test.md").write_text(markdown(r), encoding="utf-8")
    return r


def _ci(v) -> str:
    return "n/a" if not v else f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"


def markdown(r: dict) -> str:
    L = ["# Interference detector v1 (official) vs v2 (post hoc) - test", "",
         "| version | point | AUC | TPR@1%FPR curve | TPR | FPR clean | FPR benign | per-obs F1 | events | ECE |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for v in ("v1", "v2"):
        o = r[v]
        for k in ("raw", "smoothed", "strict"):
            q = o[k]
            L.append(f"| {v} | {k} | {o['auc']:.4f} | {o['tpr_at_1pct_fpr_curve']:.3f} | {q['tpr']:.3f} | {q['fpr_clean']:.4f} | "
                     f"{q['fpr_benign']:.4f} | {_ci(q['per_obs_f1'])} | {q['events']['detected']} / {q['events']['n']} | {o['ece']:.3f} |")
    for v in ("v1", "v2"):
        o = r[v]
        L += ["", f"{v}: per-station FPR (raw) {o['per_station_fpr_raw']}; Pd vs snr_db {o['pd_vs_snr']}; "
              f"min detectable snr_db {o['min_detectable_snr_db']}; evasion TPR (smoothed) {o['evasion_smoothed_tpr']:.3f}; "
              f"TPR by shape {o['raw']['tpr_by_shape']}"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Interference detector v2: station-grouped CV, final model, test report")
    ap.add_argument("stage", choices=["loso", "report"])
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.stage == "loso":
        rep = loso(cfg)
        print(json.dumps({k: rep[k] for k in ("v1", "v2", "gate")}, indent=1))
    else:
        print(markdown(report_test(cfg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
