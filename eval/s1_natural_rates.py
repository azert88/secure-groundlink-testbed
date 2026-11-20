"""S1 step 2: natural near-duplicate and same-counter rates on clean TRAIN frames only (docs/S1_PLAN.md §3, §6).

Reads data/splits.json for the TRAIN observation ids of each satellite (all of them, whatever their QC `frames` flag,
as models/replay/params.json train_stats) and opens ONLY data/features/<obs_id>/frames.csv of those ids (rows with
variant_id == "clean"). Calib, test and data/holdout_p2/ are never opened.

Bytes masked before any comparison: the counter field, byte 0 (FUNCUBE-1) and the ASCII marker (TECHSAT 1B), i.e.
attacks.replay.protected_bytes of either frame of a pair. Only frames of the same length are compared.
Concurrent receptions (the same pass heard by two stations: other observation, |dt| <= concurrent_tol_s of
models/replay/params.json, `line_ok` if both observations are timing-reliable else `batched`) are not a natural
neighbour for v2 (v1 already reports them as corroboration); they are counted separately.
  a) per frame: minimum masked Hamming distance (bytes) to any OTHER frame of the same pass, and to any
     non-concurrent frame of the other TRAIN passes; cumulative counts of frames with a neighbour at distance <= d.
  b) pairs with the same decoded counter but different bytes: within a pass, across passes (non-concurrent),
     and concurrent.
  c) d_max = largest d such that <= 0.1 % of TRAIN frames have a natural neighbour (within or across) at <= d
     (-1 if even d = 0 exceeds the budget).
Output: models/replay_v2/near_dup_rates.json and a printed copy.

Usage: python eval/s1_natural_rates.py [--config config.yaml]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from attacks.replay import protected_bytes  # noqa: E402
from detect.counters import counter_codec  # noqa: E402
from detect.hash_store import qc_view  # noqa: E402

DISTS = [0, 1, 2, 4, 8, 16, 32]
BUDGET = 0.001
OUT = ROOT / "models/replay_v2/near_dup_rates.json"


def load_frames(obs_id: int) -> list[dict]:
    """Clean canonical frames of one TRAIN observation: bytes and reception time (epoch seconds)."""
    p = ROOT / "data/features" / str(obs_id) / "frames.csv"
    if not p.exists():
        return []
    out = []
    with open(p, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["variant_id"] == "clean" and r["hex"]:
                t = datetime.strptime(r["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                out.append({"obs": obs_id, "data": bytes.fromhex(r["hex"]), "t": t.timestamp()})
    return out


def concurrent_matrix(t: np.ndarray, obs: np.ndarray, tol: np.ndarray) -> np.ndarray:
    """conc[i, j]: other observation and |t_i - t_j| <= tol (tol per pair, tol[i] max tol[j])."""
    return (obs[:, None] != obs[None, :]) & (np.abs(t[:, None] - t[None, :]) <= np.maximum(tol[:, None], tol[None, :]))


def masked_min_dist(frames: list[dict]) -> dict:
    """Per frame: min distance within pass, across passes (non-concurrent), to concurrent receptions; inf if none."""
    n = len(frames)
    res = {k: [np.inf] * n for k in ("within", "across", "concurrent")}
    by_len = defaultdict(list)
    for i, f in enumerate(frames):
        by_len[len(f["data"])].append(i)
    for idx in by_len.values():
        A = np.array([np.frombuffer(frames[i]["data"], np.uint8) for i in idx])
        M = np.array([frames[i]["mask"] for i in idx])
        obs = np.array([frames[i]["obs"] for i in idx])
        conc = concurrent_matrix(np.array([frames[i]["t"] for i in idx]), obs, np.array([frames[i]["ctol"] for i in idx]))
        for k, i in enumerate(idx):
            d = ((A != A[k]) & ~(M | M[k])).sum(axis=1).astype(float)
            d[k] = np.inf
            same = obs == obs[k]
            for key, sel in (("within", same), ("across", ~same & ~conc[k]), ("concurrent", conc[k])):
                if sel.any():
                    res[key][i] = float(d[sel].min())
    return res


def counter_pairs(frames: list[dict]) -> dict:
    by_c = defaultdict(list)
    for f in frames:
        if f["counter"] is not None:
            by_c[f["counter"]].append(f)
    out = {"within_pass": 0, "across_passes": 0, "concurrent": 0}
    for g in by_c.values():
        for x in range(len(g)):
            for y in range(x + 1, len(g)):
                a, b = g[x], g[y]
                if a["data"] == b["data"]:
                    continue
                if a["obs"] == b["obs"]:
                    out["within_pass"] += 1
                elif abs(a["t"] - b["t"]) <= max(a["ctol"], b["ctol"]):
                    out["concurrent"] += 1
                else:
                    out["across_passes"] += 1
    return out


def cum(v) -> dict:
    v = np.asarray(v, float)
    return {str(d): int((v <= d).sum()) for d in DISTS} | {"none": int(np.isinf(v).sum())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load((ROOT / a.config).read_text(encoding="utf-8"))
    qc = qc_view(cfg)
    ctol = json.loads((ROOT / "models/replay/params.json").read_text(encoding="utf-8"))["params"]["concurrent_tol_s"]
    splits = json.loads((ROOT / "data/splits.json").read_text(encoding="utf-8"))
    rep = {"created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "split": "train",
           "masked": "counter field; FUNCUBE-1 byte 0; TECHSAT 1B ASCII marker (attacks.replay.protected_bytes)",
           "concurrent_tol_s": ctol, "budget_frac": BUDGET, "satellites": {}}
    for key, sat in splits["satellites"].items():
        norad = int(key)                                    # splits.json keys are NORAD ids
        ids = sorted(int(o) for o in sat["splits"]["train"])
        frames = []
        for oid in ids:
            tol = ctol["line_ok"] if sat["obs"][str(oid)]["timing_reliable"] else ctol["batched"]
            for f in load_frames(oid):
                frames.append({**f, "ctol": tol})
        if not frames:
            continue
        dec, _ = counter_codec(norad, qc)
        for f in frames:
            m = np.zeros(len(f["data"]), bool)
            m[[i for i in protected_bytes(norad, qc, f["data"]) if i < len(m)]] = True
            f["mask"], f["counter"] = m, dec(f["data"])
        dist = masked_min_dist(frames)
        both = np.minimum(dist["within"], dist["across"])
        n = len(frames)
        d_max = -1
        for d in range(0, max(len(f["data"]) for f in frames) + 1):
            if (both <= d).sum() > BUDGET * n:
                break
            d_max = d
        lengths = defaultdict(int)
        for f in frames:
            lengths[len(f["data"])] += 1
        rep["satellites"][key] = {
            "train_observations": len(ids), "observations_with_frames": len({f["obs"] for f in frames}),
            "frames": n, "frames_with_counter": sum(f["counter"] is not None for f in frames),
            "lengths": {str(k): v for k, v in sorted(lengths.items())},
            "a_cum_within_pass": cum(dist["within"]), "a_cum_across_passes": cum(dist["across"]),
            "a_cum_any": cum(both), "a_cum_concurrent_excluded": cum(dist["concurrent"]),
            "b_same_counter_diff_bytes": counter_pairs(frames), "c_d_max": d_max,
            "c_note": "largest d with <= 0.1 % of TRAIN frames having a natural neighbour (within or across) at <= d"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
