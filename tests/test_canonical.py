"""Tests for the canonical frame stream (decoder branches + rewrites)."""

from datetime import datetime, timedelta, timezone

from detect.canonical import canonical_frames

T0 = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)


def f(sec, sha, name):
    return {"t": T0 + timedelta(seconds=sec), "sha256": sha, "filename": name}


def test_branches_and_rewrites_merge():
    # pattern seen at one station: _g0 / _g1 at t, then a suffix-free copy 4 s later
    frames = canonical_frames([f(6, "a", "x_g0"), f(6, "a", "x_g1"), f(10, "a", "x"), f(36, "b", "y_g0")])
    assert len(frames) == 2
    a = frames[0]
    assert a["filename"] == "x_g0" and a["decoder_branches"] == 2
    assert a["n_copies"] == 1 and a["copy_delays_s"] == [4.0]
    assert frames[1]["n_copies"] == 0 and frames[1]["copy_delays_s"] == []


def test_no_merge_beyond_delay_or_different_bytes():
    frames = canonical_frames([f(0, "a", "1"), f(11, "a", "2"), f(3, "c", "3")], max_rewrite_delay_s=10)
    assert [fr["filename"] for fr in frames] == ["1", "3", "2"]       # 11 s apart: natural repeat, kept
    assert all(fr["n_copies"] == 0 for fr in frames)


def test_delay_measured_from_first_copy_and_plain_seconds():
    frames = canonical_frames([{"t": 0.0, "sha256": "a", "filename": "1"},
                               {"t": 6.0, "sha256": "a", "filename": "2"},
                               {"t": 12.0, "sha256": "a", "filename": "3"}])
    assert len(frames) == 2 and frames[0]["copy_delays_s"] == [6.0]  # 12 s from the first copy
