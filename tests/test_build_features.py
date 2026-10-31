"""Tests for Phase G per-variant tables (data/build_features.py) on synthetic clean data."""

import json

import numpy as np
import yaml

from build_features import is_done, peak_rss_mb, recipes_digest, variant_tables
from test_attacks import clean_obs

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def synthetic_clean():
    c = clean_obs(n_frames=20, dur=200.0)
    c.update(order_by_counter=True, band_hi=22000.0)
    return c


def test_peak_rss_positive():
    assert peak_rss_mb() > 10


def test_resume_marker_tracks_recipes(tmp_path):
    rdir, fdir = tmp_path / "recipes" / "7", tmp_path / "features" / "7"
    rdir.mkdir(parents=True), fdir.mkdir(parents=True)
    (rdir / "clean.json").write_text("{}")
    cfg = {"attacks": {"recipes_dir": str(tmp_path / "recipes")}, "features": {"out_dir": str(tmp_path / "features")}}
    assert not is_done(7, cfg)
    (fdir / "done.json").write_text(json.dumps({"recipes_sha256": recipes_digest(rdir)}))
    assert is_done(7, cfg)
    (rdir / "clean.json").write_text('{"changed": 1}')                  # regenerated recipes -> redo
    assert not is_done(7, cfg)


def test_variant_tables_labels_and_lengths():
    c = synthetic_clean()
    clean_rec = {"obs_id": 1, "variant_id": "clean", "seed": 1, "operations": []}
    f0, w0, l0 = variant_tables(clean_rec, c, CFG, None)
    assert len(f0) == 20 and len(w0) == 399 and len(l0) == 20 + 399 and l0.is_attack.sum() == 0
    rec = {"obs_id": 1, "variant_id": "combo_00", "seed": 7, "operations": [
        {"type": "replay", "source_filenames": [f["filename"] for f in c["frames"][1:6]], "delta_s": 110.0,
         "subtype": "intra_pass", "audio": True, "airtime_s": 4.33, "margin_s": 0.5, "fade_ms": 10.0},
        {"type": "sequence", "subtype": "swap", "filenames": [c["frames"][12]["filename"], c["frames"][13]["filename"]]},
        {"type": "interference", "shape": "tone", "f0_hz": 4000.0, "bw_hz": None, "snr_db": -10.0, "phase": 0.0,
         "segments": [[50.0, 80.0]], "subtype": "tone"}]}
    f, w, lab = variant_tables(rec, c, CFG, None)
    fl, wl = lab[lab.unit == "frame"], lab[lab.unit == "window"]
    assert len(f) == 25 and (fl.attack_type == "replay").sum() == 5 and (fl.subtype == "swap").sum() == 2
    assert len(w) == len(w0)                                            # file length unchanged
    tone = wl[(wl.attack_type == "interference") & (wl.is_attack == 1)]
    assert len(tone) == 61 and np.allclose(tone.snr_db, -10.0)          # >= 0.5 s overlap: starts 49.5 .. 79.5 s
    assert ((wl.attack_type == "replay") & (wl.is_attack == 1)).sum() > 0
    peak = w[w.t_start_s.between(52, 75)].peak_freq_hz
    assert np.all(np.abs(peak - 4000) < 30)
