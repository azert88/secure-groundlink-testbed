"""Benign conditions (DATASET_SPEC.md 6.5): is_attack = 0, attack_type = benign.

  gain             multiply the whole file or [t0, t1] by `factor` (10 ms raised-cosine transitions)
  noise_rise       add white noise on [t0, t1] that raises the in-band ([f_low, f_edge]) floor by
                   rise_db: in-band added power = P_noise (10^(rise_db/10) - 1), white over 0..sr/2
  fading           slow amplitude modulation, gain_dB(t) = -depth_db * (1 - cos(2 pi rate t + phase)) / 2
  edge_attenuation gain ramps linearly in dB from -10 dB at the file edges to 0 dB over start_s / end_s
All keep the file length. Every function returns (new audio, event).
"""

from __future__ import annotations

import math

import numpy as np

from attacks.common import raised_cosine

CHUNK = 1 << 20


def _event(subtype: str, segments) -> dict:
    return {"attack_type": "benign", "subtype": subtype, "is_attack": 0, "evasion": 0,
            "segments": [list(s) for s in segments]}


def _envelope_apply(x: np.ndarray, sr: int, fn) -> np.ndarray:
    """y = x * fn(t), evaluated in chunks."""
    y = np.empty_like(x)
    for k in range(0, len(x), CHUNK):
        t = np.arange(k, min(len(x), k + CHUNK)) / sr
        y[k:k + CHUNK] = x[k:k + CHUNK] * fn(t).astype(np.float32)
    return y


def gain(x, sr, op):
    dur = len(x) / sr
    t0, t1 = (0.0, dur) if op.get("t0_s") is None else (op["t0_s"], op["t1_s"])
    i0, i1 = int(round(t0 * sr)), min(len(x), int(round(t1 * sr)))
    y = x.copy()
    g = np.full(i1 - i0, op["factor"], dtype=np.float32)
    f = min(int(0.01 * sr), (i1 - i0) // 2)
    if t0 > 0 and f:
        g[:f] = 1 + (op["factor"] - 1) * raised_cosine(f)
    if i1 < len(x) and f:
        g[-f:] = 1 + (op["factor"] - 1) * raised_cosine(f)[::-1]
    y[i0:i1] *= g
    return y, _event("gain", [[t0, t1]])


def noise_rise(x, sr, op, ref, f_low, f_edge, rng):
    p_add = ref["p_noise"] * (10 ** (op["rise_db"] / 10) - 1)
    sigma = math.sqrt(p_add * (sr / 2) / (f_edge - f_low))
    i0, i1 = int(round(op["t0_s"] * sr)), min(len(x), int(round(op["t1_s"] * sr)))
    y = x.copy()
    f = min(int(0.01 * sr), (i1 - i0) // 2)
    for k in range(i0, i1, CHUNK):
        m = min(i1, k + CHUNK) - k
        z = (sigma * rng.standard_normal(m)).astype(np.float32)
        if k == i0 and f:
            z[:f] *= raised_cosine(f)
        if k + m == i1 and f:
            z[-f:] *= raised_cosine(f)[::-1]
        y[k:k + m] += z
    return y, _event("noise_rise", [[op["t0_s"], op["t1_s"]]])


def fading(x, sr, op):
    fn = lambda t: 10 ** (-op["depth_db"] * 0.5 * (1 - np.cos(2 * np.pi * op["rate_hz"] * t + op["phase"])) / 20)
    return _envelope_apply(x, sr, fn), _event("fading", [[0.0, len(x) / sr]])


def edge_attenuation(x, sr, op):
    dur = len(x) / sr
    a, b = op["start_s"], op["end_s"]

    def fn(t):
        db = np.zeros_like(t)
        db = np.where(t < a, -10 * (1 - t / a), db)
        db = np.where(t > dur - b, -10 * (1 - (dur - t) / b), db)
        return 10 ** (db / 20)
    return _envelope_apply(x, sr, fn), _event("edge_attenuation", [[0.0, a], [dur - b, dur]])
