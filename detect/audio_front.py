"""Audio front end of the detectors: raw audio.ogg of ONE recording -> per-window features.

Same steps as the frozen dataset (so the detectors see what they were trained on):
  1. feature band [f_low, f_drop - margin]; f_drop = steepest fall of the median PSD (copies of
     data/qc.py window_psds / steepest_drop, kept here so the prototype does not import the data pipeline;
     tests/test_interference_det.py checks they agree with the originals);
  2. common processing chain (copy of data/build_tables.py common_chain: headroom, Ogg Vorbis round trip);
  3. detect/features.py window_features.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
from scipy.ndimage import median_filter
from scipy.signal import welch

from detect.features import FEATURES, window_features


def window_psds(x: np.ndarray, sr: int, p: dict):
    """Welch PSD (Hann) of win_s windows with hop_s hop. Returns (freqs, psd[n_windows, n_bins])."""
    win, hop = int(p["win_s"] * sr), int(p["hop_s"] * sr)
    starts = np.arange(0, len(x) - win + 1, hop)
    out, freqs = [], None
    for k in range(0, len(starts), p["chunk_windows"]):
        idx = starts[k:k + p["chunk_windows"]]
        block = np.stack([x[s:s + win] for s in idx])
        freqs, pxx = welch(block, fs=sr, window="hann", nperseg=p["nperseg"], noverlap=p["noverlap"], axis=-1)
        out.append(pxx.astype(np.float32))
    return freqs, np.concatenate(out) if out else np.empty((0, 0))


def steepest_drop(freqs: np.ndarray, med_db: np.ndarray, p: dict) -> float:
    """Frequency of the steepest fall of the median PSD (median-filtered over drop_smooth_hz)."""
    k = max(1, int(round(p["drop_smooth_hz"] / (freqs[1] - freqs[0])))) | 1
    slope = np.diff(median_filter(med_db, size=k, mode="nearest"))
    ok = freqs[:-1] >= p["f_low_hz"] + p["drop_min_hz"]
    i = int(np.argmin(np.where(ok, slope, np.inf)))
    return float((freqs[i] + freqs[i + 1]) / 2)


def feature_band(x: np.ndarray, sr: int, psd: dict, top_margin_hz: float) -> tuple[float, float]:
    freqs, pxx = window_psds(x, sr, psd)
    f_drop = steepest_drop(freqs, np.median(10 * np.log10(pxx + 1e-30), axis=0), psd)
    return float(psd["f_low_hz"]), f_drop - top_margin_hz


def common_chain(x: np.ndarray, sr: int, chain: dict) -> np.ndarray:
    """Multiply by the fixed headroom, encode Ogg Vorbis, decode (written in small blocks)."""
    buf = io.BytesIO()
    y = (x * chain["headroom"]).astype(np.float32)
    with sf.SoundFile(buf, "w", sr, 1, format="OGG", subtype="VORBIS",
                      compression_level=chain["vorbis_compression_level"]) as fh:
        for i in range(0, len(y), 4096):
            fh.write(y[i:i + 4096])
    buf.seek(0)
    y, _ = sf.read(buf, dtype="float32")
    return y


def windows_from_audio(path: Path, cfg: dict, chain: bool = True) -> tuple[pd.DataFrame, dict]:
    """Per-window features of one audio file (windows.csv columns, without burst_active)."""
    x, sr = sf.read(str(path), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    psd = cfg["qc"]["psd"]
    lo, hi = feature_band(x, sr, psd, cfg["tables"]["feature_band_top_margin_hz"])
    y = common_chain(x, sr, cfg["tables"]["chain"]) if chain else x
    del x
    w = window_features(y, sr, {**psd, "chunk_windows": cfg["tables"]["features_chunk_windows"]}, lo, hi)
    df = pd.DataFrame({"win_idx": np.arange(len(w["t_start_s"])), **{k: w[k] for k in ["t_start_s"] + FEATURES}})
    return df, {"sample_rate": int(sr), "duration_s": round(len(y) / sr, 3), "band_hz": [lo, round(hi, 2)]}
