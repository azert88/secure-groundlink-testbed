"""Phase 2 bench: safety guard, mode A DSP chain, KISS parsing, SatNOGS-format folder. No hardware needed."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from bench import dsp, iqfile, kiss
from bench.iq_to_satnogs import demodulate, frame_name, station_lowpass, write_folder
from bench.prepare_tx import prepare
from bench.safety import SafetyError, check_tx, load_config, worst_case_rx_dbm


@pytest.fixture()
def cfg():
    c = load_config()
    c = copy.deepcopy(c)
    c["safety"]["declared_attenuation_db"] = 30
    return c


# ------------------------------------------------------------------ safety
def test_shipped_config_refuses_tx_until_attenuation_declared():
    c = load_config()
    assert c["safety"]["declared_attenuation_db"] == 0          # repository default: not wired
    with pytest.raises(SafetyError, match="attenuation"):
        check_tx(c, -40)


def test_default_gain_accepted_with_30_db(cfg):
    banner = check_tx(cfg, cfg["safety"]["tx_gain_db"])
    assert any("NO ANTENNA" in line for line in banner)


def test_gain_above_limit_needs_explicit_flag(cfg):
    with pytest.raises(SafetyError, match="above the bench limit"):
        check_tx(cfg, -5)
    check_tx(cfg, -5, confirmed_attenuation=True)


def test_positive_gain_and_low_attenuation_refused(cfg):
    with pytest.raises(SafetyError):
        check_tx(cfg, 3, confirmed_attenuation=True)
    cfg["safety"]["declared_attenuation_db"] = 20
    with pytest.raises(SafetyError):
        check_tx(cfg, -40)


def test_worst_case_below_rx_absolute_maximum(cfg):
    assert worst_case_rx_dbm(cfg) < cfg["safety"]["rx_abs_max_dbm"]   # 7 - 30 = -23 dBm < +2.5 dBm


# ------------------------------------------------------------------ DSP
def _tone(f, seconds, sr=dsp.AUDIO_SR):
    t = np.arange(int(seconds * sr)) / sr
    return 0.3 * np.sin(2 * np.pi * f * t)


def _apply_cfo(iq, fs, cfo):
    return iq * np.exp(2j * np.pi * cfo * np.arange(len(iq)) / fs)


@pytest.mark.parametrize("cfo", [0.0, 7300.0, -11200.0, 21000.0, -21000.0])
def test_tone_survives_up_and_down_conversion_with_cfo(cfg, cfo):
    fs = cfg["rf"]["sample_rate"]
    z, _ = dsp.scaled_analytic(_tone(3000.0, 3.0), 0.5)
    iq = dsp.Upconverter(cfg).process(z)
    iq = _apply_cfo(iq, fs, cfo)
    down = dsp.Downconverter(cfg)
    audio = np.concatenate([down.process(b) for b in dsp.chunks(iq, fs)])
    assert abs(down.features[-1]["cfo_hz"] - cfo) < 2.0
    seg = audio[dsp.AUDIO_SR:]                                    # skip the first second (filter start-up)
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f_peak = np.fft.rfftfreq(len(seg), 1 / dsp.AUDIO_SR)[np.argmax(spec)]
    assert abs(f_peak - 3000.0) < 3.0


def test_upconverter_is_streaming_invariant(cfg):
    z, _ = dsp.scaled_analytic(_tone(1500.0, 2.0), 0.5)
    whole = dsp.Upconverter(cfg).process(z)
    up = dsp.Upconverter(cfg)
    parts = np.concatenate([up.process(b) for b in dsp.chunks(z, 7001)])
    assert np.allclose(whole, parts, atol=1e-9)


def test_pilot_estimate_resolves_sub_hertz(cfg):
    fs = cfg["rf"]["sample_rate"]
    t = np.arange(fs) / fs
    iq = np.exp(2j * np.pi * (cfg["rf"]["pilot_hz"] + 1234.4) * t)
    est = dsp.pilot_estimate(iq, fs, cfg["rf"]["pilot_hz"])
    assert abs(est["cfo_hz"] - 1234.4) < 0.5


def test_cs16_round_trip_and_limit():
    x = np.array([0.5 + 0.5j, 2.0 + 0j, -0.1j])
    y = dsp.from_cs16(dsp.to_cs16(x, 2 ** 14), 2 ** 14)
    assert abs(y[0] - (0.5 + 0.5j)) < 1e-3 and abs(abs(y[1]) - 0.95) < 1e-3


# ------------------------------------------------------------------ KISS
def test_kiss_round_trip_with_timestamps_and_escapes():
    frames = [{"t_ms": 1763640000123, "data": bytes([0xC0, 0xDB, 1, 2])}, {"t_ms": None, "data": b"abc"}]
    assert kiss.read_frames(kiss.write_frames(frames)) == frames


# ------------------------------------------------------------------ station folder
def test_prepare_demodulate_and_folder_readable_by_detectors(cfg, tmp_path: Path):
    from detect.frames_io import stream_from_dir
    audio = _tone(2500.0, 4.0).astype(np.float32)
    info = prepare(audio, dsp.AUDIO_SR, cfg, tmp_path / "tx")
    assert info["clipped_fraction"] < 0.01
    rec, feats = demodulate(tmp_path / "tx", cfg)
    assert len(rec) == 4 * dsp.AUDIO_SR and len(feats) == 4
    rec = station_lowpass(rec, 10000.0)
    start = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    frames = [{"t": start.replace(second=s), "t_ms": None, "data": bytes([s]) * 44} for s in (1, 1, 3)]
    write_folder(tmp_path / "obs", rec, feats, frames, 900000009, 25397, start, {"test": True})
    names = sorted(p.name for p in (tmp_path / "obs" / "demoddata").iterdir())
    assert names == ["data_900000009_2026-11-20T12-00-01_g0", "data_900000009_2026-11-20T12-00-01_g1",
                     "data_900000009_2026-11-20T12-00-03_g0"]
    assert (tmp_path / "obs" / "audio.ogg").exists() and (tmp_path / "obs" / "bench_features.csv").exists()
    import yaml
    from detect.hash_store import qc_view
    root_cfg = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "config.yaml", encoding="utf-8"))
    s, _ = stream_from_dir(tmp_path / "obs" / "demoddata", qc_view(root_cfg), 25397)
    assert len(s) == 2                                            # the two same-second identical files are one frame


def test_frame_name_counts_per_second():
    used = {}
    t = datetime(2026, 11, 20, 12, 0, 5, tzinfo=timezone.utc)
    assert [frame_name(1, t, used) for _ in range(2)] == ["data_1_2026-11-20T12-00-05_g0", "data_1_2026-11-20T12-00-05_g1"]
