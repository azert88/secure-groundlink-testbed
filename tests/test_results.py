"""eval/results.py: count intervals and the zero-event bounds."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.results import cp, zero_bounds  # noqa: E402


def test_zero_event_bounds():
    b = zero_bounds(44)
    assert b["rule_of_three_upper_95"] == round(3 / 44, 4)
    assert abs(b["exact_one_sided_upper_95"] - 0.0658) < 1e-3
    r = cp(0, 44)
    assert r[0] == 0 and r[1] == 0 and 0.07 < r[2] < 0.09     # two-sided 97.5 % upper limit


def test_cp_full_and_partial():
    assert cp(133, 133)[2] == 1.0 and cp(133, 133)[1] > 0.97
    v, lo, hi = cp(5, 10)
    assert v == 0.5 and lo < 0.5 < hi
