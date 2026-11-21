"""Interference v3 (S3): template, scoring, fallback and enrollment order, on synthetic receiver audio."""

import numpy as np
import pytest

from detect import interference_v3 as V3

SR = 48000
PSD = {"win_s": 1.0, "hop_s": 0.5, "nperseg": 2048, "noverlap": 1024, "f_low_hz": 100.0, "drop_smooth_hz": 1000.0,
       "drop_min_hz": 1000.0, "chunk_windows": 200}
P = {"k_max": 3, "k_min": 1, "sigma_min_db": 1.0, "smooth_bins": 3, "band_top_margin_hz": 200.0, "k": 3, "n": 5}
TAU = 5.0


def receiver_audio(seed: int, seconds: float = 20.0, tone_sir_db: float | None = None, tone_hz: float = 4321.0):
    """White noise band-limited at 10 kHz (the 'receiver'), optional whole-pass tone at a given SIR."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    spec = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    spec[np.fft.rfftfreq(n, 1 / SR) > 10000] = 0                         # receiver audio band edge at 10 kHz
    x = np.fft.irfft(spec, n)
    x = x / x.std() + 1e-2 * rng.standard_normal(n)                       # floor 40 dB below the band
    if tone_sir_db is not None:
        p_ref = np.mean(x ** 2)
        a = np.sqrt(2 * p_ref / 10 ** (tone_sir_db / 10))
        x = x + a * np.sin(2 * np.pi * tone_hz * np.arange(len(x)) / SR + rng.uniform(0, 2 * np.pi))
    return (0.1 * x).astype(np.float32)


def spectra(x):
    freqs, t, db = V3.psd_db_full(x, SR, PSD, chunk_windows=40)
    return {"freqs": freqs, "t": t, "db": db, "f_drop": V3.f_drop_raw(x, SR, PSD)}


@pytest.fixture(scope="module")
def template():
    return V3.fit_template([spectra(receiver_audio(s)) for s in (1, 2, 3)], P, PSD["f_low_hz"])


def test_template_from_three_clean_passes(template):
    assert template["n_passes"] == 3
    lo, hi = template["band_hz"]
    assert lo == 100.0 and 9000.0 < hi < 11000.0          # one band per receiver, from the enrollment f_drop
    assert template["m"].shape == template["sigma"].shape == (int(template["band"].sum()),)
    assert np.all(template["sigma"] >= P["sigma_min_db"])
    assert abs(float(np.median(template["m"]))) < 0.5     # band-median normalized


def test_whole_pass_tone_flagged(template):
    s = spectra(receiver_audio(7, tone_sir_db=10.0))
    t, f = V3.score(s["db"], s["freqs"], template, P)
    alert, t_alert = V3.decide(np.zeros(len(t), bool), t, TAU, P["k"], P["n"])
    assert t_alert[P["k"] - 1:].all() and alert.all() == t_alert.all()
    assert np.all(np.abs(f[t >= TAU] - 4321.0) < 50.0)


def test_clean_pass_not_flagged(template):
    s = spectra(receiver_audio(8))
    t, _ = V3.score(s["db"], s["freqs"], template, P)
    alert, t_alert = V3.decide(np.zeros(len(t), bool), t, TAU, P["k"], P["n"])
    assert not alert.any() and not t_alert.any() and t.max() < TAU


def test_k0_falls_back_to_v2():
    v2 = np.array([0, 1, 1, 1, 0, 0], bool)
    alert, t_alert = V3.decide(v2, None, TAU, P["k"], P["n"])
    assert np.array_equal(alert, v2) and not t_alert.any()
    assert V3.select_enrollment("2026-09-01T00:00:00Z", 99, 25397, []) == []


def test_enrollment_never_uses_a_later_pass():
    arch = [{"obs_id": i, "station": st, "norad": 25397, "start": s, "audio_qc": qc} for i, st, s, qc in [
        (1, 6, "2026-08-01T00:00:00Z", True), (2, 6, "2026-08-02T00:00:00Z", True),
        (3, 6, "2026-08-03T00:00:00Z", False), (4, 6, "2026-08-04T00:00:00Z", True),
        (5, 6, "2026-08-05T00:00:00Z", True), (6, 6, "2026-08-06T00:00:00Z", True),
        (7, 9, "2026-08-04T12:00:00Z", True), (8, 6, "2026-11-03T00:00:00Z", True)]]
    got = V3.select_enrollment("2026-08-05T00:00:00Z", 6, 25397, arch)
    assert [a["obs_id"] for a in got] == [4, 2, 1]        # 5 same time, 6 later, 3 QC fail, 7 other station
    assert all(a["start"] < "2026-08-05T00:00:00Z" for a in got)
    assert [a["obs_id"] for a in V3.select_enrollment("2026-12-01T00:00:00Z", 6, 25397, arch)] == [6, 5, 4]
