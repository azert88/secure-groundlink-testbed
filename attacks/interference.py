"""Narrowband interference (docs/DATASET_SPEC.md 6.4, decision 56).

Reference powers of the clean observation (in-band = [f_low, f_edge], decision 48):
  P_ref   = median in-band power over ALL windows        snr_db = 10 log10(P_ref / P_interf)
  P_noise = mean in-band power over non-burst windows     inr_db = 10 log10(P_interf / P_noise)
            (all windows when burst_active is unknown, e.g. timing-unreliable observations)
Shapes: tone, A = sqrt(2 P_interf), random phase, optional linear drift (Hz/s);
        Gaussian noise through a 4th-order Butterworth band-pass of width bw_hz, scaled so its
        mean power over the active samples is P_interf exactly.
Active time: union of `segments` [[t0, t1], ...] (one segment, or on/off segments for the
intermittent evasion), 10 ms raised-cosine edges. Generated in chunks (bounded memory).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attacks.common import raised_cosine  # noqa: E402
from detect.features import window_spectra  # noqa: E402

CHUNK = 1 << 20


def reference_powers(x: np.ndarray, sr: int, p: dict, f_low: float, f_edge: float, f_top: float,
                     intervals: list | None = None) -> dict:
    """P_ref, P_noise and the clean median PSD over [f_low, f_top] (for the 'strongest' placement)."""
    powers, t_start, med_parts, freqs = [], [], [], None
    for freqs, idx, psd, _ in window_spectra(x, sr, p):
        band = (freqs >= f_low) & (freqs <= f_edge)
        powers.append(psd[:, band].sum(axis=1) * (freqs[1] - freqs[0]))
        t_start.append(idx / sr)
        med_parts.append(psd[:, (freqs >= f_low) & (freqs <= f_top)].astype(np.float32))
    pw, t = np.concatenate(powers), np.concatenate(t_start)
    if intervals is not None:
        act = np.zeros(len(t), dtype=bool)
        for a, b in intervals:
            act |= (t < b) & (t + p["win_s"] > a)
        quiet = pw[~act] if (~act).any() else pw
    else:
        quiet = pw
    top = (freqs >= f_low) & (freqs <= f_top)
    return {"p_ref": float(np.median(pw)), "p_noise": float(np.mean(quiet)), "n_windows": len(pw),
            "freqs": freqs[top], "median_psd": np.median(np.concatenate(med_parts), axis=0)}


def gate(n: int, sr: int, segments, fade_s: float = 0.01) -> list[tuple[int, int, np.ndarray | None]]:
    """Sample ranges (i0, i1, ramp) of the active segments; ramp marks 10 ms edges."""
    out = []
    f = max(1, int(fade_s * sr))
    for a, b in segments:
        i0, i1 = max(0, int(round(a * sr))), min(n, int(round(b * sr)))
        if i1 - i0 > 2 * f:
            out.append((i0, i1, raised_cosine(f)))
    return out


def interferer_segments(n: int, sr: int, op: dict, p_interf: float, rng) -> list[tuple[int, np.ndarray]]:
    """(start sample, float32 waveform) per active segment, scaled to p_interf."""
    pieces = []
    for i0, i1, ramp in gate(n, sr, op["segments"]):
        m = i1 - i0
        z = np.empty(m, dtype=np.float32)
        if op["shape"] == "tone":
            a = math.sqrt(2 * p_interf)
            t_off = i0 / sr - op["segments"][0][0]          # drift/phase continue across on/off segments
            for k in range(0, m, CHUNK):
                t = t_off + (np.arange(k, min(m, k + CHUNK)) / sr)
                ph = op["phase"] + 2 * np.pi * (op["f0_hz"] * t + 0.5 * op.get("drift_hz_s", 0.0) * t * t)
                z[k:k + CHUNK] = a * np.sin(ph)
        else:
            sos = butter(4, [op["f0_hz"] - op["bw_hz"] / 2, op["f0_hz"] + op["bw_hz"] / 2], btype="band",
                         fs=sr, output="sos")
            zi = sosfilt_zi(sos) * 0.0
            warm = rng.standard_normal(sr)                     # settle the filter before the segment
            _, zi = sosfilt(sos, warm, zi=zi)
            for k in range(0, m, CHUNK):
                seg, zi = sosfilt(sos, rng.standard_normal(min(m, k + CHUNK) - k), zi=zi)
                z[k:k + CHUNK] = seg
        f = len(ramp)
        z[:f] *= ramp
        z[-f:] *= ramp[::-1]
        pieces.append((i0, z))
    if op["shape"] != "tone" and pieces:                       # exact power over the active samples
        tot = sum(float(np.dot(z.astype(np.float64), z)) for _, z in pieces)
        cnt = sum(len(z) for _, z in pieces)
        s = math.sqrt(p_interf / (tot / cnt))
        pieces = [(i0, z * np.float32(s)) for i0, z in pieces]
    return pieces


def resolve_f0(op: dict, ref: dict) -> float:
    """'strongest' placement: frequency of the strongest clean signal energy that keeps the band inside."""
    if op["f0_hz"] != "strongest":
        return float(op["f0_hz"])
    half = (op.get("bw_hz") or 0.0) / 2
    f, med = ref["freqs"], ref["median_psd"]
    ok = (f >= f[0] + 100 + half) & (f <= f[-1] - half)
    return float(f[ok][np.argmax(med[ok])])


def apply_interference(x: np.ndarray, sr: int, op: dict, ref: dict, rng) -> tuple[np.ndarray, dict]:
    """Add one interferer. Returns (new audio, event)."""
    op = {**op, "f0_hz": resolve_f0(op, ref)}
    p_interf = ref["p_ref"] / 10 ** (op["snr_db"] / 10)
    y = x.copy()
    for i0, z in interferer_segments(len(x), sr, op, p_interf, rng):
        y[i0:i0 + len(z)] += z
    event = {"attack_type": "interference", "subtype": op["subtype"], "is_attack": 1,
             "evasion": int(op.get("evasion", 0)), "segments": op["segments"], "snr_db": op["snr_db"],
             "inr_db": 10 * math.log10(p_interf / ref["p_noise"]), "f0_hz": op["f0_hz"],
             "bw_hz": op.get("bw_hz"), "shape": op["shape"], "drift_hz_s": op.get("drift_hz_s", 0.0),
             "p_interf": p_interf}
    return y, event
