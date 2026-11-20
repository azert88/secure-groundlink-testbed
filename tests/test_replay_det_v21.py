"""Replay v2.1 (parameter set of detect/replay_det_v2.py, models/replay_v21/params.json, decision P2-13)."""

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from detect import replay_det_v2 as v2
from detect.counters import counter_codec
from tests.golden_replay import compute
from tests.test_replay_det import FC, QC, T0, TC, fc_pass, mk, with_counters
from tests.test_replay_det_v2 import near, ts_pass

ROOT = Path(__file__).resolve().parents[1]
P21 = json.loads((ROOT / "models/replay_v21/params.json").read_text(encoding="utf-8"))["params"]
TAU = json.loads((ROOT / "models/replay_v21/threshold.json").read_text(encoding="utf-8"))["tau"]
C0 = 1_000_000


def run21(frames, norad=FC):
    return v2.detect(with_counters(frames, norad), norad, QC, P21, None, T0, None)


def by_name(res):
    return {r["filename"]: r for r in res["frames"]}


def change(data: bytes, positions, counter=None, norad=FC) -> bytes:
    b = bytearray(data)
    for p in positions:
        b[p] ^= 0x5A
    if counter is not None:
        b = bytearray(counter_codec(norad, QC)[1](bytes(b), counter))
    return bytes(b)


def test_params_are_the_registered_v21_set():
    assert P21["e2_near_dup_d"] == {"39444": 5, "25397": None} and P21["e2_min_run"] == 2
    assert P21["e1_same_counter"] and TAU == 0.35


def test_two_genuine_consecutive_frames_11_bytes_apart_no_alert():
    """As obs 15275276 (P2-13): frame k+1 differs from frame k by 11 bytes outside the masked fields."""
    base = fc_pass(30)
    k = 12
    base[k + 1] = mk(change(base[k]["data"], range(100, 111), counter=C0 + k + 1), base[k + 1]["t_utc"], base[k + 1]["filename"])
    res = run21(base)
    assert max(r["score"] for r in res["frames"]) == 0.0


def test_single_near_copy_with_changed_counter_not_flagged_by_e2():
    """Documented limitation: one E2 twin alone (no common-shift run) does not count in v2.1."""
    base = fc_pass(30)
    cp = mk(change(base[5]["data"], [100], counter=C0 + 5 + 40), base[5]["t_utc"] + timedelta(seconds=200), "c005")
    r = by_name(run21(base + [cp]))["c005"]
    assert r["score"] < TAU and not r["evidence"]


def test_run_of_five_near_copies_with_changed_counters_flagged():
    base = fc_pass(30)
    cps = [mk(change(base[i]["data"], [100, 120, 140], counter=C0 + i + 40), base[i]["t_utc"] + timedelta(seconds=200),
              f"c{i:03d}") for i in range(5, 10)]
    res = by_name(run21(base + cps))
    for i in range(5, 10):
        assert "intra_run" in res[f"c{i:03d}"]["evidence"] and res[f"c{i:03d}"]["score"] >= TAU
        assert "near (d = 3)" in res[f"c{i:03d}"]["reason"]
    assert all(not res[f"f{k:03d}"]["evidence"] for k in range(30))


@pytest.mark.parametrize("norad,mkpass,pos", [(FC, fc_pass, 100), (TC, ts_pass, 30)])
def test_one_byte_near_copy_run_counter_kept_flagged_via_e1(norad, mkpass, pos):
    base = mkpass()
    res = by_name(run21(base + near(base, range(3, 8), 200, pos=pos, norad=norad), norad))
    for i in range(3, 8):
        assert "intra_run" in res[f"c{i:03d}"]["evidence"] and res[f"c{i:03d}"]["score"] >= TAU
        assert "same counter" in res[f"c{i:03d}"]["reason"]


def test_v1_and_v2_outputs_unchanged_on_sample_recordings():
    """tests/data/replay_golden_v1_v2.json was written from v1 and v2 BEFORE the v2.1 change."""
    ref = json.loads((ROOT / "tests/data/replay_golden_v1_v2.json").read_text(encoding="utf-8"))
    assert compute() == ref
    assert any(r["score"] > 0 for r in ref["14932721/near"]["v2"])        # the reference exercises v2 evidence
