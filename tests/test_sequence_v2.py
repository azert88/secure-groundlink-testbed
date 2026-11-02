"""Sequence v2 (detect/sequence_v2.py): change (a) pooling and change (b) cross-station content rule."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import sequence_det as S  # noqa: E402
from detect import sequence_v2 as V2  # noqa: E402

P = {"min_frames": 100, "dev_max_bin_s": 10}


def model(own_inv: int):
    own = {"n_frames": 36, "n_inversions": own_inv, "dev_bins": {"0": 30}}
    big = {"n_frames": 600, "n_inversions": 2, "dev_bins": {"0": 500, "1": 90}}
    return {"satellites": {"1": own, "2": big}, "pooled": {"n_frames": 636, "n_inversions": 2 + own_inv,
                                                           "dev_bins": {"0": 530, "1": 90}}}


def test_pooling_fix_lets_a_small_satellite_reach_rare_scores():
    m = model(0)
    v1, v2 = S.Natural(m, 1, P), V2.NaturalV2(m, 1, P)
    assert v1.rate("n_inversions") == 1 / 37                     # v1: own Laplace prior dominates (score 1.57)
    assert v2.rate("n_inversions") == 3 / 637                    # v2: pooled, own count is 0
    assert v2.dev(7) < v1.dev(7)
    m = model(5)                                                 # a value the satellite shows keeps its own rate
    assert V2.NaturalV2(m, 1, P).rate("n_inversions") == 6 / 37
    assert V2.NaturalV2(m, 2, P).rate("n_inversions") == S.Natural(m, 2, P).rate("n_inversions")


def test_content_constants_need_two_stations():
    qc = {"funcube": {"norad": 39444}}
    f = lambda b: {"counter": 1, "data": bytes([1]) + bytes([b]) * 4}
    passes = {10: [f(7)] * 8, 11: [f(7)] * 8}
    one = V2.constants_across_stations(passes, {10: 1, 11: 1}, 39444, qc, 8)
    two = V2.constants_across_stations(passes, {10: 1, 11: 2}, 39444, qc, 8)
    assert one == {} and 1 in two and two[1][1] == 7
