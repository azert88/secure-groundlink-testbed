"""Interference v3.1 (S3, docs/GATES_PHASE2_S3_V31.md): re-encoded band edge and empty bins ignored."""

import numpy as np
import pytest

from detect import interference_v3 as V3
from detect import interference_v31 as V31

SR = 48000
PSD = {"win_s": 1.0, "hop_s": 0.5, "nperseg": 2048, "noverlap": 1024, "f_low_hz": 100.0, "drop_smooth_hz": 1000.0,
       "drop_min_hz": 1000.0, "chunk_windows": 200}
P31 = {"k_max": 3, "k_min": 1, "sigma_min_db": 1.0, "smooth_bins": 3, "band_top_margin_hz": 200.0, "k": 3, "n": 5,
       "band_edge": "chained", "min_level_db": -60.0}
P3 = {**P31, "band_edge": "raw", "min_level_db": None}          # v3 behaviour
TAU = 5.0
RAW_F_DROP = 18600.0                                             # raw audio edge; the re-encoded audio stops at 11 kHz


def reencoded_audio(seed: int, seconds: float = 20.0, blip: bool = False, tone_sir_db: float | None = None):
    """Noise with nothing above 11 kHz (as after the Vorbis re-encode); optional tiny wideband blips (numerical
    noise, -120 dB) in a few seconds, optional whole-pass tone at 4321 Hz."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    spec = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    spec[np.fft.rfftfreq(n, 1 / SR) > 11000] = 0
    x = np.fft.irfft(spec, n)
    x = x / x.std()
    if blip:
        for t0 in (4.0, 9.0, 14.0):
            i = int(t0 * SR)
            x[i:i + 2 * SR] += 1e-6 * rng.standard_normal(2 * SR)
    if tone_sir_db is not None:
        a = np.sqrt(2 * np.mean(x ** 2) / 10 ** (tone_sir_db / 10))
        x = x + a * np.sin(2 * np.pi * 4321.0 * np.arange(n) / SR)
    return x.astype(np.float64)


def spectra(x, f_drop_raw=RAW_F_DROP):
    freqs, t, db = V3.psd_db_full(x, SR, PSD, chunk_windows=40)
    return {"freqs": freqs, "t": t, "db": db, "f_drop": f_drop_raw}


@pytest.fixture(scope="module")
def enroll():
    return [spectra(reencoded_audio(s)) for s in (1, 2, 3)]


def alarms(spec, tmpl, p):
    t, f = V31.score(spec["db"], spec["freqs"], tmpl, p)
    return V3.decide(np.zeros(len(t), bool), t, TAU, p["k"], p["n"])[1], t, f


def test_empty_bin_band_gives_no_alarm(enroll):
    scored = spectra(reencoded_audio(9, blip=True))
    t31 = V31.fit_template(enroll, P31, PSD)
    assert 10500 < t31["band_hz"][1] < 11000                    # edge from the re-encoded spectra, not 18.6 kHz
    a31, _, _ = alarms(scored, t31, P31)
    assert not a31.any()
    t3 = V31.fit_template(enroll, P3, PSD)                       # v3: band to 18.4 kHz, empty bins scored
    assert t3["band_hz"][1] == RAW_F_DROP - 200
    a3, T3, f3 = alarms(scored, t3, P3)
    assert a3.any() and np.all(f3[T3 >= TAU] > 11000)            # the v3 false alarm this fixes


def test_whole_pass_tone_in_band_still_flagged(enroll):
    tm = V31.fit_template(enroll, P31, PSD)
    a, t, f = alarms(spectra(reencoded_audio(7, tone_sir_db=10.0)), tm, P31)
    assert a[P31["k"] - 1:].all()
    assert np.all(np.abs(f[t >= TAU] - 4321.0) < 50.0)


def test_bins_70db_below_band_median_ignored():
    freqs = np.fft.rfftfreq(PSD["nperseg"], 1 / SR)
    rng = np.random.default_rng(0)
    low = (freqs > 5000) & (freqs < 6000)
    def pass_db(n=200):
        db = rng.normal(0.0, 1.0, (n, len(freqs))).astype(np.float32)
        db[:, low] -= 70.0                                       # 70 dB below the band median
        db[:, freqs > 15000] = -200.0                            # receiver edge at 15 kHz
        return db
    enr = [{"freqs": freqs, "t": np.arange(200) * 0.5, "db": pass_db(), "f_drop": 15000.0} for _ in range(3)]
    tm = V31.fit_template(enr, P31, PSD)
    fb = freqs[tm["band"]]
    assert not tm["keep"][(fb > 5100) & (fb < 5900)].any() and tm["keep"][(fb > 1000) & (fb < 4000)].all()
    scored = pass_db()
    scored[:, low] += 70.0                                       # the low bins come up to the band level
    t31, f31 = V31.score(scored, freqs, tm, P31)
    assert t31.max() < TAU and not np.any((f31 > 5000) & (f31 < 6000))
    t3, _ = V31.score(scored, freqs, V31.fit_template(enr, {**P31, "min_level_db": None}, PSD), P31)
    assert t3.min() > 30                                          # without the mask the same bins score ~70 SD
