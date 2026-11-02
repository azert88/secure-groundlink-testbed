"""Line screen (detect/line_screen.py): agrees with the Phase C originals, flags a persistent tone, respects the
downlink band and the 200 Hz width rule."""

import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
import qc as Q  # noqa: E402
from detect import line_screen as L  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def test_copies_agree_with_phase_c():
    p = CFG["qc"]["psd"]
    rng = np.random.default_rng(1)
    x = rng.normal(size=(250, 400)) * 3
    x[:, 200] += 15
    assert np.allclose(Q.local_baseline_norm(x, 23.4375, p), L.local_baseline_norm(x, 23.4375, p))
    prof = rng.normal(size=300)
    assert Q.width_3db(prof, 10, 40) == L.width_3db(prof, 10, 40)
    m = rng.random(100) > 0.5
    assert Q.runs(m) == L.runs(m)
    freqs = np.arange(1025) * 48000 / 2048
    db = np.where(freqs < 10000, -60.0, -110.0)[None, :] + rng.normal(size=(50, 1025))
    assert Q.estimate_f_edge(freqs, db, p) == L.estimate_f_edge(freqs, db, p)


def _audio(seconds=60, sr=48000, tone=None, frac=1.0, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=seconds * sr).astype(np.float32) * 0.1
    if tone:
        n = int(frac * len(x))
        t = np.arange(n) / sr
        x[:n] += 0.05 * np.sin(2 * np.pi * tone * t).astype(np.float32)
    return x, sr


def test_full_pass_tone_flags_and_noise_does_not():
    x, sr = _audio(tone=3000.0)
    r = L.screen_audio(x, sr, (100.0, 10000.0), CFG["qc"], [])
    assert r["line_flag"] and any(2900 <= l["f_lo_hz"] <= 3000 <= l["f_hi_hz"] + 30 for l in r["lines"] if l["flag"])
    assert r["lines"][0]["hot_windows"]
    x, sr = _audio(seed=2)
    assert not L.screen_audio(x, sr, (100.0, 10000.0), CFG["qc"], [])["line_flag"]


def test_short_segment_is_not_persistent():
    x, sr = _audio(tone=3000.0, frac=0.1)
    assert not L.screen_audio(x, sr, (100.0, 10000.0), CFG["qc"], [])["line_flag"]


def test_downlink_band_is_excluded():
    x, sr = _audio(tone=3000.0)
    r = L.screen_audio(x, sr, (100.0, 10000.0), CFG["qc"], [[2500.0, 3500.0]])
    assert not r["line_flag"] and any(l["in_signal_band"] for l in r["lines"])
