"""Replay detector v2 (detect/replay_det_v2.py, S1) on synthetic streams; v1 is the reference where they must agree."""

from datetime import timedelta

import pytest

from detect import replay_det as v1
from detect import replay_det_v2 as v2
from detect.counters import counter_codec
from detect.frame_store_v2 import FrameStore
from tests.test_replay_det import CFG, FC, P, QC, T0, TC, copies, fc_frame, fc_pass, mk, ts_frame, with_counters

import numpy as np

P2 = {**P, **{k: CFG["detectors"]["replay_v2"][k] for k in v2.V2_KEYS}}


def ts_pass(n=15, c0=5_000_000, start=T0, seed=1):
    rng = np.random.default_rng(seed)
    return [mk(ts_frame(c0 + 30 * k, rng), start + timedelta(seconds=30 * k), f"f{k:03d}") for k in range(n)]


def flip(data: bytes, pos: int) -> bytes:
    b = bytearray(data)
    b[pos] ^= 0x5A
    return bytes(b)


def near(frames, idx, shift_s, pos, prefix="c", counter_fn=None, norad=FC):
    """Copies with one byte changed at pos (outside counter / marker / byte 0); counter optionally rewritten."""
    _, enc = counter_codec(norad, QC)
    out = []
    for i in idx:
        d = flip(frames[i]["data"], pos)
        if counter_fn is not None:
            d = enc(d, counter_fn(i))
        out.append(mk(d, frames[i]["t_utc"] + timedelta(seconds=shift_s), f"{prefix}{i:03d}"))
    return out


def run2(frames, norad=FC, store=None, p=P2, **kw):
    return v2.detect(with_counters(frames, norad), norad, QC, p, store, T0, None, **kw)


def run1(frames, norad=FC, store=None):
    return v1.detect(with_counters(frames, norad), norad, QC, P, store, T0, None)


def scores(res):
    return {r["filename"]: r["score"] for r in res["frames"]}


def kinds(res):
    return {r["filename"]: r["evidence"] for r in res["frames"]}


@pytest.mark.parametrize("norad,mkpass", [(FC, fc_pass), (TC, ts_pass)])
def test_clean_pass_no_alert(norad, mkpass):
    assert max(scores(run2(mkpass(), norad)).values()) == 0.0


def test_exact_copy_same_as_v1():
    base = fc_pass(30)
    frames = base + copies(base, range(5, 12), 200)
    assert scores(run2(frames)) == scores(run1(frames))
    assert all("intra_run" in kinds(run2(frames))[f"c{i:03d}"] for i in range(5, 12))


def test_one_byte_near_copy_funcube():
    base = fc_pass(30)
    frames = base + near(base, range(5, 12), 200, pos=100)
    s1, s2 = scores(run1(frames)), scores(run2(frames))
    assert all(s2[f"c{i:03d}"] >= P["scores"]["intra_run"] for i in range(5, 12))
    assert all(s2[f"f{k:03d}"] == 0.0 for k in range(30))
    assert max(s1[f"c{i:03d}"] for i in range(5, 12)) < P["scores"]["intra_run"]     # v1 does not see the twins


def test_one_byte_near_copy_techsat_same_counter():
    base = ts_pass(15)
    frames = base + near(base, range(3, 8), 200, pos=30, norad=TC)
    k2 = kinds(run2(frames, TC))
    assert all("intra_run" in k2[f"c{i:03d}"] for i in range(3, 8))
    assert all(not k2[f"f{k:03d}"] for k in range(15))


def test_copy_with_modified_counter_funcube_caught_by_e2():
    base = fc_pass(30)
    c0 = 1_000_000
    frames = base + near(base, range(5, 12), 200, pos=100, counter_fn=lambda i: c0 + i + 40)   # counter fits the copy time
    k2 = kinds(run2(frames))
    assert all("intra_run" in k2[f"c{i:03d}"] for i in range(5, 12))


def test_copy_with_modified_counter_techsat_not_caught_e2_off():
    """Documented negative result (P2-10): TECHSAT 1B E2 is off, a copy with a consistent new counter is not a twin."""
    base = ts_pass(15)
    c0 = 5_000_000
    frames = base + near(base, range(3, 8), 200, pos=30, counter_fn=lambda i: c0 + 30 * i + 200, norad=TC)
    s2 = scores(run2(frames, TC))
    assert max(s2.values()) < P["scores"]["intra_run"]


def _store_with(frames, obs_id, pass_start, norad=FC):
    st = FrameStore()
    st.add_pass(norad, obs_id, pass_start, with_counters([dict(f) for f in frames], norad))
    return st


@pytest.mark.parametrize("mutate", [False, True])
def test_two_stations_same_pass_no_alert(mutate):
    """Another station's reception of the same pass (within concurrent_tol_s) is corroboration, also when it differs
    by one byte (E1 / E2 twin): no replay evidence."""
    base = fc_pass(30)
    other = [mk(flip(f["data"], 100) if (mutate and k % 3 == 0) else f["data"], f["t_utc"] + timedelta(seconds=2),
                f"o{k:03d}") for k, f in enumerate(base)]
    st = _store_with(other, 111, T0 - timedelta(seconds=30))
    res = run2(base, store=st)
    assert max(scores(res).values()) == 0.0
    assert any("corroborated" in r["reason"] for r in res["frames"])


def test_cross_pass_near_copy_caught():
    old = fc_pass(30, c0=900_000, start=T0 - timedelta(days=1), seed=7)
    st = _store_with(old, 222, T0 - timedelta(days=1))
    base = fc_pass(30)
    cp = [mk(flip(old[i]["data"], 100), T0 + timedelta(seconds=152 + 5 * i), f"x{i:03d}") for i in range(5, 12)]
    k2 = kinds(run2(base + cp, store=st))
    assert all("cross_run" in k2[f"x{i:03d}"] for i in range(5, 12))
    assert all(not k2[f"f{k:03d}"] for k in range(30))


def test_robust_line_sets_copies_aside():
    """E3 alone (E1, E2 off): 12 one-byte copies of a 15-frame pass break v1's line (15 / 27 singletons on it);
    v2 sets the lower-counter cluster aside, keeps the line and names the copies by counter regression."""
    p = {**P2, "e1_same_counter": False, "e2_near_dup_d": {}}
    base = fc_pass(15)
    frames = base + near(base, range(0, 12), 80, pos=100)
    r1, r2 = run1(frames), run2(frames, p=p)
    assert not r1["diagnostics"]["line_ok"]
    assert r2["diagnostics"]["line_ok"] and r2["diagnostics"]["line_copies_set_aside"] == 12
    k2 = kinds(r2)
    assert all("regress_run" in k2[f"c{i:03d}"] for i in range(12))
    assert all(not k2[f"f{k:03d}"] for k in range(15))


def test_counter_reset_not_set_aside():
    """A genuine counter reset (counters below the pass's range) is not treated as copies: same scores as v1."""
    a = fc_pass(10, c0=1_000_000)
    b = fc_pass(10, c0=10, start=T0 + timedelta(seconds=50), seed=3)
    b = [mk(f["data"], f["t_utc"], f"r{k:03d}") for k, f in enumerate(b)]
    r2 = run2(a + b)
    assert r2["diagnostics"]["line_copies_set_aside"] == 0
    assert scores(r2) == scores(run1(a + b))


def test_run_p2_default_is_v1_and_switch_restores_module():
    from detect import run as R
    from detect import run_p2
    assert CFG["detectors"]["replay_version"] == "v1"
    saved = R.replay_det
    with run_p2.replay_v2_in_run(CFG) as c2:
        assert R.replay_det is not saved
        assert c2["detectors"]["replay"]["threshold"] == CFG["detectors"]["replay_v2"]["threshold"]
    assert R.replay_det is saved
