"""S1 development evaluation of replay v2: LOSO over TRAIN + CALIB stations (docs/S1_PLAN.md §4, gate S1 in
docs/GATES_PHASE2.md, frozen in models/gates_p2/lock.json). Labels are read here only. Test and data/holdout_p2/ are
never opened (split ids from data/splits.json; only train and calib observations are listed).

Stages (1 worker, resumable: one JSON per recording in out/s1_dev/rec/<obs_id>/<variant>__<mode>.json):
  prepare   v2 frame archive (detect/frame_store_v2.build_from_table: clean canonical frames of every Phase 1 pass,
            as v1's hash store; the local data/raw is incomplete) -> detectors.replay_v2.store;
            LOSO folds: per held-out station, E2 distance d for FUNCUBE-1 re-measured on TRAIN frames of the OTHER
            stations (same rule as P2-10: largest d with <= 0.1 % natural neighbours; TECHSAT 1B E2 off)
            -> out/s1_dev/folds.json
  score     per recording of train + calib (dataset variants from data/features/<obs>/frames.csv):
              base     v2 per-frame results for every fold distance d (v1 results come from the Phase J caches)
              nc<k>    variants that contain a replay: every replayed frame gets k bytes changed outside the counter /
                       marker / byte 0 (k = 1, 2, 4, 8, 16; k = 1 uses attacks.replay.near_copy), seed
                       recipe_seed(obs, "<variant>:s1-dev:<mode>:<idx>"); v1, v2 (held-out fold d) and sequence
              nc1_ctr  as nc1, and the counter of every copy rewritten to the value the pass's counter-time line
                       gives at the copy's time (attacker who forges a consistent counter)
  report    per fold: tau from the other stations (as eval/replay_eval.calibrate: max of calib and train-guard
            thresholds for target_fpr on not-attacked frames, per satellite); on the held-out station:
              (a) nc1 recordings named replay / replay_cross_pass on a replayed frame (fusion, frames only)
              (b) replay frame false alarms on clean + benign recordings (score >= tau)
              (c) clean + benign recordings not accepted (frozen fusion with the Phase N audio inputs, v2 replay)
            pooled over folds + worst station; stress curve (named rate per mode, v1 and v2). -> report.{json,md}

Usage:  python eval/s1_dev_eval.py prepare
        python eval/s1_dev_eval.py score [--obs ID ...] [--variants V ...] [--modes M ...] [--limit N]
        python eval/s1_dev_eval.py report [--skip-c]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
from attacks.common import recipe_seed  # noqa: E402
from attacks.replay import near_copy, protected_bytes  # noqa: E402
from detect import fusion as F  # noqa: E402
from detect import replay_det as v1  # noqa: E402
from detect import replay_det_v2 as v2  # noqa: E402
from detect import sequence_det  # noqa: E402
from detect.counters import counter_codec  # noqa: E402
from detect.frame_store_v2 import FrameStore, build_from_table  # noqa: E402
from detect.frames_io import stream_from_rows  # noqa: E402
from detect.hash_store import HashStore, qc_view  # noqa: E402
from eval import metrics as M  # noqa: E402
from eval import s1_natural_rates as NR  # noqa: E402

OUT = ROOT / "out/s1_dev"
MODES = ("nc1", "nc2", "nc4", "nc8", "nc16", "nc1_ctr")
SPLITS = ("train", "calib")
KEEP = ("filename", "t_utc", "t_rel_s", "score", "evidence", "reason", "trajectory", "phase_dev_s", "dev_s")
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _utc(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def load_cfg(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def dev_obs() -> dict[int, dict]:
    """obs_id -> {norad, split, station, timing_reliable} for TRAIN + CALIB only."""
    sp = json.loads((ROOT / "data/splits.json").read_text(encoding="utf-8"))
    out = {}
    for norad, s in sp["satellites"].items():
        for split in SPLITS:
            for o in s["splits"][split]:
                fl = s["obs"][str(o)]
                out[int(o)] = {"norad": int(norad), "split": split, "station": int(fl["station_id"]),
                               "timing_reliable": bool(fl["timing_reliable"])}
    return out


def d_key(norad: int, d: int | None) -> str:
    return "off" if d is None else str(d)


# ------------------------------------------------------------------------------------------------ prepare
def fold_distance(train_ids: list[int], norad: int, qc: dict, ctol: dict, timing: dict) -> int:
    """P2-10 rule on the given TRAIN observations: largest d with <= 0.1 % natural neighbours (within or across)."""
    frames = []
    for oid in train_ids:
        tol = ctol["line_ok"] if timing[oid] else ctol["batched"]
        frames += [{**f, "ctol": tol} for f in NR.load_frames(oid)]
    if not frames:
        return -1
    for f in frames:
        m = np.zeros(len(f["data"]), bool)
        m[[i for i in protected_bytes(norad, qc, f["data"]) if i < len(m)]] = True
        f["mask"] = m
    dist = NR.masked_min_dist(frames)
    both = np.minimum(dist["within"], dist["across"])
    d_max = -1
    for d in range(0, max(len(f["data"]) for f in frames) + 1):
        if (both <= d).sum() > NR.BUDGET * len(frames):
            break
        d_max = d
    return d_max


def prepare(cfg: dict) -> dict:
    qc = qc_view(cfg)
    rc2 = cfg["detectors"]["replay_v2"]
    t = time.perf_counter()
    st = build_from_table(ROOT / cfg["tables"]["out_dir"] / "frames.csv", ROOT / "data/tables/observations.csv", qc)
    st.save(ROOT / rc2["store"])
    ctol = json.loads((ROOT / cfg["detectors"]["replay"]["params"]).read_text(encoding="utf-8"))["params"]["concurrent_tol_s"]
    dev = dev_obs()
    timing = {o: v["timing_reliable"] for o, v in dev.items()}
    fc = qc["funcube"]["norad"]
    folds = {}
    for s in sorted({v["station"] for v in dev.values()}):
        d = {}
        for norad in sorted({v["norad"] for v in dev.values()}):
            cfg_d = v2.e2_distance(norad, rc2)
            if cfg_d is None:
                d[str(norad)] = None                          # E2 off for this satellite (P2-10)
                continue
            tr = [o for o, v in dev.items() if v["split"] == "train" and v["norad"] == norad and v["station"] != s]
            dm = fold_distance(tr, norad, qc, ctol, timing)
            d[str(norad)] = dm if dm >= 0 else None
        folds[str(s)] = {"obs": sorted(o for o, v in dev.items() if v["station"] == s), "d": d}
    dset = {str(n): sorted({f["d"][str(n)] for f in folds.values()}, key=lambda x: -1 if x is None else x)
            for n in {v["norad"] for v in dev.values()}}
    out = {"created_utc": datetime.now(timezone.utc).strftime(FMT), "funcube_norad": fc,
           "store_frames": st.n_frames(), "folds": folds, "distances": dset,
           "runtime_s": round(time.perf_counter() - t, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "folds.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out


# ------------------------------------------------------------------------------------------------ variants
def modify_bytes(hex_str: str, norad: int, qc: dict, k: int, seed: int) -> str:
    """k bytes changed outside the protected fields (k = 1: attacks.replay.near_copy, as Phase O)."""
    if k == 1:
        return near_copy(hex_str, norad, qc, seed)[0]
    b = bytearray(bytes.fromhex(hex_str))
    keep = protected_bytes(norad, qc, bytes(b))
    pos = [i for i in range(len(b)) if i not in keep]
    rng = np.random.default_rng(seed)
    for i, x in zip(rng.choice(pos, size=min(k, len(pos)), replace=False), rng.integers(1, 256, size=k)):
        b[int(i)] ^= int(x)
    return b.hex()


def line_counter(rows: list[dict], is_copy: set, norad: int, qc: dict, start: datetime):
    """Counter the pass's counter-time line gives at a time (median offset of the non-copied frames)."""
    spc = v1.s_per_count(norad, qc)
    dec, _ = counter_codec(norad, qc)
    res = []
    for r in rows:
        if r["filename"] in is_copy or not isinstance(r["hex"], str):
            continue
        c = dec(bytes.fromhex(r["hex"]))
        if c is not None:
            res.append(c * spc - (_utc(r["timestamp_utc"]) - start).total_seconds())
    if spc is None or not res:
        return None
    off = float(np.median(res))
    return lambda t: int(round(((t - start).total_seconds() + off) / spc))


def make_mode(rows: list[dict], replayed: set, mode: str, norad: int, qc: dict, oid: int, vid: str,
              start: datetime) -> list[dict]:
    k = int(re.match(r"nc(\d+)", mode).group(1))
    _, enc = counter_codec(norad, qc)
    lc = line_counter(rows, replayed, norad, qc, start) if mode.endswith("_ctr") else None
    out = []
    for r in rows:
        if r["filename"] in replayed and isinstance(r["hex"], str):
            h = modify_bytes(r["hex"], norad, qc, k, recipe_seed(oid, f"{vid}:s1-dev:{mode}:{r['idx']}"))
            if lc is not None and enc is not None:
                h = enc(bytes.fromhex(h), lc(_utc(r["timestamp_utc"]))).hex()
            r = {**r, "hex": h}
        out.append(r)
    return out


def slim(res: dict | None) -> list[dict] | None:
    if res is None:
        return None
    return [{k: r.get(k) for k in KEEP if k in r} for r in res["frames"]]


# ------------------------------------------------------------------------------------------------ score
def score(cfg: dict, only_obs: list[int] | None, only_var: list[str] | None, modes: list[str], limit: int | None) -> int:
    qc = qc_view(cfg)
    folds = json.loads((OUT / "folds.json").read_text(encoding="utf-8"))
    p1 = v1.load_params(cfg)
    base2 = {**p1, **{k: cfg["detectors"]["replay_v2"][k] for k in v2.V2_KEYS}}
    hs = HashStore.load(ROOT / cfg["detectors"]["replay"]["store"])
    fs = FrameStore.load(ROOT / cfg["detectors"]["replay_v2"]["store"])
    prof = json.loads((ROOT / cfg["detectors"]["sequence"]["profile"]).read_text(encoding="utf-8"))
    fcfg = F.load(ROOT / F.DEFAULT_FUSION)
    off = {k: False for k in fcfg.get("sequence_disabled_evidence", [])} or None
    obs = pd.read_csv(ROOT / "data/tables/observations.csv").set_index("obs_id")
    dev = dev_obs()
    n_done, t0 = 0, time.perf_counter()
    for oid in sorted(dev):
        if only_obs and oid not in only_obs:
            continue
        fp = ROOT / cfg["features"]["out_dir"] / str(oid) / "frames.csv"
        if not fp.exists():
            continue
        fr = pd.read_csv(fp, dtype={"copy_delays_s": str})
        if fr.empty:
            continue
        lab = pd.read_csv(fp.parent / "labels.csv")
        lab = lab[lab["unit"] == "frame"].rename(columns={"index": "idx"})
        fr = fr.merge(lab[["variant_id", "idx", "is_attack", "attack_type"]], on=["variant_id", "idx"], how="left")
        norad, info = dev[oid]["norad"], dev[oid]
        start = _utc(obs.loc[oid, "start_utc"])
        fold_d = folds["folds"][str(info["station"])]["d"][str(norad)]
        dists = folds["distances"][str(norad)]
        for vid, g in fr.groupby("variant_id", sort=True):
            if only_var and vid not in only_var:
                continue
            rows = g.to_dict("records")
            replayed = {r["filename"] for r in rows if r["is_attack"] == 1 and r["attack_type"] == "replay"}
            todo = [("base", None)] + ([(m, None) for m in modes] if replayed else [])
            for mode, _ in todo:
                p = OUT / "rec" / str(oid) / f"{vid}__{mode}.json"
                if p.exists():
                    continue
                if limit is not None and n_done >= limit:
                    return n_done
                t = time.perf_counter()
                rr = rows if mode == "base" else make_mode(rows, replayed, mode, norad, qc, oid, vid, start)
                frames, _ = stream_from_rows(rr, qc, norad)
                rec = {"obs_id": oid, "variant_id": vid, "mode": mode, **info, "replayed": sorted(replayed),
                       "n_frames": len(frames)}
                if mode == "base":
                    rec["v2"] = {d_key(norad, d): slim(v2.detect(frames, norad, qc, {**base2, "e2_near_dup_d":
                                                                                     {str(norad): d}}, fs, start, oid))
                                 for d in dists}
                else:
                    rec["v1"] = slim(v1.detect(frames, norad, qc, p1, hs, start, oid))
                    rec["v2"] = {d_key(norad, fold_d): slim(v2.detect(frames, norad, qc, {**base2, "e2_near_dup_d":
                                                                                         {str(norad): fold_d}}, fs, start, oid))}
                    rec["seq"] = slim(sequence_det.detect(frames, norad, qc, prof, use=off))
                rec["runtime_s"] = round(time.perf_counter() - t, 3)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(rec, default=str) + "\n", encoding="utf-8")
                tmp.replace(p)
                n_done += 1
                print(f"[S1 dev] {oid} {vid} {mode}: {len(frames)} frames, {rec['runtime_s']} s "
                      f"(total {time.perf_counter() - t0:.0f} s, {n_done} written)", file=sys.stderr, flush=True)
    return n_done


# ------------------------------------------------------------------------------------------------ report
def load_recs() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((OUT / "rec").glob("*/*.json"))]


def kind(vid: str) -> str:
    return re.sub(r"_\d+$", "", vid)


def frame_table(recs: list[dict], cfg: dict) -> pd.DataFrame:
    """Base recordings: one row per frame and distance key, with labels (train + calib only)."""
    parts = []
    for r in recs:
        if r["mode"] != "base":
            continue
        fp = ROOT / cfg["features"]["out_dir"] / str(r["obs_id"])
        fr = pd.read_csv(fp / "frames.csv", usecols=["variant_id", "idx", "filename"])
        fr = fr[fr["variant_id"] == r["variant_id"]]
        lab = pd.read_csv(fp / "labels.csv")
        lab = lab[(lab["unit"] == "frame") & (lab["variant_id"] == r["variant_id"])].rename(columns={"index": "idx"})
        lab = fr.merge(lab[["idx", "is_attack", "attack_type"]], on="idx").set_index("filename")
        for key, rows in r["v2"].items():
            for x in rows or []:
                lb = lab.loc[x["filename"]] if x["filename"] in lab.index else None
                parts.append({"obs_id": r["obs_id"], "variant_id": r["variant_id"], "kind": kind(r["variant_id"]),
                              "split": r["split"], "station": r["station"], "norad": r["norad"], "dkey": key,
                              "score": x["score"], "is_attack": int(lb["is_attack"]) if lb is not None else 0,
                              "pos": lb is not None and lb["is_attack"] == 1 and lb["attack_type"] == "replay"})
    return pd.DataFrame(parts)


def fold_tau(ft: pd.DataFrame, station: int, dkeys: dict, alpha: float) -> float:
    """eval/replay_eval.calibrate on the other stations: max(calib tau, train-guard tau), per satellite groups."""
    sel = (ft["station"] != station) & (ft["dkey"] == ft["norad"].astype(str).map(dkeys))
    o = ft[sel]
    extra = o.loc[o["pos"], "score"].to_numpy(float)
    taus = []
    for split in SPLITS:
        neg = o[(o["split"] == split) & (o["is_attack"] == 0)]
        if len(neg):
            taus.append(M.threshold_for_fpr(neg["score"].to_numpy(float), alpha, neg["norad"].to_numpy(),
                                            extra_candidates=extra))
    return max(taus) if taus else float("inf")


def named_replay(rep_rows, seq_rows, tau_r, tau_s, spc, fcfg, replayed) -> bool:
    fu = F.fuse({"frames": rep_rows} if rep_rows is not None else None, {"frames": seq_rows} if seq_rows else None,
                None, None, fcfg, tau_r, tau_s, spc, None, None)
    return any(f["name"] in F.REPLAY_NAMES and f["filename"] in replayed for f in fu["frames"])


def _rate(x) -> float | None:
    x = list(x)
    return round(float(np.mean(x)), 4) if x else None


def report(cfg: dict, skip_c: bool) -> dict:
    qc = qc_view(cfg)
    alpha = cfg["detectors"]["target_fpr"]
    folds = json.loads((OUT / "folds.json").read_text(encoding="utf-8"))
    recs = load_recs()
    ft = frame_table(recs, cfg)
    fcfg = F.load(ROOT / F.DEFAULT_FUSION)
    tau_s = json.loads((ROOT / cfg["detectors"]["sequence"]["threshold"]).read_text(encoding="utf-8"))["tau"]
    tau_v1 = json.loads((ROOT / cfg["detectors"]["replay"]["threshold"]).read_text(encoding="utf-8"))["tau"]
    taus, dk = {}, {}
    for s, f in folds["folds"].items():
        dk[s] = {n: d_key(int(n), d) for n, d in f["d"].items()}
        taus[s] = fold_tau(ft, int(s), dk[s], alpha) if not ft.empty else float("inf")
    # (b) frame false alarms on clean + benign of the held-out station
    b_rows = []
    for s in folds["folds"]:
        h = ft[(ft["station"] == int(s)) & ft["kind"].isin(["clean", "benign"]) & (ft["is_attack"] == 0)]
        h = h[h["dkey"] == h["norad"].astype(str).map(dk[s])]
        b_rows += [{"station": int(s), "alert": sc >= taus[s]} for sc in h["score"]]
    b = pd.DataFrame(b_rows)
    # (a) + stress: near-copy recordings named replay
    a_rows = []
    for r in recs:
        if r["mode"] == "base":
            continue
        s = str(r["station"])
        spc = sequence_det.sat_params(r["norad"], qc)["spc"]
        rep2 = next(iter(r["v2"].values()))
        a_rows.append({"station": r["station"], "norad": r["norad"], "mode": r["mode"],
                       "v2": named_replay(rep2, r["seq"], taus[s], tau_s, spc, fcfg, set(r["replayed"])),
                       "v1": named_replay(r["v1"], r["seq"], tau_v1, tau_s, spc, fcfg, set(r["replayed"]))})
    a = pd.DataFrame(a_rows)
    # (c) clean + benign recordings not accepted, frozen fusion with Phase N audio inputs
    c = pd.DataFrame()
    if not skip_c:
        from eval.fusion_dev import collect, run_fusion
        v2rows = {(r["obs_id"], r["variant_id"]): r for r in recs if r["mode"] == "base"}
        c_rows = []
        for x in collect(cfg):
            t = x["truth"]
            if t["replay"] or t["sequence"] or t["interference"] or t["group"] not in ("clean", "benign"):
                continue
            s = str(x["station"])
            row = {"station": x["station"], "obs_id": x["obs_id"], "variant_id": x["variant_id"],
                   "v1_not_accepted": run_fusion(x, fcfg)["verdict"] != "accept"}
            r2 = v2rows.get((x["obs_id"], x["variant_id"]))
            if x["replay"] is not None and r2 is None:
                row["v2_not_accepted"] = None                 # not scored yet
            else:
                y = dict(x)
                if r2 is not None:
                    y["replay"] = {"frames": r2["v2"][dk[s][str(x["norad"])]]}
                y["tau_r"] = taus[s]
                row["v2_not_accepted"] = run_fusion(y, fcfg)["verdict"] != "accept"
            c_rows.append(row)
        c = pd.DataFrame(c_rows)

    def pooled_worst(df, col, worst="max"):
        if df.empty or col not in df or df[col].dropna().empty:
            return {"pooled": None, "n": 0, "by_station": {}, "worst_station": None}
        d = df.dropna(subset=[col])
        by = {str(k): {"rate": _rate(g[col]), "n": int(len(g))} for k, g in d.groupby("station")}
        w = (max if worst == "max" else min)(by, key=lambda k: by[k]["rate"])
        return {"pooled": _rate(d[col]), "n": int(len(d)), "by_station": by, "worst_station": {w: by[w]}}

    a1 = a[a["mode"] == "nc1"] if not a.empty else a
    gate = {"a_near_copy_named_replay": pooled_worst(a1, "v2", "min"),
            "b_replay_frame_fa_clean_benign": pooled_worst(b, "alert"),
            "c_not_accepted_clean_benign": pooled_worst(c, "v2_not_accepted")}
    th = {"a": (">=", 0.80), "b": ("<=", 0.001), "c": ("<=", 0.136)}
    for k, (op, v) in th.items():
        g = gate[next(x for x in gate if x.startswith(k + "_"))]
        g["threshold"] = f"{op} {v}"
        g["pass"] = None if g["pooled"] is None else (g["pooled"] >= v if op == ">=" else g["pooled"] <= v)
    stress = {}
    if not a.empty:
        for m, g in a.groupby("mode"):
            stress[m] = {"n": int(len(g)), "v1": _rate(g["v1"]), "v2": _rate(g["v2"]),
                         "by_satellite": {str(n): {"n": int(len(h)), "v1": _rate(h["v1"]), "v2": _rate(h["v2"])}
                                          for n, h in g.groupby("norad")}}
    ref = {"v1_not_accepted_clean_benign": _rate(c["v1_not_accepted"]) if not c.empty else None,
           "v1_near_copy_named_nc1": _rate(a1["v1"]) if not a1.empty else None}
    n_total = sum(1 for _ in (OUT / "rec").glob("*/*.json"))
    rep = {"created_utc": datetime.now(timezone.utc).strftime(FMT), "recordings_scored": n_total,
           "fold_taus": taus, "fold_d": {s: f["d"] for s, f in folds["folds"].items()}, "gate": gate,
           "stress": stress, "reference_v1": ref, "skip_c": skip_c,
           "note": "pooled over LOSO folds (held-out station predictions); worst station reported next to it"}
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(markdown(rep), encoding="utf-8")
    return rep


def markdown(r: dict) -> str:
    L = ["# S1 replay v2: development evaluation (LOSO, train + calib)", "",
         f"{r['recordings_scored']} recordings scored. Gate pooled over folds; worst station next to it.", "",
         "| gate | pooled | n | threshold | pass | worst station |", "|---|---|---|---|---|---|"]
    for k, g in r["gate"].items():
        L.append(f"| {k} | {g['pooled']} | {g['n']} | {g.get('threshold')} | {g.get('pass')} | {g['worst_station']} |")
    L += ["", "| mode | n | v1 named | v2 named |", "|---|---|---|---|"]
    for m, s in r["stress"].items():
        L.append(f"| {m} | {s['n']} | {s['v1']} | {s['v2']} |")
    L += ["", f"Reference v1: {r['reference_v1']}", f"Fold taus: {r['fold_taus']}", f"Fold d: {r['fold_d']}"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S1 replay v2 development evaluation (LOSO, train + calib)")
    ap.add_argument("stage", choices=["prepare", "score", "report"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--obs", type=int, nargs="*", default=None)
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--modes", nargs="*", default=list(MODES), choices=MODES)
    ap.add_argument("--limit", type=int, default=None, help="stop after writing this many recordings")
    ap.add_argument("--skip-c", action="store_true", help="skip gate (c) (loads the Phase N audio inputs)")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.stage == "prepare":
        r = prepare(cfg)
        print(json.dumps({k: r[k] for k in ("store_frames", "distances", "runtime_s")} |
                         {"folds": {s: f["d"] for s, f in r["folds"].items()}}))
    elif a.stage == "score":
        print(json.dumps({"written": score(cfg, a.obs, a.variants, a.modes, a.limit)}))
    else:
        print(json.dumps(report(cfg, a.skip_c)["gate"], indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
