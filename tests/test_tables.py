"""Tests for Phase D tables (frames ordering / timing rules, chain, window features)."""

import math
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.signal import welch

from attacks.common import burst_intervals
from build_tables import common_chain, frame_rows
from detect.features import burst_active, window_features, window_spectra

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
P = {"win_s": 1.0, "hop_s": 0.5, "nperseg": 2048, "noverlap": 1024, "chunk_windows": 7}


def frames_dir(tmp_path, specs):
    """specs: (seconds, counter, name) -> attack-ready frame dicts; the payload's first byte is the counter."""
    return [{"filename": name, "t": float(sec), "hex": (bytes([ctr % 256]) * 8).hex(), "decoder_branches": 1,
             "n_copies": 0, "copy_delays_s": [], "is_attack": 0, "attack_type": "", "subtype": "", "evasion": 0}
            for sec, ctr, name in specs]


def first_byte(data):
    return None if data[0] == 0 else data[0]


def test_frame_rows_bursts_and_deltas(tmp_path):
    fr = frames_dir(tmp_path, [(10, 1, "a"), (11, 2, "b"), (40, 3, "c"), (41, 4, "d"), (70, 5, "e")])
    rows = frame_rows(fr, T0, True, False, 2.0, first_byte)
    assert [r["burst_id"] for r in rows] == [0, 0, 1, 1, 2]
    assert math.isnan(rows[0]["dt_prev_s"]) and rows[1]["dt_prev_s"] == 1.0
    assert math.isnan(rows[1]["dt_burst_s"]) and rows[2]["dt_burst_s"] == 30.0 and rows[4]["dt_burst_s"] == 30.0
    assert [r["idx"] for r in rows] == list(range(5)) and rows[0]["hex"] == "01" * 8
    assert rows[1]["timestamp_utc"] == "2026-10-01T00:00:11Z" and rows[1]["counter"] == 2
    assert burst_intervals(rows, 1.0) == [(9.0, 12.0), (39.0, 42.0), (69.0, 71.0)]


def test_unreliable_timing_nan_and_counter_order(tmp_path):
    # batched writes: all files in one second, file order differs from counter order
    fr = frames_dir(tmp_path, [(5, 30, "f0"), (5, 10, "f1"), (5, 20, "f2"), (5, 0, "f3")])  # f3: no counter
    rows = frame_rows(fr, T0, False, True, 2.0, first_byte)
    assert [r["counter"] for r in rows] == [10, 20, 30, None]
    assert all(math.isnan(r["dt_prev_s"]) and math.isnan(r["dt_burst_s"]) for r in rows)
    assert [r["idx"] for r in rows] == [0, 1, 2, 3]


def test_common_chain_is_deterministic():
    x = (0.1 * np.random.default_rng(0).standard_normal(48000 * 3)).astype(np.float32)
    chain = {"headroom": 0.5, "vorbis_compression_level": 0.4}
    a, b = common_chain(x, 48000, chain), common_chain(x, 48000, chain)
    assert np.array_equal(a, b) and len(a) == len(x)
    assert abs(np.std(a) / np.std(x) - 0.5) < 0.05


def test_window_psd_matches_scipy_welch():
    sr = 48000
    x = np.random.default_rng(1).standard_normal(4 * sr)
    freqs, idx, psd, sk = next(window_spectra(x, sr, P))
    _, ref = welch(x[idx[3]:idx[3] + sr], fs=sr, window="hann", nperseg=2048, noverlap=1024)
    assert np.allclose(psd[3], ref, rtol=1e-6)
    assert abs(np.median(sk[3, 10:-10])) < 0.5          # Gaussian noise: SK about 0


def test_features_tone_gain_invariance_and_persistence():
    sr = 48000
    rng = np.random.default_rng(2)
    t = np.arange(10 * sr) / sr
    x = rng.standard_normal(len(t)) + 3 * np.sin(2 * np.pi * 3000 * t)
    f1 = window_features(x, sr, P, 100.0, 10000.0)
    f2 = window_features(4.0 * x, sr, P, 100.0, 10000.0)
    assert np.all(np.abs(f1["peak_freq_hz"] - 3000) < 30)
    assert np.all(f1["peak_width_hz"] <= 100)
    assert np.all(f1["peak_to_median_db"] > 20)
    for k in ("peak_to_median_db", "flatness", "spectral_entropy", "max_dev_from_obs_median_db"):
        assert np.allclose(f1[k], f2[k], atol=1e-6), k
    assert np.allclose(f2["power_db"] - f1["power_db"], 20 * np.log10(4), atol=1e-6)
    assert list(f1["peak_persistence"][:4]) == [0, 1, 2, 3]
    # spectral kurtosis of a pure tone is about -1, of noise about 0: the tone bin dominates the max anyway
    assert f1["spectral_kurtosis"].shape == f1["t_start_s"].shape


def test_burst_active():
    t = np.array([0.0, 0.5, 1.0, 5.0])
    assert list(burst_active(t, 1.0, [(1.2, 2.0)])) == [0, 1, 1, 0]
