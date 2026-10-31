"""Tests for Phase H validation helpers (data/validate.py) on synthetic inputs."""

from pathlib import Path

import numpy as np
import pandas as pd

from validate import label_consistency, leakage, single_feature_auc, snr_bin


def splits(test_station=3):
    obs = {"1": {"station_id": 1}, "2": {"station_id": 2}, "3": {"station_id": test_station}}
    return {"satellites": {"99": {"obs": obs, "splits": {"train": [1], "calib": [2], "test": [3]}}}}


def test_leakage_flags_shared_station_and_split(tmp_path):
    rec = {(3, "clean"): {"split": "test", "operations": []}}
    assert leakage(splits(), rec, tmp_path)["ok"]
    assert not leakage(splits(test_station=1), rec, tmp_path)["ok"]          # test station also in train
    bad = {(3, "replay_00"): {"split": "test", "operations": [{"type": "replay", "source_obs": 1, "source_filenames": []}]}}
    assert not leakage(splits(), bad, tmp_path)["ok"]                        # cross-pass source from train
    (tmp_path / "x.py").write_text("open('data/labels/labels.csv')\n")
    assert not leakage(splits(), rec, tmp_path)["ok"]                        # detect/ must not read labels


def labels(frame_attack, win_attack, snr=np.nan):
    f = [{"unit": "frame", "index": k, "is_attack": int(a), "attack_type": t, "snr_db": np.nan}
         for k, (a, t) in enumerate(frame_attack)]
    w = [{"unit": "window", "index": k, "is_attack": int(a), "attack_type": "interference" if a else "", "snr_db": snr if a else np.nan}
         for k, a in enumerate(win_attack)]
    return pd.DataFrame(f + w)


def test_label_consistency():
    t = np.arange(0, 20, 0.5)                                                # 40 windows of 1 s
    rec = {"variant_id": "combo_00", "group": "combo", "operations": [
        {"type": "replay", "source_filenames": ["a", "b"], "audio": False},
        {"type": "sequence", "subtype": "swap", "filenames": ["c", "d"]},
        {"type": "interference", "segments": [[5.0, 8.0]], "snr_db": 3.0}]}
    win = np.array([max(0, min(x + 1, 8) - max(x, 5)) >= 0.5 for x in t])
    fr = [(1, "replay")] * 2 + [(1, "sequence")] * 2 + [(0, "")] * 10
    assert label_consistency(rec, labels(fr, win, 3.0), 14, 12, t, 1.0, 0.5) == []
    assert label_consistency(rec, labels(fr[1:], win, 3.0), 13, 12, t, 1.0, 0.5)   # one replay label missing
    shifted = np.roll(win, 3)
    assert label_consistency(rec, labels(fr, shifted, 3.0), 14, 12, t, 1.0, 0.5)   # windows off the recipe times
    assert label_consistency(rec, labels(fr, win, -7.0), 14, 12, t, 1.0, 0.5)      # wrong snr_db


def test_snr_bin():
    assert list(snr_bin([-10, -7.4, 2.4, 2.6, 35], 5.0)) == [-10, -5, 0, 5, 35]


def test_single_feature_auc_orientation_and_bins():
    rng = np.random.default_rng(1)
    rows = []
    for split in ("train", "test"):
        for snr in (-10, 30):
            for _ in range(50):                                              # strong interference -> large feature
                rows.append({"split": split, "norad": 1, "is_attack": 1, "attack_type": "interference", "snr_db": snr,
                             "overlap_frac": 1.0, "f": -(snr + rng.normal()) })
        for _ in range(200):
            rows.append({"split": split, "norad": 1, "is_attack": 0, "attack_type": "", "snr_db": np.nan,
                         "overlap_frac": 0.0, "f": -40 + rng.normal()})
    out = single_feature_auc(pd.DataFrame(rows), ["f"], 5.0)[0]
    assert out["direction"] == "+" and out["-10"] == 1.0 and out["+30"] == 1.0 and out["n-10"] == 50
