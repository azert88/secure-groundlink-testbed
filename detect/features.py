"""Per-window audio features (docs/DATASET_SPEC.md section 4, windows.csv), shared by tables and detectors.

Windows: win_s long, hop_s apart. Each window holds Welch segments (Hann, nperseg, noverlap,
constant detrend, density scaling: the same estimate as scipy.signal.welch). The per-segment
power spectra also give the spectral kurtosis (Antoni 2006): per bin, the kurtosis over time of
the STFT magnitude, SK = E|X|^4 / (E|X|^2)^2 - 2, summarized as the max over in-band bins.

All features use the feature band [band_lo, band_hi] (decision 48: up to f_drop - 200 Hz).
  power_db                   10 log10 of the band power
  noise_floor_db             10 log10 of the 10th percentile of the band PSD
  peak_to_median_db          strongest bin over the band median (dB ratio, gain-invariant)
  peak_freq_hz               frequency of the strongest bin
  peak_width_hz              -3 dB width of the strongest line (contiguous bins, decision 41)
  flatness                   geometric / arithmetic mean of the band PSD
  spectral_entropy           Shannon entropy of the normalized band PSD / log(n_bins)
  spectral_kurtosis          max over band bins of SK
  occupied_bw_hz             width holding 99 % of the band power (0.5 % .. 99.5 % of the cumulative sum)
  peak_persistence           consecutive previous windows whose peak bin is within +-2 bins
  max_dev_from_obs_median_db max over bins of the window's median-normalized PSD minus the
                             observation's median-normalized PSD (per-bin median over windows);
                             both are normalized by their band median, so a gain change cancels
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import get_window

FEATURES = ["power_db", "noise_floor_db", "peak_to_median_db", "peak_freq_hz", "peak_width_hz", "flatness",
            "spectral_entropy", "spectral_kurtosis", "occupied_bw_hz", "peak_persistence",
            "max_dev_from_obs_median_db"]


def window_spectra(x: np.ndarray, sr: int, p: dict):
    """Yield (window start indices, psd[n, bins], sk[n, bins]) for chunks of windows; also returns freqs."""
    win, hop = int(p["win_s"] * sr), int(p["hop_s"] * sr)
    nper, step = p["nperseg"], p["nperseg"] - p["noverlap"]
    w = get_window("hann", nper).astype(np.float64)
    scale = 1.0 / (sr * np.sum(w ** 2))
    freqs = np.fft.rfftfreq(nper, 1 / sr)
    starts = np.arange(0, len(x) - win + 1, hop)
    for k in range(0, len(starts), p["chunk_windows"]):
        idx = starts[k:k + p["chunk_windows"]]
        block = np.stack([x[s:s + win] for s in idx]).astype(np.float64)
        seg = sliding_window_view(block, nper, axis=1)[:, ::step]           # (n, n_seg, nper)
        seg = (seg - seg.mean(axis=-1, keepdims=True)) * w
        pw = np.abs(np.fft.rfft(seg, axis=-1)) ** 2                          # |X|^2 per segment
        psd = pw.mean(axis=1) * scale
        psd[:, 1:-1 if nper % 2 == 0 else None] *= 2                         # one-sided
        sk = (pw ** 2).mean(axis=1) / np.maximum(pw.mean(axis=1) ** 2, 1e-300) - 2.0
        yield freqs, idx, psd, sk


def peak_width(row_db: np.ndarray, i: int, df: float, drop_db: float = 3.0) -> float:
    """Width of the contiguous run of bins around i within drop_db of the peak."""
    thr = row_db[i] - drop_db
    lo, hi = i, i
    while lo > 0 and row_db[lo - 1] >= thr:
        lo -= 1
    while hi < len(row_db) - 1 and row_db[hi + 1] >= thr:
        hi += 1
    return (hi - lo + 1) * df


def window_features(x: np.ndarray, sr: int, p: dict, band_lo: float, band_hi: float) -> dict:
    """Features of every window. Returns {"t_start_s": array, feature: array, ...}."""
    t_start, psd_db, sk_max, freqs = [], [], [], None
    for freqs, idx, psd, sk in window_spectra(x, sr, p):
        band = (freqs >= band_lo) & (freqs <= band_hi)
        t_start.append(idx / sr)
        psd_db.append(10 * np.log10(psd[:, band] + 1e-30))
        sk_max.append(sk[:, band].max(axis=1))
    if freqs is None:
        return {"t_start_s": np.empty(0), **{f: np.empty(0) for f in FEATURES}}
    band = (freqs >= band_lo) & (freqs <= band_hi)
    fb, df = freqs[band], float(freqs[1] - freqs[0])
    db = np.concatenate(psd_db)
    lin = 10 ** (db / 10)
    n = len(db)
    med = np.median(db, axis=1)
    peak = db.argmax(axis=1)
    out = {"t_start_s": np.concatenate(t_start),
           "power_db": 10 * np.log10(lin.sum(axis=1) * df),
           "noise_floor_db": np.percentile(db, 10, axis=1),
           "peak_to_median_db": db.max(axis=1) - med,
           "peak_freq_hz": fb[peak],
           "peak_width_hz": np.array([peak_width(db[j], peak[j], df) for j in range(n)]),
           "flatness": np.exp(np.log(lin).mean(axis=1)) / lin.mean(axis=1),
           "spectral_kurtosis": np.concatenate(sk_max)}
    q = lin / lin.sum(axis=1, keepdims=True)
    out["spectral_entropy"] = -(q * np.log(q + 1e-300)).sum(axis=1) / np.log(q.shape[1])
    c = np.cumsum(q, axis=1)
    lo_i = (c < 0.005).sum(axis=1)
    hi_i = np.minimum((c < 0.995).sum(axis=1), q.shape[1] - 1)
    out["occupied_bw_hz"] = (hi_i - lo_i + 1) * df
    pers = np.zeros(n, dtype=int)
    for j in range(1, n):            # count back while the earlier peak stays near this window's peak
        k = j - 1
        while k >= 0 and abs(int(peak[k]) - int(peak[j])) <= 2:
            k -= 1
        pers[j] = j - 1 - k
    out["peak_persistence"] = pers
    obs_norm = np.median(db, axis=0)
    obs_norm = obs_norm - np.median(obs_norm)
    out["max_dev_from_obs_median_db"] = ((db - med[:, None]) - obs_norm).max(axis=1)
    return out


def burst_active(t_start_s: np.ndarray, win_s: float, intervals: list[tuple[float, float]]) -> np.ndarray:
    """1 if the window [t, t + win_s] overlaps any interval (a, b), else 0."""
    act = np.zeros(len(t_start_s), dtype=int)
    for a, b in intervals:
        act |= ((t_start_s < b) & (t_start_s + win_s > a)).astype(int)
    return act
