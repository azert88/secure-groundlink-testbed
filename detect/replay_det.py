"""Replay detector on the canonical frame stream of ONE recording (DETECTOR_SPEC Phase J).

Inputs: the canonical stream (detect.frames_io), the satellite, the pass start and a hash store of
EARLIER passes (detect.hash_store). No labels. Evidence per frame, each with a score and a reason:
  intra   bytes already received in this pass (> rewrite window, else the canonical stream merged it).
          Which member of a repeated pair is the copy: the one off the counter-time line when the line
          fits (copies may be placed before their originals), else the later one (first seen = original).
          Copies that share one time shift form a run (a replayed block keeps its spacing, even when it
          interleaves with the pass's own beacons): run >= 2 -> intra_run, else intra_single.
  cross   bytes of a frame archived from an earlier pass. A copy received within concurrent_tol_s of the
          archived reception is the same pass heard by another station: not a replay, reported as
          corroboration. Runs by common time shift as above.
          The counter-time line is the mode of counter * s_per_count - t over the pass (counter_line).
  regress counter <= highest counter of the line-consistent frames seen so far AND off the counter-time
          line by > line_tol_s (only when the line fits; with batched timestamps order is meaningless).
          Runs by common offset. Using line-consistent frames for the running maximum means one
          forward counter jump does not make every later frame a regression.
  rewrite the canonical stream merged byte-identical rewrites (n_copies > 0): low score.
Frame score = max over its evidence (levels in config detectors.replay.scores); reason = all evidence.
Causal scoring: the score of every frame is also tracked as frames arrive (prefix of the stream), so a
detection delay can be measured; `score` is the score once the whole pass is known.

CLI: python detect/replay_det.py --obs-dir <recording folder> [--threshold models/replay/threshold.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.frames_io import read_metadata, stream_from_dir  # noqa: E402
from detect.hash_store import HashStore, qc_view  # noqa: E402

INF = float("inf")


def s_per_count(norad: int | None, qc: dict) -> float | None:
    for k in ("funcube", "techsat"):
        if norad == qc[k]["norad"]:
            return float(qc[k]["s_per_count"])
    return None


def _clusters(items: list[tuple[int, float]], tol: float) -> list[list[int]]:
    """Group (frame index, value) by value: sorted values closer than tol chain into one cluster."""
    out, last = [], None
    for i, v in sorted(items, key=lambda x: x[1]):
        if last is not None and v - last <= tol:
            out[-1].append(i)
        else:
            out.append([i])
        last = v
    return out


def counter_line(frames: list[dict], spc: float | None, exclude: set, p: dict) -> dict:
    """Counter-time line of a pass: residual counter * spc - t (s) per frame and the pass offset.

    Offset = mode of the residuals (frames not in exclude): the residual whose +-line_tol_s/2 window holds
    the largest weight (singleton frames weigh 2, members of a repeated group 1, so originals + singletons
    always outweigh their copies), refined as the median of the frames within line_tol_s of it.
    Line usable (ok): >= line_ok_min_frac of the singletons on the line when there are >= line_min_frames
    singletons; otherwise more than half of the weight on the line and >= line_min_frames frames on it.
    """
    if spc is None or not frames:
        return {"res": [None] * len(frames), "offset": None, "ok": False, "frac": None, "n": 0}
    t0 = frames[0]["t_utc"]
    res = [f["counter"] * spc - (f["t_utc"] - t0).total_seconds() if f["counter"] is not None else None
           for f in frames]
    n_sha = defaultdict(int)
    for f in frames:
        n_sha[f["sha256"]] += 1
    idx = [i for i, r in enumerate(res) if r is not None and i not in exclude]
    if len(idx) < p["line_min_frames"]:
        return {"res": res, "offset": None, "ok": False, "frac": None, "n": len(idx)}
    tol = p["line_tol_s"]
    order = sorted(idx, key=lambda i: res[i])
    vals = [res[i] for i in order]
    cw = [0.0]
    for i in order:
        cw.append(cw[-1] + (2.0 if n_sha[frames[i]["sha256"]] == 1 else 1.0))
    best, best_w = None, -1.0
    for k, v in enumerate(vals):                          # window weight by bisection: O(n log n)
        w = cw[bisect_right(vals, v + tol / 2)] - cw[bisect_left(vals, v - tol / 2)]
        if w > best_w:
            best, best_w = v, w
    on = [i for i in idx if abs(res[i] - best) <= tol]
    off = statistics.median(res[i] for i in on)
    single = [i for i in idx if n_sha[frames[i]["sha256"]] == 1]
    if len(single) >= p["line_min_frames"]:
        frac = sum(abs(res[i] - off) <= tol for i in single) / len(single)
        ok = frac >= p["line_ok_min_frac"]
    else:
        on = [i for i in idx if abs(res[i] - off) <= tol]
        frac = sum(2.0 if n_sha[frames[i]["sha256"]] == 1 else 1.0 for i in on) / cw[-1]
        ok = frac > 0.5 and len(on) >= p["line_min_frames"]
    return {"res": res, "offset": off, "ok": ok, "frac": frac, "n": len(idx)}


def score_frames(frames: list[dict], hits: list[list], spc: float | None, p: dict,
                 use: dict | None = None) -> tuple[list[dict], dict]:
    """Batch evidence on a (prefix of a) stream. hits[i]: store lookups of frame i.

    use: switches for ablations {cross, intra, regress, rewrite, attribution, runs} (default all True).
    Returns (per-frame dicts {score, kinds, reasons}, diagnostics).
    """
    use = {"cross": True, "intra": True, "regress": True, "rewrite": True, "attribution": True, "runs": True,
           **(use or {})}
    sc, n = p["scores"], len(frames)
    out = [{"score": 0.0, "kinds": [], "reasons": []} for _ in range(n)]
    tight = p["concurrent_tol_s"]["line_ok"]

    def add(i, kind, reason):
        out[i]["kinds"].append(kind)
        out[i]["reasons"].append(reason)
        out[i]["score"] = max(out[i]["score"], float(sc[kind]))

    # nearest archived reception of each frame (seconds, signed: this frame minus archived)
    near = []
    for f, h in zip(frames, hits):
        best = min(h, key=lambda x: abs((f["t_utc"] - x[2]).total_seconds())) if h else None
        near.append((best, (f["t_utc"] - best[2]).total_seconds()) if best else (None, None))
    far = {i for i, (b, d) in enumerate(near) if b is not None and abs(d) > tight}
    line = counter_line(frames, spc, far, p)
    res, off, tol = line["res"], line["offset"], p["line_tol_s"]
    dev = [abs(r - off) if (r is not None and off is not None) else INF for r in res]
    run_tol = p["shift_group_tol_s"]

    def runs(items, kind_run, kind_single, tol_v, reason_fn):
        groups = _clusters(items, tol_v) if use["runs"] else [[i] for i, _ in items]
        for g in groups:
            for i in g:
                add(i, kind_run if len(g) >= 2 else kind_single, reason_fn(i, len(g)))

    # cross-pass: archived in an earlier pass
    if use["cross"]:
        ctol = p["concurrent_tol_s"]["line_ok" if line["ok"] else "batched"]
        items, info = [], {}
        for i, (b, d) in enumerate(near):
            if b is None:
                continue
            if abs(d) <= ctol:
                out[i]["reasons"].append(f"also received by another station in the same pass (obs {b[0]}, "
                                         f"dt {d:+.0f} s): corroborated, not a replay")
                continue
            items.append((i, d))
            info[i] = (b, d)
        runs(items, "cross_run", "cross_single", run_tol, lambda i, k: (
            f"same bytes as a frame of an earlier pass (obs {info[i][0][0]}, received "
            f"{info[i][0][2]:%Y-%m-%d %H:%M:%S} UTC, {info[i][1] / 3600:+.2f} h ago); {k} frame(s) share this shift"))

    # intra-pass repeats
    if use["intra"]:
        by_sha = defaultdict(list)
        for i, f in enumerate(frames):
            by_sha[f["sha256"]].append(i)
        items, info, resolved, pending = [], {}, [], []
        for idxs in by_sha.values():
            if len(idxs) < 2:
                continue
            orig, how = idxs[0], "first seen taken as original"
            if use["attribution"] and line["ok"]:
                d = [dev[i] for i in idxs]
                k = min(range(len(idxs)), key=lambda j: d[j])
                if d[k] <= tol and all(d[j] > tol for j in range(len(idxs)) if j != k):
                    orig, how = idxs[k], "original = the copy on the counter-time line"
            if how.startswith("first") and len(idxs) == 2:
                pending.append(idxs)                 # decided after the line-attributed pairs
                continue
            for i in idxs:
                if i != orig:
                    shift = (frames[i]["t_utc"] - frames[orig]["t_utc"]).total_seconds()
                    items.append((i, shift))
                    info[i] = (orig, shift, how)
                    if how.startswith("original"):
                        resolved.append(shift)
        # a pair the line cannot attribute (e.g. undecodable counter) follows its replayed block: if the
        # reversed shift matches a line-attributed shift (and the direct one does not), the earlier is the copy
        for a, b in pending:
            s = (frames[b]["t_utc"] - frames[a]["t_utc"]).total_seconds()
            fwd = any(abs(s - r) <= run_tol for r in resolved)
            rev = any(abs(-s - r) <= run_tol for r in resolved)
            orig, copy, how = (b, a, "attributed by the common shift of the replayed block") if (rev and not fwd) \
                else (a, b, "first seen taken as original")
            shift = (frames[copy]["t_utc"] - frames[orig]["t_utc"]).total_seconds()
            items.append((copy, shift))
            info[copy] = (orig, shift, how)
        runs(items, "intra_run", "intra_single", run_tol, lambda i, k: (
            f"same bytes as frame {frames[info[i][0]]['filename']} of this pass ({info[i][1]:+.0f} s; "
            f"{info[i][2]}); {k} frame(s) share this shift"))

    # counter regression, only when the counter-time line fits
    if use["regress"] and line["ok"]:
        items, info, top = [], {}, None
        for i, f in enumerate(frames):
            c = f["counter"]
            if c is None:
                continue
            if dev[i] > tol and top is not None and c <= top:
                items.append((i, res[i] - off))
                info[i] = (c, top, res[i] - off)
            if dev[i] <= tol:
                top = c if top is None else max(top, c)
        runs(items, "regress_run", "regress_single", tol, lambda i, k: (
            f"counter {info[i][0]} <= highest counter so far {info[i][1]} and {info[i][2]:+.0f} s off the "
            f"counter-time line; {k} frame(s) share this offset"))

    if use["rewrite"]:
        for i, f in enumerate(frames):
            if f["n_copies"] > 0:
                add(i, "rewrite", f"{f['n_copies']} byte-identical rewrite(s) merged "
                                  f"({', '.join(f'{d:g}' for d in f['copy_delays_s'])} s later): probable station rewrite")
    diag = {"line_ok": line["ok"], "line_offset_s": off, "line_frac_on": line["frac"], "line_n": line["n"]}
    return out, diag


def detect(frames: list[dict], norad: int | None, qc: dict, p: dict, store: HashStore | None = None,
           start_utc: datetime | None = None, exclude_obs: int | None = None, causal: bool = True,
           use: dict | None = None) -> dict:
    """Per-frame replay scores of one recording. start_utc: pass start (store sees earlier passes only)."""
    spc = s_per_count(norad, qc)
    if start_utc is None and frames:
        start_utc = frames[0]["t_utc"]
    hits = [store.lookup(norad, f["sha256"], start_utc, exclude_obs) if store is not None else [] for f in frames]
    final, diag = score_frames(frames, hits, spc, p, use)
    t0 = frames[0]["t_utc"] if frames else None
    traj = [[] for _ in frames]
    if causal:
        best = [0.0] * len(frames)
        for k in range(1, len(frames) + 1):
            s, _ = score_frames(frames[:k], hits[:k], spc, p, use)
            now = (frames[k - 1]["t_utc"] - t0).total_seconds()
            for j in range(k):
                if s[j]["score"] > best[j]:
                    best[j] = s[j]["score"]
                    traj[j].append([now, best[j]])
    rows = []
    for f, s, tr in zip(frames, final, traj):
        rows.append({"filename": f["filename"], "t_utc": f["t_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "t_rel_s": (f["t_utc"] - t0).total_seconds(), "counter": f["counter"],
                     "score": s["score"], "online_max": max([x[1] for x in tr], default=s["score"] if not causal else 0.0),
                     "trajectory": tr, "evidence": s["kinds"], "reason": "; ".join(s["reasons"])})
    return {"norad": norad, "n_frames": len(frames), "diagnostics": diag, "frames": rows}


def events(result: dict, tau: float) -> list[dict]:
    """Alert events: runs of alerted frames sharing their strongest evidence kind (for the report)."""
    ev = []
    for r in result["frames"]:
        if r["score"] < tau:
            continue
        kind = max(r["evidence"], key=lambda k: k.endswith("run")) if r["evidence"] else ""
        if ev and ev[-1]["kind"] == kind:
            ev[-1]["frames"].append(r["filename"])
            ev[-1]["t_last_utc"] = r["t_utc"]
        else:
            ev.append({"kind": kind, "frames": [r["filename"]], "t_first_utc": r["t_utc"], "t_last_utc": r["t_utc"],
                       "reason": r["reason"]})
    return ev


def load_params(cfg: dict) -> dict:
    """Replay parameters: models/replay/params.json if present (train-fitted copy), else the config."""
    p = Path(cfg["detectors"]["replay"]["params"])
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["params"]
    return cfg["detectors"]["replay"]


def main(argv: list[str] | None = None) -> int:
    import yaml
    ap = argparse.ArgumentParser(description="Replay detector on one recording folder (demoddata/, metadata.json optional)")
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--store", default=None, help="hash store (default: detectors.replay.store); 'none' to disable")
    ap.add_argument("--threshold", default=None, help="threshold JSON (default: detectors.replay.threshold)")
    ap.add_argument("--out", default=None, help="output JSON (default: stdout)")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    rc, qc = cfg["detectors"]["replay"], qc_view(cfg)
    d = Path(a.obs_dir)
    meta = read_metadata(d)
    if not (d / "demoddata").is_dir():
        res = {"available": False, "reason": "no demoddata/ folder: frame detectors unavailable"}
    else:
        frames, norad = stream_from_dir(d / "demoddata", qc, meta.get("norad"))
        sp = a.store or rc["store"]
        store = HashStore.load(Path(sp)) if sp != "none" and Path(sp).exists() else None
        res = {"available": True, **detect(frames, norad, qc, load_params(cfg), store, meta.get("start_utc"),
                                           meta.get("obs_id"))}
        tp = Path(a.threshold or rc["threshold"])
        if tp.exists():
            tau = json.loads(tp.read_text(encoding="utf-8"))["tau"]
            res["tau"] = tau
            res["events"] = events(res, tau)
            for r in res["frames"]:
                r["alert"] = r["score"] >= tau
        res["store_used"] = store is not None
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt + "\n", encoding="utf-8")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
