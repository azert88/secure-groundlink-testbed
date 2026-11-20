"""S1 diagnosis (dev data only, no code change, no retuning): the replay v2 false alarms behind gate (b) and the newly
not-accepted recordings behind gate (c) of out/s1_dev/report.json (decision P2-12).

For every replay v2 frame alarm (score >= fold tau) on a clean / benign train + calib recording: obs, station, split,
variant, frame time, counter, timing reliability, the evidence that fired (exact hash / E1 same counter, different bytes
/ E2 masked Hamming / E3 robust line + regression / rewrite), the matched frame(s) (obs, time, counter, masked byte
distance, |dt|), the v1 score of the same frame (Phase J cache) and context flags (another station's reception within
the concurrent tolerance, station rewrite, counter reset, batched timestamps). Unique (obs, frame) pairs are counted.
For station 2107: every clean / benign recording not accepted by v2, its alerts (name, evidence, frames) and what v1
raised on the same recording.

Writes out/s1_dev/diag.json and prints a summary. Reads only train + calib (data/splits.json) and the Phase J / N
caches. Usage: python eval/s1_dev_diag.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
from detect import fusion as F  # noqa: E402
from detect import replay_det as v1  # noqa: E402
from detect import replay_det_v2 as v2  # noqa: E402
from detect.frame_store_v2 import FrameStore  # noqa: E402
from detect.frames_io import stream_from_rows  # noqa: E402
from detect.hash_store import qc_view  # noqa: E402
from eval.s1_dev_eval import OUT, _utc, d_key, dev_obs, kind, load_cfg, load_recs  # noqa: E402

STATION_C = 2107


def masked_d(norad, qc, a: bytes, b: bytes) -> int | None:
    if len(a) != len(b):
        return None
    m = v2.protected_mask(norad, qc, a) | v2.protected_mask(norad, qc, b)
    return int(((np.frombuffer(a, np.uint8) != np.frombuffer(b, np.uint8)) & ~m).sum())


def frames_of(cfg, qc, oid, vid, norad):
    fr = pd.read_csv(ROOT / cfg["features"]["out_dir"] / str(oid) / "frames.csv", dtype={"copy_delays_s": str})
    frames, _ = stream_from_rows(fr[fr["variant_id"] == vid].to_dict("records"), qc, norad)
    return frames


def v1_cache(cfg, split, oid):
    p = ROOT / cfg["detectors"]["eval_dir"] / "replay" / split / f"{oid}.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, usecols=["variant_id", "filename", "score", "evidence"])
    return {(r.variant_id, r.filename): (float(r.score), r.evidence if isinstance(r.evidence, str) else "")
            for r in df.itertuples()}


def explain(cfg, qc, store, p2, rec, frames, i, info, ctol):
    """Evidence of frame i of a recording, with matched frames."""
    norad, oid = rec["norad"], rec["obs_id"]
    f = frames[i]
    start = _utc(pd.read_csv(ROOT / "data/tables/observations.csv").set_index("obs_id").loc[oid, "start_utc"])
    twins = v2.twin_pairs(frames, norad, qc, p2)
    hits = v2.archive_hits(frames, norad, qc, p2, store, start, oid)
    _, diag = v2.score_frames(frames, hits, twins, v1.s_per_count(norad, qc), p2)
    # v1 line on the same frames (exact twins only) to see whether E3 made the line usable
    _, d1 = v1.score_frames(frames, [[h[:3] for h in hh if h[3] == "exact"] for hh in hits],
                            v1.s_per_count(norad, qc), {k: p2[k] for k in p2 if k not in v2.V2_KEYS})
    out = {"intra": [], "cross": []}
    for (a, b), how in twins.items():
        if i in (a, b):
            j = b if a == i else a
            g = frames[j]
            out["intra"].append({"how": how, "frame": g["filename"], "t": g["t_utc"].strftime("%H:%M:%S"),
                                 "counter": g["counter"], "masked_d": masked_d(norad, qc, f["data"], g["data"]),
                                 "dt_s": (g["t_utc"] - f["t_utc"]).total_seconds()})
    ix = store.index(norad)
    for obs_m, ps, fu, how in hits[i]:
        e = next((e for e in ix["entries"] if e["obs"] == obs_m and e["fu"] == fu), None)
        cnt = v2.counter_codec(norad, qc)[0](e["data"]) if e else None
        dt = (f["t_utc"] - fu).total_seconds()
        out["cross"].append({"how": how, "obs": obs_m, "t": fu.strftime("%Y-%m-%d %H:%M:%S"), "counter": cnt,
                             "masked_d": masked_d(norad, qc, f["data"], e["data"]) if e else None, "dt_s": dt,
                             "concurrent": abs(dt) <= ctol})
    tr = info["timing_reliable"]
    counters = [g["counter"] for g in frames if g["counter"] is not None]
    reset = any(b < a - 1000 for a, b in zip(counters, counters[1:]))
    return {**out, "n_copies": f["n_copies"], "timing_reliable": tr, "counter_reset_in_pass": reset,
            "line_ok_v2": diag["line_ok"], "line_ok_v1": d1["line_ok"], "line_copies_set_aside": diag["line_copies_set_aside"],
            "twin_counts": diag["twins"]}


def main() -> int:
    cfg = load_cfg("config.yaml")
    qc = qc_view(cfg)
    rep = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    folds = json.loads((OUT / "folds.json").read_text(encoding="utf-8"))
    taus = rep["fold_taus"]
    dev = dev_obs()
    store = FrameStore.load(ROOT / cfg["detectors"]["replay_v2"]["store"])
    base2 = {**v1.load_params(cfg), **{k: cfg["detectors"]["replay_v2"][k] for k in v2.V2_KEYS}}
    ctol_all = base2["concurrent_tol_s"]
    recs = [r for r in load_recs() if r["mode"] == "base" and kind(r["variant_id"]) in ("clean", "benign")]
    alarms, cache = [], {}
    for r in recs:
        s = str(r["station"])
        d = folds["folds"][s]["d"][str(r["norad"])]
        rows = r["v2"][d_key(r["norad"], d)] or []
        hit = [k for k, x in enumerate(rows) if x["score"] >= taus[s]]
        if not hit:
            continue
        frames = frames_of(cfg, qc, r["obs_id"], r["variant_id"], r["norad"])
        p2 = {**base2, "e2_near_dup_d": {str(r["norad"]): d}}
        info = dev[r["obs_id"]]
        ctol = ctol_all["line_ok"] if info["timing_reliable"] else ctol_all["batched"]
        if (r["split"], r["obs_id"]) not in cache:
            cache[(r["split"], r["obs_id"])] = v1_cache(cfg, r["split"], r["obs_id"])
        c1 = cache[(r["split"], r["obs_id"])]
        by_name = {g["filename"]: k for k, g in enumerate(frames)}
        for k in hit:
            x = rows[k]
            i = by_name[x["filename"]]
            alarms.append({"obs_id": r["obs_id"], "station": r["station"], "split": r["split"], "variant": r["variant_id"],
                           "frame": x["filename"], "t_utc": x["t_utc"], "counter": frames[i]["counter"],
                           "score": x["score"], "tau": taus[s], "evidence": x["evidence"], "reason": x["reason"],
                           "v1": c1.get((r["variant_id"], x["filename"])),
                           **explain(cfg, qc, store, p2, r, frames, i, info, ctol)})
    uniq = defaultdict(list)
    for a in alarms:
        uniq[(a["obs_id"], a["frame"])].append(a["variant"])
    # (c) station 2107: not accepted by v2, and what v1 raised
    from eval.fusion_dev import collect, run_fusion
    fcfg = F.load(ROOT / F.DEFAULT_FUSION)
    v2rows = {(r["obs_id"], r["variant_id"]): r for r in recs}
    c_out = []
    for x in collect(cfg):
        t = x["truth"]
        if x["station"] != STATION_C or t["replay"] or t["sequence"] or t["interference"] or t["group"] not in ("clean", "benign"):
            continue
        f1 = run_fusion(x, fcfg)
        y = dict(x)
        s = str(x["station"])
        r2 = v2rows.get((x["obs_id"], x["variant_id"]))
        if r2 is not None:
            y["replay"] = {"frames": r2["v2"][d_key(x["norad"], folds["folds"][s]["d"][str(x["norad"])])]}
        y["tau_r"] = taus[s]
        f2 = run_fusion(y, fcfg)
        if f2["verdict"] == "accept" and f1["verdict"] == "accept":
            continue
        al = lambda fu: [{"name": a["name"], "alert": a["alert"], "decision": a["decision"],
                          "evidence_score": a.get("evidence_score"), "frames": a.get("frames", [])[:6],
                          "reason": a.get("reason", "")[:200]} for a in fu["alerts"]]
        c_out.append({"obs_id": x["obs_id"], "variant": x["variant_id"], "split": x["split"],
                      "v1_verdict": f1["verdict"], "v2_verdict": f2["verdict"], "v1_alerts": al(f1), "v2_alerts": al(f2)})
    out = {"n_alarm_frames": len(alarms), "n_unique_obs_frames": len(uniq),
           "unique": [{"obs_id": o, "frame": fn, "variants": v} for (o, fn), v in uniq.items()],
           "alarms": alarms, "station_2107_not_accepted": c_out}
    (OUT / "diag.json").write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"(b) {len(alarms)} alarm frames, {len(uniq)} unique (obs, frame)")
    seen = set()
    for a in alarms:
        key = (a["obs_id"], a["frame"])
        if key in seen:
            continue
        seen.add(key)
        print(json.dumps({k: a[k] for k in ("obs_id", "station", "split", "frame", "counter", "score", "evidence", "v1",
                                             "intra", "cross", "n_copies", "timing_reliable", "counter_reset_in_pass",
                                             "line_ok_v1", "line_ok_v2", "line_copies_set_aside")} |
                         {"variants": uniq[key]}, default=str))
    print(f"(c) station {STATION_C}: {len(c_out)} clean/benign recordings not accepted by v1 or v2")
    for c in c_out:
        print(json.dumps({k: c[k] for k in ("obs_id", "variant", "split", "v1_verdict", "v2_verdict")} |
                         {"v1_alerts": [(a["name"], a["decision"]) for a in c["v1_alerts"]],
                          "v2_alerts": [(a["name"], a["decision"], a["frames"][:3]) for a in c["v2_alerts"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
