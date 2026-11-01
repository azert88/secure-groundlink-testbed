"""Phase F: attack and benign recipes (docs/DATASET_SPEC.md section 6). Deterministic, no audio read.

For every observation of data/splits.json, the variant mix of attacks.variants (spec 6.6, test
standard combo raised to 10) is drawn with seed = SHA-256 of f"{obs_id}:{variant_id}".
Inputs: data/tables/observations.csv and frames.csv (clean variant), data/splits.json.
Availability (decisions 37, 38, 46):
  audio groups (benign, interference, snrgrid, interference evasions) need the audio flag;
  frame groups (replay, sequence, combo, cross_pass) need the frame flag;
  the replay audio paste needs audio and reliable timing; the timing subtype needs reliable timing;
  counter_jump needs a decodable counter. A group that cannot be built is skipped and reported.
Output: attacks.recipes_dir/<obs_id>/<variant_id>.json and attacks.report_json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from attacks.common import recipe_seed  # noqa: E402
from attacks.replay import frame_spans  # noqa: E402


class Infeasible(Exception):
    pass


# ---- replay ----------------------------------------------------------------------------------

def free_intervals(times: list[float], half: float, duration: float) -> list[tuple[float, float]]:
    """Parts of [0, duration] not covered by [t - half, t + half] of any frame."""
    occ = sorted((t - half, t + half) for t in times)
    out, cur = [], 0.0
    for a, b in occ:
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < duration:
        out.append((cur, duration))
    return out


def plan_replay(rng, frames: pd.DataFrame, target: pd.DataFrame, duration: float, cfg: dict, airtime: float,
                audio: bool, subtype: str, source_obs: int | None, max_len: int | None = None) -> dict:
    """Consecutive frames [i, i+L) (whole bursts when possible) and an integer time shift delta.

    Every copied frame's span [t - half, t + half] (half = airtime + margin), merged into contiguous
    spans, must land in time free of the target's own frames and inside the audio (decision 60);
    intra_pass also needs |delta| >= min_delay_s. Later placements (delta > 0) are preferred (spec),
    earlier ones are the fallback.
    """
    rp = cfg["replay"]
    src = frames.sort_values("idx").reset_index(drop=True)
    n = len(src)
    hi_len = min(rp["length"][1], n, max_len if max_len is not None else n)
    if hi_len < rp["length"][0]:
        raise Infeasible("too few frames")
    L = int(rng.integers(rp["length"][0], hi_len + 1))
    starts = [i for i in range(n - L + 1) if i == 0 or src.burst_id[i] != src.burst_id[i - 1]] or list(range(n - L + 1))
    i = int(rng.choice(starts))
    j = i + L
    while j < n and j - i < hi_len and src.burst_id[j] == src.burst_id[j - 1]:
        j += 1                                       # extend to the end of the burst
    sel = src.iloc[i:j]
    half = airtime + rp["margin_s"]
    spans = frame_spans(list(sel.t_rel_s), half)
    gaps = np.array(free_intervals(list(target.t_rel_s), half, duration))
    if not len(gaps):
        raise Infeasible("no free time in the target")
    d = np.arange(-int(duration), int(duration) + 1)
    ok = np.ones(len(d), dtype=bool)
    for a, b in spans:                               # each span must fit inside one free interval
        ok &= ((d[:, None] >= gaps[None, :, 0] - a) & (d[:, None] <= gaps[None, :, 1] - b)).any(axis=1)
    if source_obs is None:
        ok &= np.abs(d) >= rp["min_delay_s"]
    valid = d[ok]
    if not len(valid):
        raise Infeasible("no gap holds the copied frames")
    later = valid[valid > 0]
    pool = later if len(later) else valid
    delta = int(rng.choice(pool))
    return {"type": "replay", "subtype": subtype, "evasion": int(subtype == "cross_pass"),
            "source_obs": source_obs, "source_filenames": list(sel.filename), "delta_s": float(delta),
            "placement": "later" if delta > 0 else "earlier", "length": int(len(sel)),
            "audio": bool(audio), "airtime_s": airtime, "margin_s": rp["margin_s"], "fade_ms": rp["fade_ms"]}


# ---- sequence --------------------------------------------------------------------------------

def plan_sequence(rng, frames: pd.DataFrame, cfg: dict, timing_reliable: bool, duration: float,
                  exclude: set | None = None) -> dict:
    """One sequence anomaly on the clean stream (never on the first or last frame)."""
    sq = cfg["sequence"]
    fr = frames.sort_values("idx").reset_index(drop=True)
    n = len(fr)
    inner = [k for k in range(1, n - 1) if fr.filename[k] not in (exclude or set())]
    has_ctr = fr.counter.notna()
    subtypes = [s for s in sq["subtypes"]
                if not (s == "timing" and not timing_reliable)
                and not (s == "counter_jump" and not has_ctr.iloc[1:-1].any())
                and not (s == "forged" and not (fr.length > sq["forged_protect_bytes"] + 1).any())]
    if n < 4 or not inner:
        raise Infeasible("too few frames")

    def one(st: str) -> dict:
        ex = exclude or set()
        free = lambda a, b: not ex & set(fr.filename[a:b])          # frames a..b-1 untouched by a replay
        if st == "deletion":
            k = int(rng.integers(sq["deletion_k"][0], sq["deletion_k"][1] + 1))
            ok = [i for i in range(1, n - k) if free(i, i + k + 1)]  # frames i..i+k-1 removed, i+k labelled
            if not ok:
                raise Infeasible("no deletion position")
            i = int(rng.choice(ok))
            return {"type": "sequence", "subtype": st, "filenames": list(fr.filename[i:i + k]),
                    "label_filename": fr.filename[i + k], "k": k}
        if st == "swap":
            ok = [i for i in range(1, n - 2) if free(i, i + 2)]
            if not ok:
                raise Infeasible("no swap position")
            i = int(rng.choice(ok))
            return {"type": "sequence", "subtype": st, "filenames": [fr.filename[i], fr.filename[i + 1]]}
        if st == "duplicate":
            ok = [k for k in inner if fr.t_rel_s[k + 1] - fr.t_rel_s[k] >= 2] or inner
            i = int(rng.choice(ok))     # >= 2 s apart, so the copy is not merged as a decoder branch
            t_new = float(math.floor((fr.t_rel_s[i] + fr.t_rel_s[i + 1]) / 2))
            return {"type": "sequence", "subtype": st, "source_filename": fr.filename[i], "t_new": t_new,
                    "byte_changes": []}
        if st == "forged":
            okl = [k for k in range(n) if fr.length[k] > sq["forged_protect_bytes"] + 1]
            s = int(rng.choice(okl))
            length = int(fr.length[s])
            nb = int(rng.integers(sq["forged_bytes"][0], sq["forged_bytes"][1] + 1))
            pos = sorted(rng.choice(np.arange(sq["forged_protect_bytes"], length),
                                    size=min(nb, length - sq["forged_protect_bytes"]), replace=False))
            data = bytes.fromhex(fr.hex[s])
            changes = [[int(p), int((data[p] + int(rng.integers(1, 256))) % 256)] for p in pos]
            dts = fr.t_rel_s.diff().dropna()
            med = float(dts[dts > 0].median()) if (dts > 0).any() else 5.0
            slots = [k for k in range(n - 1) if fr.t_rel_s[k + 1] - fr.t_rel_s[k] >= 2 * med]
            t_new = float(fr.t_rel_s[int(rng.choice(slots))] + round(med)) if slots else float(fr.t_rel_s.max() + round(med))
            if t_new >= duration:
                t_new = float(fr.t_rel_s[n - 2] + 1)
            return {"type": "sequence", "subtype": st, "source_filename": fr.filename[s], "t_new": t_new,
                    "byte_changes": changes}
        if st == "timing":
            lo, hi = fr.t_rel_s.min(), fr.t_rel_s.max()
            for _ in range(50):
                i = int(rng.choice(inner))
                shift = int(rng.integers(sq["timing_shift_s"][0], sq["timing_shift_s"][1] + 1)) * int(rng.choice([-1, 1]))
                if lo <= fr.t_rel_s[i] + shift <= hi:
                    return {"type": "sequence", "subtype": st, "filename": fr.filename[i], "shift_s": float(shift)}
            raise Infeasible("no timing shift inside the pass")
        ok = [k for k in inner if has_ctr[k]]
        if not ok:
            raise Infeasible("no frame with a counter")
        i = int(rng.choice(ok))
        return {"type": "sequence", "subtype": "counter_jump", "filename": fr.filename[i],
                "jump": int(rng.integers(sq["counter_jump"][0], sq["counter_jump"][1] + 1))}

    for st in rng.permutation(subtypes):          # seeded order; next subtype if one has no position
        try:
            return one(str(st))
        except Infeasible:
            continue
    raise Infeasible("no sequence anomaly fits")


# ---- interference and benign -----------------------------------------------------------------

def plan_interference(rng, duration: float, f_top: float, cfg: dict, snr_db: float | None = None,
                      subtype: str | None = None) -> dict:
    it, ev = cfg["interference"], cfg["evasion"]
    shape = "tone" if rng.random() < it["p_tone"] else "noise"
    if subtype == "drift":
        shape = "tone"
    bw = float(rng.uniform(*it["bw_hz"])) if shape == "noise" else None
    half = (bw or 0.0) / 2
    f0 = float(rng.uniform(it["f0_low_hz"] + half, f_top - half))
    if rng.random() < it["p_full"]:
        t0, t1 = 0.0, duration
    else:
        length = float(min(rng.uniform(*it["segment_s"]), duration))
        t0 = float(rng.uniform(0, duration - length))
        t1 = t0 + length
    if snr_db is None:
        rng_snr = ev["near_threshold_snr_db"] if subtype == "near_threshold" else it["snr_random_db"]
        snr_db = float(rng.uniform(*rng_snr))
    op = {"type": "interference", "shape": shape, "f0_hz": f0, "bw_hz": bw, "snr_db": float(snr_db),
          "phase": float(rng.uniform(0, 2 * np.pi)), "segments": [[t0, t1]], "drift_hz_s": 0.0,
          "subtype": subtype or shape, "evasion": int(subtype is not None)}
    if subtype == "intermittent":
        segs, t, on = [], t0, True
        while t < t1:
            d = float(rng.uniform(*ev["on_off_s"]))
            if on:
                segs.append([t, min(t + d, t1)])
            t, on = t + d, not on
        op["segments"] = segs
    elif subtype == "drift":
        rate = float(rng.uniform(ev["drift_min_hz_s"], ev["drift_max_hz_s"])) * float(rng.choice([-1, 1]))
        room = (f_top - f0) if rate > 0 else (f0 - it["f0_low_hz"])
        t1 = min(t1, t0 + room / abs(rate))        # stay inside the band
        op.update(drift_hz_s=rate, segments=[[t0, t1]])
    elif subtype == "strongest":
        op["f0_hz"] = "strongest"
    return op


def plan_benign(rng, duration: float, cfg: dict, n_ops: int | None = None) -> list[dict]:
    bn = cfg["benign"]
    k = n_ops or int(rng.integers(bn["n_ops"][0], bn["n_ops"][1] + 1))
    ops = []
    for name in rng.choice(bn["ops"], size=k, replace=False):
        if name == "gain":
            op = {"type": "gain", "factor": float(rng.uniform(*bn["gain_factor"])), "t0_s": None, "t1_s": None}
            if rng.random() >= bn["p_gain_whole"]:
                length = float(min(rng.uniform(*bn["gain_segment_s"]), duration))
                t0 = float(rng.uniform(0, duration - length))
                op.update(t0_s=t0, t1_s=t0 + length)
        elif name == "noise_rise":
            length = float(min(rng.uniform(*bn["noise_segment_s"]), duration))
            t0 = float(rng.uniform(0, duration - length))
            op = {"type": "noise_rise", "rise_db": float(rng.uniform(*bn["noise_rise_db"])), "t0_s": t0, "t1_s": t0 + length}
        elif name == "fading":
            op = {"type": "fading", "rate_hz": float(rng.uniform(*bn["fading_rate_hz"])),
                  "depth_db": float(rng.uniform(*bn["fading_depth_db"])), "phase": float(rng.uniform(0, 2 * np.pi))}
        else:
            a = float(min(rng.uniform(*bn["edge_ramp_s"]), duration / 3))
            b = float(min(rng.uniform(*bn["edge_ramp_s"]), duration / 3))
            op = {"type": "edge_attenuation", "start_s": a, "end_s": b}
        ops.append(op)
    return ops


# ---- per observation -------------------------------------------------------------------------

def variant_ops(group: str, k: int, rng, ob: dict, frames: pd.DataFrame, others: dict, cfg: dict) -> tuple[list, dict]:
    """Operations of one variant. ob: observation info; others: same-split frame observations."""
    ac = cfg["attacks"]
    dur, f_top = ob["duration_s"], ob["feature_band_hi_hz"]
    airtime = ac["replay"]["airtime_s"][ob["norad"]]
    replay_audio = ob["audio"] and ob["timing_reliable"]
    under = []
    if group in ("replay", "sequence", "combo") and ob["audio"] and rng.random() < ac["benign"]["p_under_attack"]:
        under = plan_benign(rng, dur, ac, n_ops=1)
    if group == "clean":
        return [], {}
    if group == "benign":
        return plan_benign(rng, dur, ac), {}
    if group == "interference":
        return [plan_interference(rng, dur, f_top, ac)], {}
    if group == "snrgrid":
        return [plan_interference(rng, dur, f_top, ac, snr_db=ac["interference"]["snr_grid_db"][k])], {}
    if group == "replay":
        return under + [plan_replay(rng, frames, frames, dur, ac, airtime, replay_audio, "intra_pass", None)], {}
    if group == "sequence":
        return under + [plan_sequence(rng, frames, ac, ob["timing_reliable"], dur)], {}
    if group == "combo":
        # leave at least 3 frames outside the replayed set for the sequence anomaly
        rp = plan_replay(rng, frames, frames, dur, ac, airtime, replay_audio, "intra_pass", None,
                         max_len=len(frames) - 3)
        sq = plan_sequence(rng, frames, ac, ob["timing_reliable"], dur, exclude=set(rp["source_filenames"]))
        return under + [rp, sq, plan_interference(rng, dur, f_top, ac)], {}
    if group == "evasion":
        st = ob["_evasion_subtypes"][k]
        if st == "cross_pass":
            src_id = int(rng.choice(sorted(others)))
            src = others[src_id]
            # paired audio only when both observations have usable audio and timing
            return [plan_replay(rng, src["frames"], frames, dur, ac, airtime,
                                replay_audio and src["audio"] and src["timing_reliable"], "cross_pass", src_id)], {}
        return [plan_interference(rng, dur, f_top, ac, subtype=st)], {}
    raise ValueError(group)


def observation_info(o: pd.Series, flags: dict, split: str) -> dict:
    return {"obs_id": int(o.obs_id), "norad": int(o.norad), "station_id": int(o.station_id), "split": split,
            "duration_s": float(o.duration_s) if pd.notna(o.duration_s) else None,
            "feature_band_hi_hz": float(o.feature_band_hi_hz) if pd.notna(o.feature_band_hi_hz) else None,
            "audio": bool(flags["audio"]), "frames": bool(flags["frames"]),
            "timing_reliable": bool(flags["timing_reliable"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    ac = cfg["attacks"]
    tdir = Path(cfg["tables"]["out_dir"])
    obs = pd.read_csv(tdir / "observations.csv")
    frames_all = pd.read_csv(tdir / "frames.csv")
    frames_all = frames_all[frames_all.variant_id == cfg["tables"]["clean_variant"]]
    splits = json.loads(Path(cfg["splits"]["out_json"]).read_text(encoding="utf-8"))
    out_dir = Path(ac["recipes_dir"])
    for old in out_dir.glob("*/*.json"):           # regenerate from scratch (deterministic)
        old.unlink()
    infos = {}
    for sat in splits["satellites"].values():
        for split, ids in sat["splits"].items():
            for i in ids:
                infos[i] = observation_info(obs[obs.obs_id == i].iloc[0], sat["obs"][str(i)], split)
    frames_by = {i: g for i, g in frames_all.groupby("obs_id")}
    report = {"recipes": 0, "by_group": Counter(), "skipped": [], "by_split_satellite": Counter()}
    for i in sorted(infos):
        ob = infos[i]
        fr = frames_by.get(i, frames_all.iloc[0:0])
        others = {j: {"frames": frames_by[j], "audio": infos[j]["audio"], "timing_reliable": infos[j]["timing_reliable"]}
                  for j in infos if j != i and infos[j]["norad"] == ob["norad"] and infos[j]["split"] == ob["split"]
                  and infos[j]["frames"] and j in frames_by}
        ev_avail = ([s for s in ac["evasion"]["subtypes"] if s != "cross_pass"] if ob["audio"] else []) + \
                   (["cross_pass"] if ob["frames"] and others else [])
        for group, counts in ac["variants"].items():
            n = counts[ob["split"]]
            needs = {"benign": ob["audio"], "interference": ob["audio"], "snrgrid": ob["audio"],
                     "replay": ob["frames"], "sequence": ob["frames"], "combo": ob["frames"] and ob["audio"],
                     "evasion": bool(ev_avail), "clean": True}[group]
            if n == 0 or not needs:
                if n:
                    report["skipped"].append({"obs_id": i, "group": group, "n": n, "reason": "not available"})
                continue
            if group == "evasion":
                r0 = np.random.default_rng(recipe_seed(i, "evasion"))
                ob["_evasion_subtypes"] = list(r0.choice(ev_avail, size=min(n, len(ev_avail)), replace=False))
                n = len(ob["_evasion_subtypes"])
            if group == "snrgrid":
                n = min(n, len(ac["interference"]["snr_grid_db"]))
            for k in range(n):
                vid = "clean" if group == "clean" else (
                    f"snrgrid_{ac['interference']['snr_grid_db'][k]:+d}" if group == "snrgrid" else f"{group}_{k:02d}")
                seed = recipe_seed(i, vid)
                rng = np.random.default_rng(seed)
                try:
                    ops, _ = variant_ops(group, k, rng, ob, fr, others, cfg)
                except Infeasible as exc:
                    report["skipped"].append({"obs_id": i, "variant_id": vid, "reason": str(exc)})
                    continue
                rec = {"obs_id": i, "variant_id": vid, "group": group, "split": ob["split"], "seed": seed,
                       "norad": ob["norad"], "operations": ops}
                p = out_dir / str(i) / f"{vid}.json"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
                report["recipes"] += 1
                report["by_group"][group] += 1
                report["by_split_satellite"][f"{ob['split']}/{ob['norad']}"] += 1
    report = {**report, "by_group": dict(report["by_group"]), "by_split_satellite": dict(report["by_split_satellite"])}
    Path(ac["report_json"]).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "skipped"} | {"n_skipped": len(report["skipped"])}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
