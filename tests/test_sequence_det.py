"""Sequence detector (detect/sequence_det.py) and fusion naming (detect/naming.py), on synthetic streams."""

from datetime import timedelta

import numpy as np
import pytest

from detect.naming import name_frame, timing_or_jump
from detect.sequence_det import detect, fit_profile, pooled
from tests.test_replay_det import CFG, FC, QC, T0, TC, fc_frame, mk, ts_frame, with_counters

P = {k: v for k, v in CFG["detectors"]["sequence"].items() if k not in ("profile", "threshold")}


def fc_pass(n=40, c0=1_000_000, start=T0, seed=0, text=False):
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        b = bytearray(fc_frame(c0 + k, rng))
        b[100:200] = bytes(100)                                   # constant padding, as in text frames
        out.append(mk(bytes(b), start + timedelta(seconds=5 * k), f"f{k:03d}"))
    return with_counters(out, FC)


@pytest.fixture(scope="module")
def model():
    # clean training passes with natural losses (1-3 frames, ~7 % of steps, as in TRAIN FUNCUBE-1)
    lr = np.random.default_rng(99)
    passes = {}
    for i in range(12):
        fr = fc_pass(40, c0=1_000_000 + 10_000 * i, seed=i)
        drop = set()
        for k in lr.choice(np.arange(2, 37), size=3, replace=False):
            drop |= set(range(k, k + int(lr.integers(1, 4))))
        passes[i] = [f for j, f in enumerate(fr) if j not in drop]
    rng = np.random.default_rng(9)
    tpass = {0: with_counters([mk(ts_frame(5000 + 30 * k, rng), T0 + timedelta(seconds=30 * k), f"t{k}") for k in range(10)], TC)}
    sats = {str(FC): fit_profile(passes, FC, QC, P), str(TC): fit_profile(tpass, TC, QC, P)}
    return {"params": P, "satellites": sats, "pooled": pooled(sats)}


def run(frames, model, norad=FC, **kw):
    res = detect(with_counters(frames, norad), norad, QC, model, **kw)
    return {r["filename"]: r for r in res["frames"]}, res


def test_clean_pass_is_quiet(model):
    r, res = run(fc_pass(seed=50), model)
    assert res["diagnostics"]["timing_usable"]
    assert max(x["score"] for x in r.values()) < 1.0


def test_duplicate_flags_the_copy(model):
    base = fc_pass(seed=51)
    cp = mk(base[10]["data"], base[10]["t_utc"] + timedelta(seconds=2), "dup")
    r, _ = run(base + [cp], model)
    assert "dup_payload" in r["dup"]["evidence"] and r["f010"]["score"] < 1.0


def test_forged_near_copy_in_a_gap(model):
    base = fc_pass(seed=52)
    del base[20:23]
    b = bytearray(base[5]["data"])
    b[150] ^= 0xFF                                               # one constant padding byte changed
    forged = mk(bytes(b), base[19]["t_utc"] + timedelta(seconds=5), "forged")
    r, _ = run(base + [forged], model)
    assert {"same_counter", "content"} <= set(r["forged"]["evidence"])
    assert "same_counter" not in r["f005"]["evidence"]
    assert name_frame(False, [], True, r["forged"]["evidence"]) == "forged"


def test_swap_flags_both_with_swap_rate(model):
    base = fc_pass(seed=53)
    base[10]["data"], base[11]["data"] = base[11]["data"], base[10]["data"]
    for f in base:
        f.pop("counter", None)
    r, _ = run([{**f, "sha256": __import__("hashlib").sha256(f["data"]).hexdigest()} for f in base], model)
    assert "inversion" in r["f010"]["evidence"] and "inversion" in r["f011"]["evidence"]
    assert r["f010"]["score"] > 2.5 and r["f012"]["score"] < 1.0


def test_counter_jump_flags_only_the_jumped_frame(model):
    base = fc_pass(seed=54)
    rng = np.random.default_rng(1)
    base[15] = mk(fc_frame(1_000_000 + 15 + 6, rng), base[15]["t_utc"], "f015")
    r, _ = run(base, model)
    assert r["f015"]["score"] > 2.5
    # neighbours stay far below the operating threshold (~2.3): the frame after the jump only shows the
    # 1-count hole left by the jump, which is a natural loss (p ~ 0.09)
    assert all(r[f"f{k:03d}"]["score"] < 2.0 for k in range(40) if k != 15)
    j = r["f015"]
    assert name_frame(False, [], True, j["evidence"], j["phase_dev_s"], j["dev_s"], 5.0) == "counter_jump"


def test_timing_shift(model):
    base = fc_pass(seed=55)
    base[20] = {**base[20], "t_utc": base[20]["t_utc"] + timedelta(seconds=3), "filename": "moved"}
    r, _ = run(base, model)
    m = r["moved"]
    assert "time_dev" in m["evidence"] and m["score"] > 2.0
    assert name_frame(False, [], True, m["evidence"], m["phase_dev_s"], m["dev_s"], 5.0) == "timing"


def test_deletion_gap_and_its_limit(model):
    base = fc_pass(seed=56)
    big = base[:10] + base[18:]                                   # 8 frames removed: never natural in the model
    r, _ = run(big, model)
    assert "gap" in r["f018"]["evidence"]


def test_batched_timestamps_disable_timing(model):
    base = fc_pass(seed=57)
    batched = [{**f, "t_utc": T0} for f in base]                  # every file written in one second
    r, res = run(batched, model)
    assert not res["diagnostics"]["timing_usable"]
    assert not any("time_dev" in x["evidence"] for x in r.values())


def test_small_satellite_uses_conservative_pooled_bins(model):
    from detect.sequence_det import Natural
    nat = Natural(model, TC, P)
    assert nat.pooled_used and len(nat.stats) == 2
    assert nat.dev(0.0) >= max(st["dev_bins"].get("0", 0) for st in nat.stats) / 1000


def test_ablation_switch(model):
    base = fc_pass(seed=58)
    cp = mk(base[10]["data"], base[10]["t_utc"] + timedelta(seconds=2), "dup")
    r, _ = run(base + [cp], model, causal=False, use={"dup_payload": False})
    assert "dup_payload" not in r["dup"]["evidence"]


def test_naming_precedence():
    assert name_frame(True, ["intra_run", "cross_single"], True, ["dup_payload"]) == "replay"
    assert name_frame(True, ["cross_run"], False, []) == "replay_cross_pass"
    assert name_frame(True, ["intra_single"], True, ["dup_payload"]) == "duplicate"
    assert name_frame(False, ["intra_run"], False, ["dup_payload"]) is None      # no alert -> no name
    assert name_frame(False, [], True, ["gap"]) == "deletion"
    assert timing_or_jump(None, 30.0, 1.0) == "counter_jump"                      # TECHSAT: phase on rhythm
    assert timing_or_jump(5.0, 5.0, 1.0) == "timing"
    assert timing_or_jump(0.0, 7.0, 5.0) == "timing" and timing_or_jump(0.0, 20.0, 5.0) == "counter_jump"
