import json
import math

import numpy as np
import pandas as pd

from eval import s4_satnogs_fa as S


def fr(counters, times, valid=None):
    return pd.DataFrame({"counter": counters, "t_rel_s": times, "valid": valid or [1] * len(counters)})


def test_expected_frames_interpolates_gaps():
    f, why = S.expected_frames(fr([100, 100, 103, 104, None], [10, 10, 25, 30, 40]))
    assert why == "ok" and [x["counter"] for x in f] == [100, 101, 102, 103, 104]
    assert [x["lost"] for x in f] == [False, True, True, False, False]
    assert np.allclose([x["t"] for x in f], [10, 15, 20, 25, 30])
    assert math.isclose(f[0]["t0"], 7.5) and math.isclose(f[-1]["t1"], 32.5) and math.isclose(f[1]["t0"], 12.5)


def test_expected_frames_exclusions():
    assert S.expected_frames(fr([5], [1]))[1] == "< 2 decoded counters"
    assert S.expected_frames(fr([5, 6], [10, 9]))[1] == "non-increasing counter-time line"
    assert S.expected_frames(fr([5, 6, 7], [50, 57, 57]))[1] == "ok"             # 1 s timestamps can tie
    assert S.expected_frames(fr([5, 6], [1, 2], valid=[0, 0]))[1] == "no decoded counter"


def test_band_power_and_reference():
    freqs = np.arange(0, 1000, 10.0)
    db = np.full((4, len(freqs)), -60.0)
    assert np.allclose(S.band_power(freqs, db, 100, 190), 10 * 1e-6 * 10)
    assert math.isclose(S.ref_power(np.array([0, 5, 10, 15, 20]), np.array([1, 3, 10, 10, 4.0])), 4.0)


def test_report_loso_on_synthetic_records(tmp_path):
    (tmp_path / "rec").mkdir()
    def rec(obs, station, lost_all, level):
        frames = [{"counter": i, "t": 5 * i, "t0": 5 * i - 2.5, "t1": 5 * i + 2.5, "lost": lost_all} for i in range(30)]
        t = np.arange(-3, 150, 0.5)
        return {"obs_id": obs, "variant_id": "clean", "station_id": station, "split": "train", "condition": "clean",
                "ops": [], "source": "test", "win_t": t.tolist(), "win_p_rel": [level] * len(t), "frames": frames}
    recs = [rec(1, 10, True, 1.0), rec(2, 20, True, 1.0), rec(3, 30, False, 1.0)]
    for r in recs:
        (tmp_path / "rec" / f"{r['obs_id']}__clean.json").write_text(json.dumps(r))
    rep = S.stage_report(tmp_path)
    # stations 10 and 20 are fully lost at the same level: each one's P0 comes from the other -> all windows alert
    assert rep["fa"]["clean"]["windows"] == 9 and rep["fa"]["clean"]["alert_windows"] == 6
    assert not rep["gate_b"]["pass"] and rep["by_station"]["30"]["alert_windows"] == 0
    assert (tmp_path / "report.md").exists()
