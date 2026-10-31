"""Tests for the recipe generator (data/make_recipes.py)."""

import numpy as np
import pandas as pd
import pytest
import yaml

from make_recipes import Infeasible, free_intervals, plan_interference, plan_replay, plan_sequence

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))["attacks"]


def frames_df(times, burst_ids=None, counters=True):
    n = len(times)
    return pd.DataFrame({"idx": range(n), "filename": [f"F{k}" for k in range(n)], "t_rel_s": times,
                         "burst_id": burst_ids if burst_ids is not None else range(n),
                         "counter": [1000 + k for k in range(n)] if counters else [np.nan] * n,
                         "length": [256] * n, "hex": ["00" * 256] * n})


def test_free_intervals():
    assert free_intervals([10, 20], 1.0, 30) == [(0.0, 9.0), (11.0, 19.0), (21.0, 30.0)]


def test_plan_replay_later_gap_outside_frames():
    fr = frames_df([20 + 5 * k for k in range(30)])          # frames 20..165 s, audio 400 s
    for seed in range(20):
        op = plan_replay(np.random.default_rng(seed), fr, fr, 400.0, CFG, 4.33, True, "intra_pass", None)
        assert 5 <= op["length"] <= 20 and op["placement"] == "later"
        src = fr[fr.filename.isin(op["source_filenames"])].t_rel_s
        new = src + op["delta_s"]
        assert all(np.min(np.abs(fr.t_rel_s.values - t)) >= 9.66 - 1e-9 for t in new)   # no overlap with any frame
        assert new.max() + 4.83 <= 400.0 and new.min() - 4.83 >= 0
        assert op["delta_s"] == int(op["delta_s"])


def test_plan_replay_interleaves_sparse_beacons():
    fr = frames_df([30.0 * k + 10 for k in range(25)])       # beacons every 30 s over the whole 760 s pass
    op = plan_replay(np.random.default_rng(3), fr, fr, 760.0, CFG, 0.1, True, "intra_pass", None)
    new = fr[fr.filename.isin(op["source_filenames"])].t_rel_s + op["delta_s"]
    assert all(np.min(np.abs(fr.t_rel_s.values - t)) >= 1.2 for t in new) and abs(op["delta_s"]) >= 10
    op = plan_replay(np.random.default_rng(4), fr, fr, 760.0, CFG, 0.1, True, "intra_pass", None, max_len=7)
    assert op["length"] <= 7


def test_plan_replay_infeasible_when_no_gap():
    fr = frames_df([5 * k for k in range(40)])               # frames fill the whole 200 s audio
    with pytest.raises(Infeasible):
        plan_replay(np.random.default_rng(0), fr, fr, 200.0, CFG, 4.33, True, "intra_pass", None)


def test_plan_sequence_respects_availability():
    fr = frames_df([20 + 5 * k for k in range(30)], counters=False)
    seen = {plan_sequence(np.random.default_rng(s), fr, CFG, False, 400.0)["subtype"] for s in range(60)}
    assert "timing" not in seen and "counter_jump" not in seen and {"deletion", "swap"} <= seen
    excl = set(f"F{k}" for k in range(2, 28))
    for s in range(20):
        op = plan_sequence(np.random.default_rng(s), fr, CFG, True, 400.0, exclude=excl)
        touched = set(op.get("filenames", [])) | {op.get("filename"), op.get("label_filename"), op.get("source_filename")}
        if op["subtype"] in ("deletion", "swap"):
            assert not touched & excl


def test_plan_interference_ranges():
    for s in range(50):
        op = plan_interference(np.random.default_rng(s), 300.0, 10874.0, CFG)
        assert -10 <= op["snr_db"] <= 30 and op["segments"][0][1] <= 300.0
        half = (op["bw_hz"] or 0) / 2
        assert 200 + half <= op["f0_hz"] <= 10874.0 - half
    op = plan_interference(np.random.default_rng(1), 300.0, 10874.0, CFG, subtype="intermittent")
    assert len(op["segments"]) >= 1 and op["evasion"] == 1
    op = plan_interference(np.random.default_rng(2), 300.0, 10874.0, CFG, subtype="drift")
    t0, t1 = op["segments"][0]
    assert op["shape"] == "tone" and 200 <= op["f0_hz"] + op["drift_hz_s"] * (t1 - t0) <= 10874.0
