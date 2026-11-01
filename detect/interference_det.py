"""Phase L interference detector (docs/DETECTOR_SPEC.md section 3). Works on the windows of ONE recording.

Input: the per-window features of detect/features.py (windows.csv columns) for one recording and its
satellite (NORAD id). No labels are read here.

Model features (relative / scale-invariant first, rule 1 of the spec):
  base        peak_to_median_db, peak_width_hz, peak_persistence, max_dev_from_obs_median_db, flatness,
              spectral_entropy, spectral_kurtosis, occupied_bw_hz            (detect/features.py)
  tz_<f>      robust z-score of each base feature against the TRAIN-only clean template of the satellite
              (median and MAD of TRAIN clean windows). The frozen dataset stores window features, not
              spectra, so the template lives in feature space.
  peak_rarity -log10 of the share of TRAIN clean windows of the satellite whose peak lies in the same
              100 Hz bin (Laplace smoothed): an interferer often sits where the clean signal never peaks
  d_<f>       feature minus its median over the recording (segment interference stands out; a gain
              change cancels because the features are ratios)
Absolute-level features (power_db, noise_floor_db, power relative to the recording median) are used
only in the 'with_level' ablation.

Decision: score = model probability (HistGradientBoosting, calibrated), alert if raw score >= tau, then a
causal k-of-n rule over consecutive windows. Each alert carries a reason in plain words.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = ["peak_to_median_db", "peak_width_hz", "peak_persistence", "max_dev_from_obs_median_db", "flatness",
        "spectral_entropy", "spectral_kurtosis", "occupied_bw_hz"]
OBS_REL = ["peak_to_median_db", "flatness", "spectral_entropy", "spectral_kurtosis", "max_dev_from_obs_median_db"]
LEVEL = ["power_db", "noise_floor_db", "d_power_db"]
MAIN = BASE + [f"tz_{f}" for f in BASE] + ["peak_rarity"] + [f"d_{f}" for f in OBS_REL]
GROUPS = {   # feature groups for the ablations (rule 8)
    "peak": ["peak_to_median_db", "peak_width_hz", "peak_persistence", "tz_peak_to_median_db", "tz_peak_width_hz",
             "tz_peak_persistence", "d_peak_to_median_db"],
    "shape": ["flatness", "spectral_entropy", "spectral_kurtosis", "occupied_bw_hz", "tz_flatness",
              "tz_spectral_entropy", "tz_spectral_kurtosis", "tz_occupied_bw_hz", "d_flatness",
              "d_spectral_entropy", "d_spectral_kurtosis"],
    "deviation": ["max_dev_from_obs_median_db", "tz_max_dev_from_obs_median_db", "d_max_dev_from_obs_median_db"],
    "template": [f"tz_{f}" for f in BASE] + ["peak_rarity"],
    "obs_relative": [f"d_{f}" for f in OBS_REL],
}
RARITY_BIN_HZ = 100.0
DEFAULT_MODEL = Path("models/interference/model.pkl")


# ------------------------------------------------------------------------------------------- template
def fit_template(clean: pd.DataFrame) -> dict:
    """Per-satellite template from TRAIN clean windows (columns norad + BASE + peak_freq_hz)."""
    out = {}
    for key, g in list(clean.groupby("norad")) + [("pooled", clean)]:
        med = g[BASE].median()
        mad = (g[BASE] - med).abs().median() * 1.4826
        scale = np.maximum(mad.to_numpy(), 1e-6 + 0.05 * g[BASE].std().fillna(0).to_numpy())
        bins = np.floor(g["peak_freq_hz"].to_numpy() / RARITY_BIN_HZ).astype(int)
        u, c = np.unique(bins, return_counts=True)
        out[str(key)] = {"median": med.round(6).to_dict(), "scale": dict(zip(BASE, np.round(scale, 6).tolist())),
                         "peak_bins": {str(int(b)): int(n) for b, n in zip(u, c)}, "n": int(len(g)),
                         "n_bins": int(np.ceil(24000 / RARITY_BIN_HZ))}
    return out


def derive(win: pd.DataFrame, norad: int | None, template: dict) -> pd.DataFrame:
    """Model features of the windows of ONE recording (rows in time order)."""
    t = template.get(str(norad), template["pooled"])
    out = pd.DataFrame(index=win.index)
    for f in BASE:
        out[f] = win[f].astype(float)
        out[f"tz_{f}"] = (win[f] - t["median"][f]) / t["scale"][f]
    bins = np.floor(win["peak_freq_hz"].to_numpy() / RARITY_BIN_HZ).astype(int).astype(str)
    cnt = np.array([t["peak_bins"].get(b, 0) for b in bins], dtype=float)
    out["peak_rarity"] = -np.log10((cnt + 1) / (t["n"] + t["n_bins"]))
    for f in OBS_REL:
        out[f"d_{f}"] = win[f] - win[f].median()
    out["power_db"], out["noise_floor_db"] = win["power_db"], win["noise_floor_db"]
    out["d_power_db"] = win["power_db"] - win["power_db"].median()
    return out


def derive_many(win: pd.DataFrame, template: dict, rec_cols=("obs_id", "variant_id")) -> pd.DataFrame:
    """derive() per recording for a table holding several recordings (column norad required)."""
    parts = [derive(g, int(g["norad"].iloc[0]), template) for _, g in win.groupby(list(rec_cols), sort=False)]
    return pd.concat(parts).loc[win.index]


# ------------------------------------------------------------------------------------------- decision
def k_of_n(alert: np.ndarray, k: int, n: int) -> np.ndarray:
    """Causal rule: window i alerts if >= k of windows i-n+1..i are above threshold."""
    a = np.asarray(alert, dtype=int)
    if n <= 1:
        return a.astype(bool)
    c = np.convolve(a, np.ones(n, dtype=int))[:len(a)]
    return c >= k


def k_of_n_many(alert: np.ndarray, rec: np.ndarray, k: int, n: int) -> np.ndarray:
    """k_of_n per recording; rows of a recording are contiguous and in time order."""
    out = np.zeros(len(alert), dtype=bool)
    _, start = np.unique(rec, return_index=True)
    edges = np.append(np.sort(start), len(rec))
    for a, b in zip(edges[:-1], edges[1:]):
        out[a:b] = k_of_n(alert[a:b], k, n)
    return out


def reason(row: pd.Series, feat: pd.Series, template_sat: dict) -> str:
    """Plain-words reason for one alerted window."""
    med = template_sat["median"]
    parts = [f"strongest line at {row['peak_freq_hz']:.0f} Hz is {row['peak_to_median_db']:.1f} dB above the band "
             f"median (clean passes: {med['peak_to_median_db']:.1f} dB), {row['peak_width_hz']:.0f} Hz wide, "
             f"same frequency for {int(row['peak_persistence'])} previous windows"]
    if feat["peak_rarity"] >= 2.5:
        parts.append("the clean passes of this satellite almost never peak at this frequency")
    tz = {f: feat[f"tz_{f}"] for f in ["flatness", "spectral_entropy", "spectral_kurtosis", "max_dev_from_obs_median_db"]}
    f, z = max(tz.items(), key=lambda kv: abs(kv[1]))
    if abs(z) >= 3:
        parts.append(f"{f} = {row[f]:.3g} is {z:+.1f} robust SD from the clean template")
    return "; ".join(parts)


class Detector:
    """Frozen interference detector (models/interference/model.pkl)."""

    def __init__(self, bundle: dict):
        self.b = bundle

    @classmethod
    def load(cls, path: Path = DEFAULT_MODEL) -> "Detector":
        with open(path, "rb") as fh:
            return cls(pickle.load(fh))

    def raw_score(self, feats: pd.DataFrame) -> np.ndarray:
        return self.b["model"].predict_proba(feats[self.b["features"]].to_numpy(np.float32))[:, 1]

    def calibrate(self, s: np.ndarray) -> np.ndarray:
        c = self.b["calibration"]
        if c["method"] == "isotonic":
            return c["iso"].predict(s)
        return 1.0 / (1.0 + np.exp(-(c["a"] * s + c["b"])))

    def run(self, win: pd.DataFrame, norad: int | None) -> dict:
        """Score the windows of one recording. Returns per-window results and alert events with reasons."""
        win = win.reset_index(drop=True)
        tmpl = self.b["template"].get(str(norad), self.b["template"]["pooled"])
        feats = derive(win, norad, self.b["template"])
        s = self.raw_score(feats)
        p = self.calibrate(s)
        th = self.b["threshold"]
        raw_alert = s >= th["tau"]
        alert = k_of_n(s >= th["tau_smoothed"], th["k"], th["n"])
        hop = float(np.median(np.diff(win["t_start_s"]))) if len(win) > 1 else 0.5
        events, i = [], 0
        while i < len(win):
            if not alert[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(win) and alert[j + 1]:
                j += 1
            top = i + int(np.argmax(s[i:j + 1]))
            events.append({"t_start_s": float(win.loc[i, "t_start_s"]), "t_end_s": float(win.loc[j, "t_start_s"] + 2 * hop),
                           "n_windows": int(j - i + 1), "max_probability": round(float(p[i:j + 1].max()), 4),
                           "peak_freq_hz": float(win.loc[top, "peak_freq_hz"]),
                           "reason": "narrowband interference: " + reason(win.loc[top], feats.loc[top], tmpl)})
            i = j + 1
        return {"detector": "interference", "n_windows": int(len(win)), "tau": th["tau"],
                "smoothing": f"{th['k']} of {th['n']}", "n_alert_windows": int(alert.sum()),
                "n_raw_alert_windows": int(raw_alert.sum()), "events": events,
                "windows": {"t_start_s": win["t_start_s"].round(3).tolist(), "score": np.round(s, 5).tolist(),
                            "probability": np.round(p, 5).tolist(), "alert": alert.astype(int).tolist()}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Interference detector on the windows.csv of one recording "
                                             "(for a raw recording folder use detect/run.py)")
    ap.add_argument("--windows", required=True, help="windows.csv (detect/features.py columns)")
    ap.add_argument("--variant", default=None, help="variant_id to select if the file holds several recordings")
    ap.add_argument("--norad", type=int, default=None)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--out", default=None, help="JSON output (default: stdout)")
    ap.add_argument("--full", action="store_true", help="include per-window scores")
    a = ap.parse_args(argv)
    win = pd.read_csv(a.windows)
    if a.variant:
        win = win[win["variant_id"] == a.variant]
    res = Detector.load(Path(a.model)).run(win.sort_values("t_start_s"), a.norad)
    if not a.full:
        res.pop("windows")
    txt = json.dumps(res, indent=1)
    Path(a.out).write_text(txt + "\n", encoding="utf-8") if a.out else print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
