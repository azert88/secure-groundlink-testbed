"""Tests for the interference detector v2 template floor and feature list."""

import numpy as np
import pandas as pd

from detect.interference_det import BASE
from eval.interference_v2 import BAND_EDGE, MIN_SCALE, V2, fit_template_v2


def clean_table(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for st in range(4):                        # 4 stations, each with its own offset of every feature
        for obs in range(2):
            n = 50
            d = {f: rng.normal(10 + 3 * st, 0.01, n) for f in BASE}
            d["peak_persistence"] = np.zeros(n)
            d["peak_freq_hz"] = rng.uniform(1000, 5000, n)
            rows.append(pd.DataFrame({**d, "norad": 1, "station": st, "obs_id": 10 * st + obs}))
    return pd.concat(rows, ignore_index=True)


def test_scale_floor_covers_station_spread_and_physical_minimum():
    t = fit_template_v2(clean_table())["1"]
    for f in BASE:
        if f != "peak_persistence":            # constant 0 here: physical floor only
            assert t["scale"][f] >= 3.0, f         # between-station SD of medians (~3.9), not the 0.01 window MAD
        assert t["scale"][f] >= MIN_SCALE.get(f, 0.0)


def test_v2_has_no_band_edge_feature_outside_recording_deviations():
    for f in BAND_EDGE:
        assert f not in V2 and f"tz_{f}" not in V2
    assert not any("occupied_bw" in c for c in V2)
