"""Fusion rules (detect/fusion.py), the end-to-end folder writer (eval/run_standard_test.py) and run.py's audio front."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import fusion as F  # noqa: E402

CFG = {"quarantine_min_score": {"sequence": 4.0, "interference_model": 0.98},
       "confidence": {"replay": {"x": [0.35, 1.0], "y": [0.8, 0.99]}, "sequence": {"x": [2.3, 6.0], "y": [0.5, 0.95]},
                      "interference_model": {"x": [0.5, 1.0], "y": [0.6, 0.97]}},
       "line_screen": {"cap": "f_edge", "f_edge_margin_hz": 100.0, "band_guard_hz": 500.0, "line_match_hz": 100.0,
                       "precision_dev": 0.8, "downlink": {}}}


def _fr(name, score, ev, t):
    return {"filename": name, "t_utc": f"2026-01-01T00:00:{t:02d}Z", "t_rel_s": float(t), "score": score,
            "evidence": ev, "reason": "r", "trajectory": [[float(t), score]] if score else [], "phase_dev_s": None,
            "dev_s": None}


def test_no_alert_accepts():
    rep = {"frames": [_fr("a", 0.0, [], 0), _fr("b", 0.0, [], 5)]}
    seq = {"frames": [_fr("a", 0.0, [], 0), _fr("b", 0.0, [], 5)]}
    out = F.fuse(rep, seq, None, None, CFG, 0.35, 2.3, 5.0, None, None)
    assert out["verdict"] == "accept" and out["alerts"] == [] and out["confidence_attacked"] == 0.0


def test_replay_block_quarantines_and_sequence_flags():
    rep = {"frames": [_fr("a", 0.0, [], 0), _fr("b", 0.9, ["intra_run"], 5), _fr("c", 0.9, ["intra_run"], 10),
                      _fr("d", 0.0, [], 15)]}
    seq = {"frames": [_fr("a", 0.0, [], 0), _fr("b", 0.0, [], 5), _fr("c", 0.0, [], 10), _fr("d", 3.0, ["gap"], 15)]}
    out = F.fuse(rep, seq, None, None, CFG, 0.35, 2.3, 5.0, None, None)
    names = [a["name"] for a in out["alerts"]]
    assert names == ["replay", "deletion"]
    assert out["alerts"][0]["decision"] == "quarantine" and out["alerts"][1]["decision"] == "flag"
    assert out["verdict"] == "quarantine"
    assert 0 < out["alerts"][1]["confidence"] < 0.9
    assert {f["filename"]: f["decision"] for f in out["frames"]} == {"b": "quarantine", "c": "quarantine", "d": "flag"}


def test_line_and_model_merge_into_one_interference_alert():
    t = (np.arange(20) * 0.5).tolist()
    inter = {"events": [{"t_start_s": 2.0, "t_end_s": 4.0, "n_windows": 3, "max_probability": 0.99,
                         "peak_freq_hz": 3010.0, "reason": "model"}], "windows": {"t_start_s": t}}
    lines = {"n_windows": 20, "lines": [{"f_lo_hz": 2990.0, "f_hi_hz": 3030.0, "width_hz": 40.0, "peak_db": 12.0,
                                         "frac_windows": 0.9, "narrow": True, "in_signal_band": False, "flag": True,
                                         "hot_windows": list(range(18))}]}
    out = F.fuse(None, None, inter, lines, CFG, 0.35, 2.3, None, 10000.0, 11000.0)
    ia = [a for a in out["alerts"] if a["alert"] == "interference"]
    assert len(ia) == 1 and ia[0]["evidence"] == ["window_model", "line_screen"]
    assert ia[0]["confidence"] > 0.95 and ia[0]["decision"] == "quarantine"
    # a line above the f_edge cap does not count
    lines["lines"][0].update(f_lo_hz=10500.0, f_hi_hz=10540.0)
    out = F.fuse(None, None, None, lines, CFG, 0.35, 2.3, None, 10000.0, 11000.0)
    assert out["verdict"] == "accept"


def test_conf_map_is_monotone_and_clipped():
    m = {"x": [1.0, 2.0], "y": [0.2, 0.8]}
    assert F.conf_map(m, 0.0) == 0.2 and F.conf_map(m, 3.0) == 0.8 and F.conf_map(m, 1.5) == 0.5


def test_write_folder_roundtrip(tmp_path):
    from eval.run_standard_test import write_folder
    from detect.frames_io import stream_from_dir, read_metadata
    from detect.hash_store import qc_view
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    frames = [{"t": 0.0, "filename": "data_1_2026-01-01T00-00-00_g0", "hex": "aa" * 44, "decoder_branches": 2,
               "n_copies": 1, "copy_delays_s": [3.0]},
              {"t": 30.0, "filename": "data_1_2026-01-01T00-00-30_g0", "hex": "bb" * 44, "decoder_branches": 1,
               "n_copies": 0, "copy_delays_s": []}]
    clean = {"obs_id": 1, "norad": 25397, "start": start, "sr": 48000}
    write_folder(tmp_path, {"frames": frames, "audio": np.zeros(48000, np.float32)}, clean)
    assert len(list((tmp_path / "demoddata").iterdir())) == 4
    s, norad = stream_from_dir(tmp_path / "demoddata", qc_view(cfg), 25397)
    assert [f["filename"] for f in s] == [f["filename"] for f in frames]
    assert s[0]["decoder_branches"] == 2 and s[0]["n_copies"] == 1 and s[1]["n_copies"] == 0
    assert read_metadata(tmp_path) == {"norad": 25397, "start_utc": start, "obs_id": 1}
    assert (tmp_path / "audio.ogg").exists()


def test_run_audio_front_matches_frozen_front(tmp_path):
    import soundfile as sf
    from detect.audio_front import windows_from_audio
    from detect.run import audio_windows
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    rng = np.random.default_rng(0)
    x = (rng.normal(size=48000 * 8) * 0.1).astype(np.float32)
    sf.write(str(tmp_path / "a.wav"), x, 48000)
    a, ia = windows_from_audio(tmp_path / "a.wav", cfg)
    b, _, _, ib = audio_windows(tmp_path / "a.wav", cfg)
    assert ia["band_hz"] == ib["band_hz"]
    assert np.allclose(a.drop(columns="win_idx").to_numpy(), b.drop(columns="win_idx").to_numpy())
