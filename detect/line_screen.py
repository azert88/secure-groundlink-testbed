"""Narrowband line screen (Phase C screen, decisions 43, 55), used by fusion as a second interference evidence.

It learns nothing. On the window PSDs of ONE recording:
  1. each bin is normalized per window by the median PSD over +-baseline_hz around it, excluding
     +-baseline_exclude_hz (local baseline, so the satellite's own band does not hide a line);
  2. a bin is "hot" in a window if it exceeds +line_threshold_db; a bin is persistent if it is hot in more
     than line_min_frac_windows of the windows (aimed at long and full-pass interference);
  3. contiguous persistent bins form a run; its width is measured at -3 dB from the peak of the
     time-averaged normalized profile; only runs <= line_max_width_hz flag;
  4. lines inside the satellite's downlink region learned from TRAIN observations (+- signal_band_margin_hz)
     are tagged in_signal_band and do not flag.
Band: the detectors' feature band [f_low, f_drop - margin] (decision 48), so no audible part is blind.
Copies of data/qc.py local_baseline_norm / runs / width_3db (the prototype does not import the data pipeline);
tests/test_line_screen.py checks they agree with the originals.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def local_baseline_norm(sub_db: np.ndarray, df: float, p: dict) -> np.ndarray:
    """Same result as data/qc.py local_baseline_norm; interior bins use np.median (no NaN padding there), only
    the bins within baseline_hz of the band edges need nanmedian (5-10x faster)."""
    k_out = int(round(p["baseline_hz"] / df))
    k_in = int(p["baseline_exclude_hz"] // df)
    offsets = [k for k in range(-k_out, k_out + 1) if abs(k) > k_in]
    n_win, n_bin = sub_db.shape
    out = np.empty_like(sub_db)
    inner = slice(k_out, max(k_out, n_bin - k_out))
    edge = np.r_[0:min(k_out, n_bin), max(k_out, n_bin - k_out):n_bin]
    for s in range(0, n_win, p["chunk_windows"]):
        block = sub_db[s:s + p["chunk_windows"]]
        padded = np.pad(block, ((0, 0), (k_out, k_out)), constant_values=np.nan)
        res = np.empty_like(block)
        n_in = inner.stop - inner.start
        if n_in > 0:
            neigh = np.stack([padded[:, 2 * k_out + k:2 * k_out + k + n_in] for k in offsets])
            res[:, inner] = np.median(neigh, axis=0)
        if len(edge):
            neigh = np.stack([padded[:, k_out + k + edge] for k in offsets])
            res[:, edge] = np.nanmedian(neigh, axis=0)
        out[s:s + p["chunk_windows"]] = block - res
    return out


def estimate_f_edge(freqs: np.ndarray, psd_db: np.ndarray, p: dict, max_iter: int = 20) -> tuple[float, float]:
    """Copy of data/qc.py estimate_f_edge: highest frequency whose median PSD is within edge_drop_db of the in-band
    median, searched below the steepest drop f_drop. Returns (f_edge, f_drop)."""
    from detect.audio_front import steepest_drop
    med = np.median(psd_db, axis=0)
    f_drop = steepest_drop(freqs, med, p)
    above_low = (freqs >= p["f_low_hz"]) & (freqs <= f_drop)
    edge = f_drop
    for _ in range(max_iter):
        ref = np.median(med[above_low & (freqs <= edge)])
        ok = np.where(above_low & (med >= ref - p["edge_drop_db"]))[0]
        new_edge = float(freqs[ok[-1]]) if len(ok) else f_drop
        if new_edge == edge:
            break
        edge = new_edge
    return edge, f_drop


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(edges == 1)[0], np.where(edges == -1)[0]))


def width_3db(profile_db: np.ndarray, a: int, b: int) -> tuple[int, int]:
    i = a + int(np.argmax(profile_db[a:b]))
    thr = profile_db[i] - 3.0
    lo, hi = i, i
    while lo > 0 and profile_db[lo - 1] >= thr:
        lo -= 1
    while hi < len(profile_db) - 1 and profile_db[hi + 1] >= thr:
        hi += 1
    return lo, hi


def screen(freqs: np.ndarray, psd: np.ndarray, t_start: np.ndarray, band: tuple[float, float], p: dict,
           downlink: list | None = None) -> dict:
    """Line screen of one recording. psd: linear window PSDs [n_windows, n_bins] (Welch, as data/qc.py)."""
    lo_b, hi_b = band
    sel = (freqs >= lo_b) & (freqs <= hi_b)
    f_band = freqs[sel]
    df = float(freqs[1] - freqs[0])
    if psd.shape[0] == 0 or sel.sum() < 3:
        return {"line_flag": False, "lines": [], "n_windows": int(psd.shape[0]), "band_hz": [lo_b, hi_b]}
    sub = 10 * np.log10(psd[:, sel].astype(np.float64) + 1e-30)
    norm = local_baseline_norm(sub, df, p)
    hot_w = norm > p["line_threshold_db"]
    frac = hot_w.mean(axis=0)
    profile = 10 * np.log10(np.mean(10 ** (norm / 10), axis=0))
    m = p["signal_band_margin_hz"]
    lines = []
    for a, b in runs(frac > p["line_min_frac_windows"]):
        lo3, hi3 = width_3db(profile, a, b)
        lo, hi = float(f_band[lo3]), float(f_band[hi3])
        width = hi - lo + df
        narrow = width <= p["line_max_width_hz"]
        in_sig = narrow and any(d_lo - m <= lo and hi <= d_hi + m for d_lo, d_hi in (downlink or []))
        win_hot = hot_w[:, a:b].any(axis=1)
        lines.append({"f_lo_hz": round(lo, 1), "f_hi_hz": round(hi, 1), "width_hz": round(width, 1),
                      "peak_db": round(float(profile[a:b].max()), 2), "frac_windows": round(float(frac[a:b].max()), 4),
                      "narrow": bool(narrow), "in_signal_band": bool(in_sig),
                      "flag": bool(narrow and not in_sig),
                      "hot_windows": np.flatnonzero(win_hot).tolist() if narrow else []})
    flagged = [l for l in lines if l["flag"]]
    return {"line_flag": bool(flagged), "n_flag_lines": len(flagged), "n_windows": int(psd.shape[0]),
            "max_frac_windows": round(float(frac.max()), 4), "band_hz": [round(lo_b, 1), round(hi_b, 1)],
            "downlink": downlink or [], "lines": lines, "t_start_s": np.round(t_start, 3).tolist()}


def reason(line: dict, duration_s: float) -> str:
    return (f"narrowband line at {line['f_lo_hz']:.0f}-{line['f_hi_hz']:.0f} Hz ({line['width_hz']:.0f} Hz wide at -3 dB), "
            f"{line['peak_db']:.1f} dB above its local baseline on average, more than +10 dB in "
            f"{100 * line['frac_windows']:.0f} % of the {duration_s:.0f} s recording, outside the satellite's downlink band")


def screen_audio(y: np.ndarray, sr: int, band: tuple[float, float], qc: dict, downlink: list | None) -> dict:
    """Line screen on (chained) audio samples; PSD estimator as data/qc.py window_psds."""
    from detect.audio_front import window_psds
    p = {**qc["psd"], **{k: qc[k] for k in ("line_threshold_db", "line_min_frac_windows", "line_max_width_hz",
                                             "signal_band_margin_hz")}}
    freqs, psd = window_psds(y, sr, qc["psd"])
    hop = int(qc["psd"]["hop_s"] * sr)
    t = np.arange(psd.shape[0]) * hop / sr
    return screen(freqs, psd, t, band, p, downlink)


def main(argv: list[str] | None = None) -> int:
    import yaml
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from detect.audio_front import common_chain, feature_band
    import soundfile as sf
    ap = argparse.ArgumentParser(description="Narrowband line screen on one audio file")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--fusion", default="models/fusion/fusion.json", help="frozen fusion config (downlink bands)")
    ap.add_argument("--norad", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    dl = []
    if Path(a.fusion).exists() and a.norad is not None:
        dl = json.loads(Path(a.fusion).read_text(encoding="utf-8"))["line_screen"]["downlink"].get(str(a.norad), [])
    x, sr = sf.read(a.audio, dtype="float32")
    x = x.mean(axis=1) if x.ndim > 1 else x
    band = feature_band(x, sr, cfg["qc"]["psd"], cfg["tables"]["feature_band_top_margin_hz"])
    res = screen_audio(common_chain(x, sr, cfg["tables"]["chain"]), sr, band, cfg["qc"], dl)
    txt = json.dumps(res, indent=1)
    Path(a.out).write_text(txt + "\n", encoding="utf-8") if a.out else print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
