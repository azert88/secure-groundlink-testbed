"""Shared pieces of the attack / benign operations (DATASET_SPEC.md section 6).

Frame stream: list of dicts, one per canonical frame, with at least
  t (seconds from observation start, 1 s resolution), hex, filename, counter,
  decoder_branches, n_copies, copy_delays_s, and the label fields of LABEL_KEYS.
finalize_frames() recomputes every derived column (bytes, order, bursts, time deltas) the same
way for clean and attacked streams, so the two cannot be told apart by processing traces.

Audio events: dicts {attack_type, subtype, is_attack, evasion, segments: [[t0, t1], ...], snr_db,
inr_db, ...}; window labels are derived from them (window_labels).
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

LABEL_KEYS = ("is_attack", "attack_type", "subtype", "evasion")


def recipe_seed(obs_id: int, variant_id: str) -> int:
    """Spec 6: seed = int from SHA-256 of f"{obs_id}:{variant_id}" (first 8 bytes)."""
    return int.from_bytes(hashlib.sha256(f"{obs_id}:{variant_id}".encode()).digest()[:8], "big")


def clean_label() -> dict:
    return {"is_attack": 0, "attack_type": "", "subtype": "", "evasion": 0}


def finalize_frames(frames: list[dict], timing_reliable: bool, order_by_counter: bool, gap_s: float,
                    counter_fn) -> list[dict]:
    """Derived columns of a frame stream (new dicts; input untouched).

    Order: by (t, filename); when timing is unreliable and order_by_counter, by counter (frames
    without a counter last) - decision 46. burst_id: new burst when the time gap is > gap_s (on the
    time order). dt_prev_s / dt_burst_s: time since the previous frame / start of this burst minus
    start of the previous burst; NaN when timing is unreliable.
    """
    rows = []
    for f in sorted(frames, key=lambda f: (f["t"], f["filename"])):
        data = bytes.fromhex(f["hex"])
        rows.append({**f, "length": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "counter": counter_fn(data)})
    burst, prev_t, start, prev_start = -1, None, None, None
    for r in rows:
        if prev_t is None or r["t"] - prev_t > gap_s:
            burst += 1
            prev_start, start = start, r["t"]
        r["burst_id"] = burst
        r["dt_prev_s"] = r["t"] - prev_t if prev_t is not None else math.nan
        r["dt_burst_s"] = start - prev_start if prev_start is not None else math.nan
        r["valid"] = int(r["counter"] is not None)
        prev_t = r["t"]
    if not timing_reliable:
        for r in rows:
            r["dt_prev_s"] = r["dt_burst_s"] = math.nan
        if order_by_counter:
            rows.sort(key=lambda r: (r["counter"] is None, r["counter"] if r["counter"] is not None else 0,
                                     r["t"], r["filename"]))
    for i, r in enumerate(rows):
        r["idx"] = i
    return rows


def burst_intervals(rows: list[dict], margin_s: float) -> list[tuple[float, float]]:
    """[first frame - margin, last frame + margin] of every burst (seconds)."""
    by: dict[int, list[float]] = {}
    for r in rows:
        by.setdefault(r["burst_id"], []).append(r["t"])
    return [(min(v) - margin_s, max(v) + margin_s) for v in by.values()]


def raised_cosine(n: int) -> np.ndarray:
    """Rising half of a raised-cosine window, n samples from 0 to 1."""
    return (0.5 - 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / n)).astype(np.float32)


def paste(dst: np.ndarray, src_seg: np.ndarray, start: int, fade: int) -> np.ndarray:
    """Overwrite dst[start:start+len(src_seg)] with src_seg, raised-cosine crossfade of `fade` samples
    at both edges. File length unchanged. Returns a new array."""
    y = dst.copy()
    n = len(src_seg)
    w = np.ones(n, dtype=np.float32)
    f = min(fade, n // 2)
    if f:
        w[:f] = raised_cosine(f)
        w[-f:] = raised_cosine(f)[::-1]
    y[start:start + n] = w * src_seg + (1 - w) * y[start:start + n]
    return y


def segment_overlap(t0: float, t1: float, segments) -> float:
    """Total overlap in seconds of [t0, t1] with a list of [a, b] segments (assumed disjoint)."""
    return float(sum(max(0.0, min(t1, b) - max(t0, a)) for a, b in segments))


def window_labels(t_start: np.ndarray, win_s: float, events: list[dict], min_overlap_s: float = 0.5) -> list[dict]:
    """Spec 4 labels.csv: window label = 1 if an attack is active for >= min_overlap_s of the window.

    overlap_frac is always stored (attack overlap / window length). attack_type / subtype come from
    the event with the largest overlap (attacks before benign); benign events give attack_type
    benign with is_attack 0.
    """
    out = []
    for t in t_start:
        best, best_ov, att_ov = None, 0.0, 0.0
        for e in events:
            ov = segment_overlap(t, t + win_s, e["segments"])
            if ov <= 0:
                continue
            if e["is_attack"]:
                att_ov = max(att_ov, ov)
            key = (e["is_attack"], ov)
            if best is None or key > (best["is_attack"], best_ov):
                best, best_ov = e, ov
        lab = {"is_attack": int(att_ov >= min_overlap_s), "attack_type": "", "subtype": "", "snr_db": math.nan,
               "inr_db": math.nan, "overlap_frac": round(att_ov / win_s, 6), "evasion": 0}
        if best is not None:      # partial attack overlap (< min_overlap_s): type kept, is_attack 0
            lab.update(attack_type=best["attack_type"], subtype=best["subtype"], evasion=int(best.get("evasion", 0)),
                       snr_db=best.get("snr_db", math.nan), inr_db=best.get("inr_db", math.nan))
        out.append(lab)
    return out
