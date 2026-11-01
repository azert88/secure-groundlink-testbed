"""Tests for the Phase L interference detector, its audio front end and the run.py prototype."""

import pickle

import numpy as np
import pandas as pd
import pytest
import soundfile as sf
from sklearn.ensemble import HistGradientBoostingClassifier

from detect import audio_front as af
from detect.features import window_features
from detect.interference_det import BASE, MAIN, Detector, derive, fit_template, k_of_n, k_of_n_many

SR = 48000
PSD = {"win_s": 1.0, "hop_s": 0.5, "nperseg": 2048, "noverlap": 1024, "f_low_hz": 100.0, "drop_smooth_hz": 1000.0,
       "drop_min_hz": 1000.0, "chunk_windows": 40}


def noise_audio(seconds=20, tone_hz=None, tone_amp=0.0, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 0.05, SR * seconds)
    # band-limit to ~12 kHz so the front end finds a band edge
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(len(x), 1 / SR) > 12000] *= 1e-3
    x = np.fft.irfft(X, len(x))
    if tone_hz:
        t = np.arange(len(x)) / SR
        x = x + tone_amp * np.sin(2 * np.pi * tone_hz * t) * (t >= seconds / 2)
    return x.astype(np.float32)


def windows(x):
    w = window_features(x, SR, PSD, 100.0, 11800.0)
    return pd.DataFrame({"t_start_s": w["t_start_s"], **{k: w[k] for k in w if k != "t_start_s"}})


def test_k_of_n_is_causal():
    a = np.array([0, 1, 0, 1, 1, 0, 0, 1])
    assert k_of_n(a, 2, 3).astype(int).tolist() == [0, 0, 0, 1, 1, 1, 0, 0]
    assert k_of_n(a, 1, 1).astype(int).tolist() == a.tolist()
    rec = np.array([0, 0, 0, 1, 1, 1, 1, 1])
    # recordings do not share neighbours
    assert k_of_n_many(np.array([1, 1, 0, 1, 0, 0, 0, 0]), rec, 2, 3).astype(int).tolist() == [0, 1, 1, 0, 0, 0, 0, 0]


def test_derive_is_gain_invariant_and_template_based():
    w = windows(noise_audio(tone_hz=3000, tone_amp=0.05))
    w["norad"] = 1
    tmpl = fit_template(windows(noise_audio(seed=1)).assign(norad=1))
    f1 = derive(w, 1, tmpl)
    w2 = windows(3.0 * noise_audio(tone_hz=3000, tone_amp=0.05))
    f2 = derive(w2, 1, tmpl)
    assert np.allclose(f1[MAIN].to_numpy(), f2[MAIN].to_numpy(), atol=1e-3)     # a gain change cancels
    assert not np.allclose(f1["power_db"], f2["power_db"])                        # level features do not
    half = len(w) // 2
    assert f1["tz_peak_to_median_db"].iloc[half + 4:].min() > 5                  # tone: far from the template
    assert f1["peak_rarity"].iloc[half + 4:].min() >= f1["peak_rarity"].iloc[:half - 2].median()  # never-seen bin
    assert derive(w, 999, tmpl).shape == f1.shape                                # unknown satellite: pooled


def test_front_end_matches_dataset_pipeline():
    import qc
    import build_tables
    x = noise_audio()
    f, p = af.window_psds(x, SR, PSD)
    f0, p0 = qc.window_psds(x, SR, PSD)
    assert np.array_equal(p, p0)
    med = np.median(10 * np.log10(p + 1e-30), axis=0)
    assert af.steepest_drop(f, med, PSD) == qc.steepest_drop(f0, med, PSD)
    ch = {"headroom": 0.5, "vorbis_compression_level": 0.4}
    assert np.array_equal(af.common_chain(x[:SR * 3], SR, ch), build_tables.common_chain(x[:SR * 3], SR, ch))


@pytest.fixture(scope="module")
def toy_bundle():
    """A tiny model trained on synthetic tone / no-tone windows (only to exercise the pipeline)."""
    tmpl = fit_template(windows(noise_audio(seed=1)).assign(norad=1))
    rows, ys = [], []
    for seed in range(4):
        w = windows(noise_audio(tone_hz=2000 + 500 * seed, tone_amp=0.05, seed=seed + 10))
        rows.append(derive(w, 1, tmpl))
        ys.append((w["t_start_s"] >= 10.5).to_numpy())
    Xf, y = pd.concat(rows)[MAIN].to_numpy(np.float32), np.concatenate(ys)
    m = HistGradientBoostingClassifier(max_iter=30, random_state=0).fit(Xf, y)
    return {"model": m, "features": MAIN, "template": tmpl, "calibration": {"method": "platt", "a": 1.0, "b": 0.0},
            "threshold": {"tau": 0.5, "tau_strict": 0.9, "tau_smoothed": 0.5, "k": 2, "n": 3}}


def test_detector_alerts_with_reason(toy_bundle):
    det = Detector(toy_bundle)
    res = det.run(windows(noise_audio(tone_hz=4000, tone_amp=0.05, seed=99)), 1)
    assert res["events"], "tone should raise an event"
    ev = res["events"][0]
    assert ev["t_start_s"] >= 9.0 and abs(ev["peak_freq_hz"] - 4000) < 50
    assert "narrowband interference" in ev["reason"] and "Hz" in ev["reason"]
    quiet = det.run(windows(noise_audio(seed=98)), 1)
    assert quiet["n_alert_windows"] <= 1


def test_run_py_on_a_raw_folder(tmp_path, toy_bundle):
    import yaml
    from detect.run import run
    d = tmp_path / "obs"
    d.mkdir()
    x = noise_audio(tone_hz=4000, tone_amp=0.05, seed=99)
    with sf.SoundFile(d / "audio.ogg", "w", SR, 1, format="OGG", subtype="VORBIS") as fh:
        for i in range(0, len(x), 4096):      # small blocks: one large Vorbis write crashes libsndfile on Windows
            fh.write(x[i:i + 4096])
    (d / "metadata.json").write_text('{"norad_cat_id": 1}', encoding="utf-8")
    mp = tmp_path / "model.pkl"
    mp.write_bytes(pickle.dumps(toy_bundle))
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    res = run(d, cfg, mp)
    r = res["detectors"]["interference"]
    assert res["norad"] == 1 and res["alert"] and r["events"]
    assert "windows" not in r and r["runtime_s"] >= 0
    assert set(BASE) <= set(toy_bundle["features"])
