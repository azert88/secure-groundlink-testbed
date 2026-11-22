"""jam_v1: power / frame-loss consistency against wideband jamming (S4, W8). Definition: docs/GATES_PHASE2_S4.md A.

Idea (Xu et al., MobiHoc 2005): high received power in the signal band + high frame loss = jamming; loss at low power
= poor propagation. No pilot, no carrier offset, no fingerprint input. Output is a separate field
`jamming_suspected`; the fused verdict is not changed (decision P2-31, Q3).

Inputs (any setting: simulation, bench, SatNOGS):
  frames  list of dicts in expected order: t0, t1 (time span of the expected frame), lost (bool); optional b0, b1
          (the burst's own span, used only by the per-frame ablation).
  blocks  dict of arrays: t0, t1, inband (linear power), gain_db (RX gain to remove; zeros if not applicable).
Per window of `window_frames` expected frames: L = lost / expected, P = time-weighted mean in-band power over the
window span with the gain removed (dB). Candidate: L >= loss_threshold and P >= P0; alert = every window in a run of
>= `consecutive` candidates. P0 = fit_p0 on the lossy benign windows of a threshold set.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

PARAMS = Path(__file__).resolve().parents[1] / "models" / "jam_v1" / "params.json"


def load_params(path: str | Path = PARAMS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def windows(n: int, size: int = 10, min_last: int = 5) -> list[tuple[int, int]]:
    """Non-overlapping [i0, i1) windows of `size` frames from frame 0; a last window shorter than min_last is dropped."""
    out = [(i, min(n, i + size)) for i in range(0, n, size)]
    return [w for w in out if w[1] - w[0] >= min(size, min_last)]


def span_power_db(t0: float, t1: float, blocks: dict, remove_gain: bool = True) -> float:
    """10 log10 of the overlap-weighted mean linear in-band power on [t0, t1], each block referred to the RX input."""
    bt0, bt1 = np.asarray(blocks["t0"], float), np.asarray(blocks["t1"], float)
    w = np.clip(np.minimum(bt1, t1) - np.maximum(bt0, t0), 0.0, None)
    if not np.any(w > 0):
        return float("nan")
    p = np.asarray(blocks["inband"], float)
    if remove_gain:
        p = p * 10.0 ** (-np.asarray(blocks["gain_db"], float) / 10.0)
    return float(10.0 * np.log10(np.sum(w * p) / np.sum(w) + 1e-30))


def window_rows(frames: list[dict], blocks: dict, params: dict, remove_gain: bool = True) -> list[dict]:
    rows = []
    for i0, i1 in windows(len(frames), params["window_frames"], params["min_last_window_frames"]):
        fr = frames[i0:i1]
        lost = [f for f in fr if f["lost"]]
        t0, t1 = fr[0]["t0"], fr[-1]["t1"]
        # ablation (not gated): power over the lost frames' own bursts only
        if lost and all("b0" in f for f in lost):
            num = den = 0.0
            for f in lost:
                p = span_power_db(f["b0"], f["b1"], blocks, remove_gain)
                if not math.isnan(p):
                    d = f["b1"] - f["b0"]
                    num, den = num + d * 10 ** (p / 10), den + d
            p_lost = 10 * math.log10(num / den) if den > 0 else float("nan")
        else:
            p_lost = float("nan")
        rows.append({"i0": i0, "i1": i1, "t0": t0, "t1": t1, "expected": i1 - i0, "lost": len(lost),
                     "L": len(lost) / (i1 - i0), "P": span_power_db(t0, t1, blocks, remove_gain), "P_lost": p_lost})
    return rows


def fit_p0(p_values: list[float], params: dict) -> float:
    """P0 from the P of the lossy benign windows of a threshold set (gate A): quantile, max if < min windows, +inf if none."""
    v = np.asarray([x for x in p_values if not math.isnan(x)], float)
    if len(v) == 0:
        return float("inf")
    if len(v) < params["p0_min_windows"]:
        return float(v.max())
    return float(np.percentile(v, params["p0_quantile"]))


def apply(rows: list[dict], p0: float, params: dict, key: str = "P", loss_threshold: float | None = None) -> list[dict]:
    """Adds 'candidate' and 'jamming_suspected' to each window row of ONE pass (rows in time order)."""
    l0 = params["loss_threshold"] if loss_threshold is None else loss_threshold
    cand = [r["L"] >= l0 and not math.isnan(r[key]) and r[key] >= p0 for r in rows]
    alert = [False] * len(rows)
    i = 0
    while i < len(rows):
        if cand[i]:
            j = i
            while j < len(rows) and cand[j]:
                j += 1
            if j - i >= params["consecutive"]:
                for k in range(i, j):
                    alert[k] = True
            i = j
        else:
            i += 1
    return [{**r, "candidate": c, "jamming_suspected": a} for r, c, a in zip(rows, cand, alert)]


def pass_alert(rows: list[dict]) -> bool:
    return any(r["jamming_suspected"] for r in rows)
