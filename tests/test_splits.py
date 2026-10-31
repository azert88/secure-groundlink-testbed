"""Tests for Phase E splits (station-disjoint test, test-only station rules, TECHSAT frame set)."""

import pytest

from make_splits import split_satellite

CFG = {"splits": {"fractions": {"train": 0.6, "calib": 0.2, "test": 0.2},
                  "test_only_stations": {39444: 2}, "excluded_test_stations": {39444: [2107, 4272]},
                  "test_station_obs_range": [3, 6]}}


def obs(i, station, frame_qc=True, selected=True):
    return {"obs_id": i, "station_id": station, "frame_qc": frame_qc, "selected": selected, "timing_reliable": True}


def funcube_rows():
    rows, i = [], 1
    for station, n in ((2107, 12), (4272, 12), (1763, 3), (4806, 3), (5155, 2), (4011, 1)):
        for _ in range(n):
            rows.append(obs(i, station))
            i += 1
    return rows


def test_funcube_test_only_stations():
    res = split_satellite(funcube_rows(), 39444, CFG, 1)
    assert res["test_stations"] == [1763, 4806]                 # 2107 / 4272 excluded, 5155 has 2 obs
    sp = res["splits"]
    assert len(sp["test"]) == 6 and len(sp["calib"]) == round(0.2 * 33)
    all_ids = sp["train"] + sp["calib"] + sp["test"]
    assert sorted(all_ids) == list(range(1, 34))                 # every observation exactly once
    st = {r["obs_id"]: r["station_id"] for r in funcube_rows()}
    assert not {st[i] for i in sp["train"]} & {st[i] for i in sp["test"]}


def test_funcube_deterministic_and_too_few_stations():
    assert split_satellite(funcube_rows(), 39444, CFG, 1) == split_satellite(funcube_rows(), 39444, CFG, 1)
    rows = [r for r in funcube_rows() if r["station_id"] != 4806]
    with pytest.raises(SystemExit):
        split_satellite(rows, 39444, CFG, 1)


def test_techsat_frame_set_is_test_only():
    rows = [obs(100 + k, 10 + k // 2, frame_qc=(k < 3)) for k in range(40)]   # 20 stations x 2 obs
    res = split_satellite(rows, 25397, CFG, 7)
    sp = res["splits"]
    assert res["frame_test_extra"] == [100, 101, 102]
    assert set(res["frame_test_extra"]) <= set(sp["test"])
    assert {10, 11} <= set(res["test_stations"])
    assert abs(len(sp["test"]) - 8) <= 1                         # close to 20 % of 40
    st = {r["obs_id"]: r["station_id"] for r in rows}
    assert not {st[i] for i in sp["train"]} & set(res["test_stations"])
