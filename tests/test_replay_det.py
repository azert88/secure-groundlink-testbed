"""Replay detector (detect/replay_det.py) and its inputs (frames_io, hash_store), on synthetic streams."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import yaml

from detect.frames_io import filename_time, infer_norad, stream_from_dir, stream_from_rows
from detect.hash_store import HashStore, qc_view
from detect.replay_det import counter_line, detect

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
QC = qc_view(CFG)
P = CFG["detectors"]["replay"]
FC, TC = 39444, 25397
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def fc_frame(counter: int, rng) -> bytes:
    seq, ft = divmod(counter, 24)
    b = bytearray(rng.integers(0, 256, 256, dtype=np.uint8).tobytes())
    b[0] = (2 << 6) | ft
    b[50:53] = seq.to_bytes(3, "big")
    return bytes(b)


def ts_frame(counter: int, rng) -> bytes:
    b = bytearray(rng.integers(0, 256, 44, dtype=np.uint8).tobytes())
    b[20:27] = b"TechSat"
    b[6:10] = counter.to_bytes(4, "little")
    return bytes(b)


def mk(data: bytes, t: datetime, name: str, n_copies: int = 0) -> dict:
    return {"filename": name, "t_utc": t, "sha256": hashlib.sha256(data).hexdigest(), "data": data,
            "counter": None, "decoder_branches": 1, "n_copies": n_copies, "copy_delays_s": [2.0] * n_copies}


def with_counters(frames, norad):
    from detect.counters import counter_codec
    dec, _ = counter_codec(norad, QC)
    for f in frames:
        f["counter"] = dec(f["data"])
    return sorted(frames, key=lambda f: (f["t_utc"], f["filename"]))


def fc_pass(n=30, c0=1_000_000, start=T0, seed=0):
    rng = np.random.default_rng(seed)
    return [mk(fc_frame(c0 + k, rng), start + timedelta(seconds=5 * k), f"f{k:03d}") for k in range(n)]


def copies(frames, idx, shift_s, prefix="c"):
    return [mk(frames[i]["data"], frames[i]["t_utc"] + timedelta(seconds=shift_s), f"{prefix}{i:03d}") for i in idx]


def run(frames, norad=FC, store=None, start=None, **kw):
    return detect(with_counters(frames, norad), norad, QC, P, store, start or T0, None, **kw)


def scores(res):
    return {r["filename"]: r["score"] for r in res["frames"]}


def test_clean_pass_scores_zero():
    s = scores(run(fc_pass()))
    assert max(s.values()) == 0.0


def test_intra_block_after_original():
    base = fc_pass(30)
    s = scores(run(base + copies(base, range(5, 12), 200)))
    assert all(s[f"c{i:03d}"] == P["scores"]["intra_run"] for i in range(5, 12))
    assert all(s[f"f{k:03d}"] == 0.0 for k in range(30))


def test_copies_before_originals_are_attributed_by_line():
    base = fc_pass(30, start=T0 + timedelta(seconds=300))
    res = run(base + copies(base, range(0, 10), -200))
    s = scores(res)
    assert all(s[f"c{i:03d}"] >= P["scores"]["intra_run"] for i in range(10))
    assert all(s[f"f{k:03d}"] == 0.0 for k in range(30))
    assert "counter-time line" in next(r["reason"] for r in res["frames"] if r["filename"] == "c000")


def test_mostly_replayed_pass_keeps_its_line():
    # 10 of 12 frames replayed before the pass: originals + 2 singletons still outweigh the copies
    base = fc_pass(12, start=T0 + timedelta(seconds=120))
    s = scores(run(base + copies(base, range(0, 10), -52)))
    assert all(s[f"c{i:03d}"] > 0 for i in range(10)) and all(s[f"f{k:03d}"] == 0 for k in range(12))


def test_undecodable_pair_follows_block_shift():
    base = fc_pass(20, start=T0 + timedelta(seconds=300))
    bad = mk(b"\x00" * 100, base[10]["t_utc"], "fbad")            # no counter
    base[10] = bad
    cp = copies(base, range(5, 15), -200)
    s = scores(run(base + cp))
    assert s["c010"] > 0 and s["fbad"] == 0.0


def test_techsat_interleaved_copies_form_a_run():
    rng = np.random.default_rng(1)
    base = [mk(ts_frame(5000 + 30 * k, rng), T0 + timedelta(seconds=30 * k), f"f{k:03d}") for k in range(12)]
    cp = copies(base, range(2, 7), 315)                             # lands between later beacons
    s = scores(run(base + cp, norad=TC))
    assert all(s[f"c{i:03d}"] == P["scores"]["intra_run"] for i in range(2, 7))
    assert all(s[f"f{k:03d}"] == 0.0 for k in range(12))


def test_cross_pass_replay_and_concurrent_reception():
    old = fc_pass(20, c0=900_000, start=T0 - timedelta(days=2), seed=3)
    same = fc_pass(20, c0=1_000_000, start=T0, seed=0)               # another station, same pass
    st = HashStore()
    st.add_pass(FC, 1, old[0]["t_utc"], old)
    st.add_pass(FC, 2, T0 - timedelta(seconds=30), [{**f, "t_utc": f["t_utc"] - timedelta(seconds=2)} for f in same])
    base = fc_pass(20, c0=1_000_000, start=T0, seed=0)
    cp = copies(old, range(3, 9), 2 * 86400 + 150)
    res = run(base + cp, store=st)
    s = scores(res)
    assert all(s[f"c{i:03d}"] == P["scores"]["cross_run"] for i in range(3, 9))
    assert all(s[f"f{k:03d}"] == 0.0 for k in range(20))
    assert "corroborated" in next(r["reason"] for r in res["frames"] if r["filename"] == "f000")


def test_store_sees_only_earlier_passes_and_never_itself():
    old = fc_pass(5, start=T0 - timedelta(days=1))
    st = HashStore()
    st.add_pass(FC, 7, old[0]["t_utc"], old)
    sha = old[0]["sha256"]
    assert st.lookup(FC, sha, T0, None)
    assert not st.lookup(FC, sha, T0 - timedelta(days=2), None)       # pass starts after the recording
    assert not st.lookup(FC, sha, T0, exclude_obs=7)


def test_unarchived_cross_pass_caught_by_counter_regression():
    old = fc_pass(10, c0=900_000, start=T0 - timedelta(days=2), seed=3)
    base = fc_pass(30)
    s = scores(run(base + copies(old, range(2, 6), 2 * 86400 + 152)))   # no store
    assert all(s[f"c{i:03d}"] == P["scores"]["regress_run"] for i in range(2, 6))
    assert all(s[f"f{k:03d}"] == 0.0 for k in range(30))


def test_counter_jump_does_not_flag_following_frames():
    rng = np.random.default_rng(5)
    base = fc_pass(30)
    base[10] = mk(fc_frame(1_000_000 + 10 + 8, rng), base[10]["t_utc"], "f010")    # one forward jump
    s = scores(run(base))
    assert max(s.values()) == 0.0


def test_rewrite_gets_low_score_and_reason():
    base = fc_pass(10)
    base[3]["n_copies"] = 1
    res = run(base)
    r = next(r for r in res["frames"] if r["filename"] == "f003")
    assert r["score"] == P["scores"]["rewrite"] and "rewrite" in r["reason"]


def test_causal_trajectory_alerts_at_second_copy_of_a_run():
    base = fc_pass(30)
    res = run(base + copies(base, range(5, 9), 200))
    c5 = next(r for r in res["frames"] if r["filename"] == "c005")
    # single repeat at its own arrival, a run once the second copy with the same shift arrives
    assert c5["trajectory"][0] == [c5["t_rel_s"], P["scores"]["intra_single"]]
    assert c5["trajectory"][-1][1] == P["scores"]["intra_run"]


def test_ablation_switches():
    base = fc_pass(30)
    fr = base + copies(base, range(5, 12), 200)
    assert max(scores(run(fr, causal=False, use={"intra": False, "regress": False})).values()) == 0.0


def test_counter_line_mode():
    base = with_counters(fc_pass(20), FC)
    line = counter_line(base, 5.0, set(), P)
    assert line["ok"] and line["frac"] == 1.0


# ---------------------------------------------------------------------------------------- frames_io
def test_filename_time_tolerates_extra_suffix():
    assert filename_time("data_1_2026-10-01T12-00-05_g0_") == T0 + timedelta(seconds=5)
    assert filename_time("waterfall.png") is None


def test_stream_from_dir_and_rows_agree(tmp_path):
    rng = np.random.default_rng(2)
    d = tmp_path / "demoddata"
    d.mkdir()
    a, b = fc_frame(1_000_000, rng), fc_frame(1_000_001, rng)
    (d / "data_1_2026-10-01T12-00-00_g0").write_bytes(a)
    (d / "data_1_2026-10-01T12-00-00_g1").write_bytes(a)            # decoder branch
    (d / "data_1_2026-10-01T12-00-05_g0").write_bytes(b)
    s1, norad = stream_from_dir(d, QC)
    assert norad == FC and len(s1) == 2 and s1[0]["decoder_branches"] == 2
    rows = [{"filename": f["filename"], "hex": f["data"].hex(), "decoder_branches": f["decoder_branches"],
             "n_copies": f["n_copies"], "copy_delays_s": ""} for f in s1]
    s2, _ = stream_from_rows(rows, QC)
    assert [(f["sha256"], f["counter"], f["t_utc"]) for f in s1] == [(f["sha256"], f["counter"], f["t_utc"]) for f in s2]
    assert infer_norad([ts_frame(1, rng)], QC) == TC


@pytest.mark.skipif(not (ROOT / "data/tables/frames.csv").exists(), reason="dataset tables not built")
def test_rows_reader_reproduces_table_counters():
    import pandas as pd
    t = pd.read_csv(ROOT / "data/tables/frames.csv", dtype={"copy_delays_s": str})
    obs = pd.read_csv(ROOT / "data/tables/observations.csv").set_index("obs_id")
    for oid, g in list(t.groupby("obs_id"))[:10]:
        s, _ = stream_from_rows(g.to_dict("records"), QC, int(obs.loc[oid, "norad"]))
        want = {r.sha256: (None if pd.isna(r.counter) else int(r.counter)) for r in g.itertuples()}
        assert {f["sha256"]: f["counter"] for f in s} == want
