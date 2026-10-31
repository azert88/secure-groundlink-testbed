"""Tests for attacks/ (spec 10: label counts, SNR within 0.5 dB of target, file length unchanged)."""

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from attacks import benign
from attacks.apply import apply_recipe
from attacks.common import finalize_frames, paste, recipe_seed, window_labels
from attacks.interference import apply_interference, reference_powers
from attacks.replay import apply_replay, frame_name
from attacks.sequence import apply_sequence
from detect.counters import counter_codec, funcube_counter

SR = 48000
P = {"win_s": 1.0, "hop_s": 0.5, "nperseg": 2048, "noverlap": 1024, "chunk_windows": 20}
START = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
QC = {"funcube": {"norad": 39444, "frame_len": 256, "satid": 2, "seq_byte": 50, "cycle": 24},
      "techsat": {"norad": 25397, "frame_len": 44, "marker": "TechSat", "counter_byte": 6}}
DEC, ENC = counter_codec(39444, QC)


def fc_hex(counter):
    seq, ftype = divmod(counter, 24)
    b = bytearray(256)
    b[0] = (2 << 6) | ftype
    b[50:53] = seq.to_bytes(3, "big")
    b[100] = counter % 251                       # payload differs between frames
    return b.hex()


def clean_obs(n_frames=20, dur=200.0, period=5.0, t0=20.0):
    frames = [{"t": t0 + period * k, "filename": frame_name(1, START, t0 + period * k, "data_1_2026-10-01T12-00-00_0000"),
               "hex": fc_hex(1000 + k), "decoder_branches": 1, "n_copies": 0, "copy_delays_s": [],
               "is_attack": 0, "attack_type": "", "subtype": "", "evasion": 0} for k in range(n_frames)]
    rng = np.random.default_rng(0)
    audio = (0.05 * rng.standard_normal(int(dur * SR))).astype(np.float32)
    ref = reference_powers(audio, SR, P, 100.0, 20000.0, 22000.0)
    return {"obs_id": 1, "norad": 39444, "start": START, "sr": SR, "audio": audio, "frames": frames,
            "timing_reliable": True, "f_low": 100.0, "f_edge": 20000.0, "f_drop": 22200.0, "ref": ref,
            "encode_counter": ENC, "decode_counter": DEC}


def fin(frames):
    return finalize_frames(frames, True, True, 2.0, DEC)


def test_recipe_seed_deterministic():
    assert recipe_seed(1, "replay_00") == recipe_seed(1, "replay_00") != recipe_seed(1, "replay_01")


def test_replay_labels_timing_and_audio_length():
    c = clean_obs()
    names = [f["filename"] for f in c["frames"][2:9]]               # L = 7
    op = {"source_filenames": names, "delta_s": 100.0, "subtype": "intra_pass", "audio": True,
          "airtime_s": 4.33, "margin_s": 0.5, "fade_ms": 10.0}
    frames, audio, ev = apply_replay(c["frames"], c["audio"], SR, op, c, c)
    rows = fin(frames)
    copies = [r for r in rows if r["is_attack"]]
    assert len(copies) == 7 and len(rows) == 27
    assert [r["t"] for r in copies] == [c["frames"][k]["t"] + 100 for k in range(2, 9)]
    assert all(r["counter"] == 1002 + k for k, r in enumerate(copies))     # replayed counters are old ones
    assert len(audio) == len(c["audio"])
    a0 = c["frames"][2]["t"] - 4.83
    i_src, i_dst = int(round(a0 * SR)) + 1000, int(round((a0 + 100) * SR)) + 1000
    assert np.allclose(audio[i_dst:i_dst + 1000], c["audio"][i_src:i_src + 1000])   # pasted (inside the fades)
    assert ev["segments"][0][0] == pytest.approx(a0 + 100, abs=1e-4)
    assert len({r["filename"] for r in rows}) == len(rows)


def test_replay_sparse_beacons_paste_only_around_copies():
    c = clean_obs(n_frames=6, dur=200.0, period=30.0, t0=10.0)       # beacons at 10, 40, ..., 160 s
    op = {"source_filenames": [f["filename"] for f in c["frames"][:5]], "delta_s": 15.0, "subtype": "intra_pass",
          "audio": True, "airtime_s": 0.1, "margin_s": 0.5, "fade_ms": 10.0}
    frames, audio, ev = apply_replay(c["frames"], c["audio"], SR, op, c, c)
    assert len(ev["segments"]) == 5 and all(b - a == pytest.approx(1.2) for a, b in ev["segments"])
    assert np.array_equal(audio[int(39 * SR):int(41 * SR)], c["audio"][int(39 * SR):int(41 * SR)])   # own beacon kept
    assert len(audio) == len(c["audio"])


@pytest.mark.parametrize("op, n_labels", [
    ({"subtype": "deletion", "filenames": ["F5", "F6"], "label_filename": "F7"}, 1),
    ({"subtype": "swap", "filenames": ["F5", "F6"]}, 2),
    ({"subtype": "duplicate", "source_filename": "F5", "t_new": 47.0, "byte_changes": []}, 1),
    ({"subtype": "forged", "source_filename": "F5", "t_new": 47.0, "byte_changes": [[20, 7], [99, 3]]}, 1),
    ({"subtype": "timing", "filename": "F5", "shift_s": 4.0}, 1),
    ({"subtype": "counter_jump", "filename": "F5", "jump": 6}, 1),
])
def test_sequence_subtypes_one_event(op, n_labels):
    c = clean_obs()
    for k, f in enumerate(c["frames"]):
        f["filename"] = f"F{k}"
    out = fin(apply_sequence(c["frames"], {"type": "sequence", **op}, c, ENC, DEC))
    lab = [r for r in out if r["is_attack"]]
    assert len(lab) == n_labels and all(r["subtype"] == op["subtype"] for r in lab)
    n = {"deletion": 18, "duplicate": 21, "forged": 21}.get(op["subtype"], 20)
    assert len(out) == n
    if op["subtype"] == "counter_jump":
        assert lab[0]["counter"] == 1005 + 6
    if op["subtype"] == "swap":
        assert [r["counter"] for r in out[5:7]] == [1006, 1005]
    if op["subtype"] == "forged":
        src = bytes.fromhex(c["frames"][5]["hex"])
        new = bytes.fromhex(lab[0]["hex"])
        assert new[:16] == src[:16] and sum(a != b for a, b in zip(src, new)) == 2
    if op["subtype"] == "timing":
        assert lab[0]["t"] == c["frames"][5]["t"] + 4


@pytest.mark.parametrize("shape, bw", [("tone", None), ("noise", 120.0)])
@pytest.mark.parametrize("snr", [-20.0, 0.0, 10.0])
def test_interference_snr_within_half_db(shape, bw, snr):
    c = clean_obs(dur=60.0)
    op = {"shape": shape, "f0_hz": 5000.0, "bw_hz": bw, "snr_db": snr, "phase": 0.3,
          "segments": [[10.0, 50.0]], "subtype": shape}
    y, ev = apply_interference(c["audio"], SR, op, c["ref"], np.random.default_rng(1))
    assert len(y) == len(c["audio"])
    z = (y - c["audio"])[10 * SR:50 * SR].astype(np.float64)
    measured = 10 * math.log10(c["ref"]["p_ref"] / np.mean(z ** 2))
    assert abs(measured - snr) < 0.5
    assert np.all(y[:9 * SR] == c["audio"][:9 * SR])                       # silent outside the segment
    assert ev["inr_db"] == pytest.approx(10 * math.log10(ev["p_interf"] / c["ref"]["p_noise"]))


def test_interference_intermittent_drift_and_strongest():
    c = clean_obs(dur=60.0)
    segs = [[5.0, 8.0], [11.0, 13.0], [20.0, 24.0]]
    op = {"shape": "tone", "f0_hz": 3000.0, "bw_hz": None, "snr_db": 0.0, "phase": 0.0, "segments": segs,
          "drift_hz_s": 4.0, "subtype": "drift", "evasion": 1}
    y, ev = apply_interference(c["audio"], SR, op, c["ref"], np.random.default_rng(2))
    d = y - c["audio"]
    assert np.all(d[int(9 * SR):int(10.9 * SR)] == 0) and ev["evasion"] == 1
    c["audio"] = c["audio"] + 0.5 * np.sin(2 * np.pi * 7000 * np.arange(len(c["audio"])) / SR).astype(np.float32)
    c["ref"] = reference_powers(c["audio"], SR, P, 100.0, 20000.0, 22000.0)
    y, ev = apply_interference(c["audio"], SR, {**op, "f0_hz": "strongest", "drift_hz_s": 0.0},
                               c["ref"], np.random.default_rng(3))
    assert abs(ev["f0_hz"] - 7000) < 30


def test_benign_ops_keep_length_and_hit_targets():
    c = clean_obs(dur=60.0)
    x = c["audio"]
    y, ev = benign.gain(x, SR, {"factor": 2.0, "t0_s": 10.0, "t1_s": 20.0})
    assert len(y) == len(x) and np.allclose(y[12 * SR:18 * SR], 2 * x[12 * SR:18 * SR]) and ev["is_attack"] == 0
    assert np.allclose(y[:9 * SR], x[:9 * SR])
    y, _ = benign.noise_rise(x, SR, {"rise_db": 3.0, "t0_s": 0.0, "t1_s": 60.0}, c["ref"], 100.0, 20000.0,
                             np.random.default_rng(4))
    rise = 10 * math.log10(reference_powers(y, SR, P, 100.0, 20000.0, 22000.0)["p_noise"] / c["ref"]["p_noise"])
    assert abs(rise - 3.0) < 0.5
    y, _ = benign.fading(x, SR, {"rate_hz": 0.1, "depth_db": 6.0, "phase": 0.0})
    assert len(y) == len(x) and np.max(np.abs(y)) <= np.max(np.abs(x)) + 1e-6
    y, ev = benign.edge_attenuation(x, SR, {"start_s": 10.0, "end_s": 10.0})
    assert len(y) == len(x) and abs(y[0] / x[0] - 10 ** (-0.5)) < 1e-3 and ev["segments"][1] == [50.0, 60.0]


def test_paste_crossfade_keeps_length():
    a, b = np.zeros(1000, np.float32), np.ones(200, np.float32)
    y = paste(a, b, 100, 20)
    assert len(y) == 1000 and y[200] == 1 and y[100] < 0.1 and y[99] == 0


def test_window_labels_overlap():
    t = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
    ev = [{"attack_type": "interference", "subtype": "tone", "is_attack": 1, "segments": [[1.2, 3.0]], "snr_db": -5.0,
           "inr_db": 3.0}]
    lab = window_labels(t, 1.0, ev)
    assert [l["is_attack"] for l in lab] == [0, 0, 1, 1, 1]
    assert lab[1]["overlap_frac"] == pytest.approx(0.3) and lab[1]["attack_type"] == "interference"
    assert lab[3]["snr_db"] == -5.0 and lab[0]["attack_type"] == ""


def test_apply_recipe_combo_is_deterministic():
    c = clean_obs()
    rec = {"obs_id": 1, "variant_id": "combo_00", "seed": 123, "operations": [
        {"type": "replay", "source_filenames": [f["filename"] for f in c["frames"][1:6]], "delta_s": 110.0,
         "subtype": "intra_pass", "audio": True, "airtime_s": 4.33, "margin_s": 0.5, "fade_ms": 10.0},
        {"type": "sequence", "subtype": "counter_jump", "filename": c["frames"][10]["filename"], "jump": 3},
        {"type": "interference", "shape": "noise", "f0_hz": 4000.0, "bw_hz": 50.0, "snr_db": -5.0, "phase": 0.0,
         "segments": [[0.0, 200.0]], "subtype": "noise"}]}
    r1, r2 = apply_recipe(rec, c), apply_recipe(rec, c)
    assert np.array_equal(r1["audio"], r2["audio"]) and len(r1["audio"]) == len(c["audio"])
    rows = fin(r1["frames"])
    assert sum(r["attack_type"] == "replay" for r in rows) == 5 and sum(r["attack_type"] == "sequence" for r in rows) == 1
    assert [e["attack_type"] for e in r1["events"]] == ["replay", "interference"]
    assert funcube_counter(bytes.fromhex(c["frames"][10]["hex"]), QC["funcube"]) == 1010   # clean input untouched
