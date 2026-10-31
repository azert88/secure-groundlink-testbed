"""Offline tests for Phase C QC."""

from datetime import datetime, timedelta, timezone

import numpy as np

from qc import (estimate_f_edge, final_status, funcube_counter, load_frames, narrowband_lines,
                techsat_counter, timing_stats, window_psds)

FC = {"norad": 39444, "frame_len": 256, "satid": 2, "seq_byte": 50, "cycle": 24,
      "s_per_count": 5.0}
TC = {"norad": 25397, "frame_len": 44, "marker": "TechSat", "counter_byte": 6, "s_per_count": 1.0}
QC = {"funcube": FC, "techsat": TC, "line_threshold_db": 10.0, "line_min_frac_windows": 0.30,
      "line_max_width_hz": 200.0, "signal_band_min_frac": 0.05, "signal_band_margin_hz": 200.0,
      "max_rewrite_delay_s": 10.0,
      "psd": {"win_s": 1.0, "hop_s": 0.5, "nperseg": 2048, "noverlap": 1024, "f_low_hz": 100.0,
              "edge_margin_hz": 100.0, "edge_drop_db": 20.0, "chunk_windows": 50,
              "drop_smooth_hz": 1000.0, "drop_min_hz": 1000.0, "baseline_hz": 500.0,
              "baseline_exclude_hz": 50.0}}
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def fc_frame(seq, ftype):
    b = bytearray(256)
    b[0] = (2 << 6) | ftype
    b[50:53] = seq.to_bytes(3, "big")
    return bytes(b)


def test_funcube_counter():
    assert funcube_counter(fc_frame(10, 23), FC) + 1 == funcube_counter(fc_frame(11, 0), FC)
    assert funcube_counter(fc_frame(10, 24), FC) is None        # frame type out of range
    assert funcube_counter(fc_frame(10, 1)[:236], FC) is None   # truncated frame


def ts_beacon(sec):
    b = bytearray(44)
    b[6:10] = sec.to_bytes(4, "little")
    b[20:32] = b"TechSat-V4.2"
    return bytes(b)


def test_techsat_counter():
    assert techsat_counter(ts_beacon(1000), TC) + 30 == techsat_counter(ts_beacon(1030), TC)
    assert techsat_counter(ts_beacon(1000)[:40], TC) is None             # not 44 bytes
    assert techsat_counter(bytes(44), TC) is None                        # no marker
    assert techsat_counter(bytes.fromhex("c2a65ed476406c"), TC) is None  # 7-byte fragment seen in data


def test_techsat_timing_counter_reported_not_applied():
    frames = [{"t": T0 + timedelta(seconds=30 * k), "counter": 5000 + 30 * k} for k in range(6)]
    s = timing_stats(frames, 4, TC, counter_rule=False)
    assert s["counter_time_consistent_frac"] == 1.0 and s["timing_reliable"]
    frames[3]["counter"] = 9999                                          # inconsistent, but not a rule
    assert timing_stats(frames, 4, TC, counter_rule=False)["timing_reliable"]


def test_load_frames_collapses_identical_branches(tmp_path):
    d = tmp_path / "demoddata"
    d.mkdir()
    (d / "data_1_2026-10-01T00-00-05_g0").write_bytes(b"A" * 20)
    (d / "data_1_2026-10-01T00-00-05_g1").write_bytes(b"A" * 20)   # identical -> one frame
    (d / "data_1_2026-10-01T00-00-05_g2").write_bytes(b"B" * 20)   # different -> kept
    frames = load_frames(tmp_path, 25397, QC)
    assert len(frames) == 2
    assert sorted(f["decoder_branches"] for f in frames) == [1, 2]
    (d / "data_1_2026-10-01T00-00-35_g0").write_bytes(ts_beacon(30))
    (d / "data_1_2026-10-01T00-00-39").write_bytes(ts_beacon(30))      # station rewrite 4 s later
    frames = load_frames(tmp_path, 25397, QC)
    assert [f["valid"] for f in frames] == [False, False, True] and frames[-1]["counter"] == 30
    assert frames[-1]["n_copies"] == 1 and frames[-1]["copy_delays_s"] == [4.0]


def test_timing_stats_funcube():
    frames = [{"t": T0 + timedelta(seconds=5 * k), "counter": 100 + k} for k in range(10)]
    s = timing_stats(frames, 1, FC)
    assert s["timing_reliable"] and s["counter_step1_frac"] == 1.0
    assert s["counter_time_consistent_frac"] == 1.0
    batched = frames + [{"t": T0, "counter": 200 + k} for k in range(5)]
    s = timing_stats(batched, 1, FC)
    assert not s["timing_reliable"] and s["max_frames_per_second"] == 6
    # one isolated late write among 40 seconds is tolerated
    long = [{"t": T0 + timedelta(seconds=5 * k), "counter": 100 + k} for k in range(40)]
    long[10]["t"] = long[11]["t"]
    s = timing_stats(long, 1, FC)
    assert s["n_seconds_overfull"] == 1 and s["timing_reliable"]


def test_tone_is_flagged_and_noise_is_not():
    sr, rng = 48000, np.random.default_rng(0)
    noise = rng.standard_normal(20 * sr).astype(np.float32)
    t = np.arange(len(noise)) / sr
    for x, expected in ((noise, False), (noise + 2.0 * np.sin(2 * np.pi * 3000 * t), True)):
        f, psd = window_psds(x.astype(np.float32), sr, QC["psd"])
        db = 10 * np.log10(psd + 1e-20)
        assert psd.shape[0] == 39                                  # (20 - 1) / 0.5 + 1 windows
        res = narrowband_lines(f, db, estimate_f_edge(f, db, QC["psd"])[0], QC)
        assert res["line_flag"] is expected
        if expected:
            assert any(l["f_lo_hz"] <= 3000 <= l["f_hi_hz"] for l in res["lines"])


def test_wide_line_is_reported_not_flagged():
    """A 1.3 kHz wide signal (like FUNCUBE-1's own BPSK downlink) is not a narrowband RFI line."""
    from scipy.signal import butter, sosfilt
    sr, rng = 48000, np.random.default_rng(2)
    noise = rng.standard_normal(20 * sr)
    band = sosfilt(butter(6, [11000, 12300], btype="band", fs=sr, output="sos"), rng.standard_normal(20 * sr))
    x = (noise + 40 * band).astype(np.float32)
    f, psd = window_psds(x, sr, QC["psd"])
    db = 10 * np.log10(psd + 1e-20)
    res = narrowband_lines(f, db, estimate_f_edge(f, db, QC["psd"])[0], QC)
    assert not res["line_flag"]
    assert any(lo <= 11100 and 12200 <= hi for lo, hi in res["observed_bands"])


def test_noise_band_150hz_flagged_by_3db_width():
    """A strong 150 Hz noise band: hot run wider than 200 Hz (skirts) but -3 dB width ~150 Hz -> flagged,
    and it is never taken as downlink (no TRAIN region given)."""
    from scipy.signal import butter, sosfilt
    sr, rng = 48000, np.random.default_rng(6)
    z = sosfilt(butter(4, [6925, 7075], btype="band", fs=sr, output="sos"), rng.standard_normal(20 * sr))
    x = (rng.standard_normal(20 * sr) + 300 * z).astype(np.float32)
    f, psd = window_psds(x, sr, QC["psd"])
    db = 10 * np.log10(psd + 1e-20)
    res = narrowband_lines(f, db, estimate_f_edge(f, db, QC["psd"])[0], QC)
    line = [l for l in res["lines"] if l["f_lo_hz"] - 50 <= 7000 <= l["f_hi_hz"] + 50][0]
    assert line["run_hi_hz"] - line["run_lo_hz"] > line["width_hz"]
    assert line["width_hz"] <= 200 and line["narrow"] and res["line_flag"]


def test_tone_in_signal_band_is_tagged_not_flagged():
    from scipy.signal import butter, sosfilt
    sr, rng = 48000, np.random.default_rng(3)
    t = np.arange(20 * sr) / sr
    band = sosfilt(butter(6, [11000, 12300], btype="band", fs=sr, output="sos"), rng.standard_normal(20 * sr))
    base = rng.standard_normal(20 * sr) + 40 * band
    for tone_hz, amp, in_band in ((11650, 200, True), (5000, 4, False)):      # in-band tone rises above the band
        x = (base + amp * np.sin(2 * np.pi * tone_hz * t)).astype(np.float32)
        f, psd = window_psds(x, sr, QC["psd"])
        db = 10 * np.log10(psd + 1e-20)
        res = narrowband_lines(f, db, estimate_f_edge(f, db, QC["psd"])[0], QC, [[11000.0, 12300.0]])
        tone = [l for l in res["lines"] if l["f_lo_hz"] - 50 <= tone_hz <= l["f_hi_hz"] + 50]
        assert tone and tone[0]["narrow"] and tone[0]["in_signal_band"] is in_band
        assert res["line_flag"] is (not in_band)


def test_local_baseline_sees_line_inside_raised_band():
    """A narrow line inside a wideband data band (TECHSAT 1B case) is still found."""
    from scipy.signal import butter, sosfilt
    sr, rng = 48000, np.random.default_rng(4)
    t = np.arange(20 * sr) / sr
    x = sosfilt(butter(8, 11300, fs=sr, output="sos"), rng.standard_normal(20 * sr))
    x = (x + 0.5 * np.sin(2 * np.pi * 4000 * t)).astype(np.float32)
    f, psd = window_psds(x, sr, QC["psd"])
    db = 10 * np.log10(psd + 1e-20)
    res = narrowband_lines(f, db, estimate_f_edge(f, db, QC["psd"])[0], QC)
    assert res["line_flag"] and any(l["f_lo_hz"] - 50 <= 4000 <= l["f_hi_hz"] + 50 for l in res["lines"])


def test_f_edge_with_codec_cutoff():
    """Near-zero bins above the data band (lossy codec) must not pull f_edge to Nyquist."""
    from scipy.signal import butter, sosfilt
    sr, rng = 48000, np.random.default_rng(5)
    x = sosfilt(butter(8, 10500, fs=sr, output="sos"), rng.standard_normal(10 * sr))
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1 / sr) > 11500] = 0                 # codec cut-off
    x = (np.fft.irfft(spec, len(x)) + 1e-7 * rng.standard_normal(len(x))).astype(np.float32)
    f, psd = window_psds(x, sr, QC["psd"])
    edge, drop = estimate_f_edge(f, 10 * np.log10(psd + 1e-20), QC["psd"])
    assert 10000 <= edge <= drop <= 13000


def test_f_edge_on_lowpassed_noise():
    from scipy.signal import butter, sosfilt
    sr = 48000
    x = np.random.default_rng(1).standard_normal(10 * sr)
    x = sosfilt(butter(8, 6000, fs=sr, output="sos"), x).astype(np.float32)
    f, psd = window_psds(x, sr, QC["psd"])
    edge, _ = estimate_f_edge(f, 10 * np.log10(psd + 1e-20), QC["psd"])
    assert 6000 <= edge <= 8000


def test_final_status():
    row = lambda ar, flag: {"audio_reasons": ar, "line_flag": flag, "obs_id": 1}
    assert final_status(row(["x"], None), {}) == "rejected"
    assert final_status(row([], False), {}) == "ok"
    assert final_status(row([], True), {}) == "flagged"
    assert final_status(row([], True), {1: "ok"}) == "flagged_reviewed_ok"
    assert final_status(row([], True), {1: "rejected"}) == "flagged_reviewed_rejected"
    # frame-QC failure alone does not reject the audio (decision 37)
    assert final_status({**row([], False), "frame_reasons": ["3 canonical frames < 10"]}, {}) == "ok"
