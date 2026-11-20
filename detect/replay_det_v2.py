"""Replay detector v2 (S1: docs/S1_PLAN.md, gate S1 in docs/GATES_PHASE2.md). v1 (detect/replay_det.py) is untouched.

Same evidence kinds, score levels, attribution and run logic as v1. What changes is WHICH frames are twins (one a
copy of the other):
  exact  same bytes (as v1)
  E1     same decoded counter, different bytes: a near-copy that kept its counter (TRAIN natural rate 0 / 0, P2-10)
  E2     same length and Hamming distance <= d bytes with the counter field, FUNCUBE-1 byte 0 and the TECHSAT 1B
         marker masked: a near-copy, also with a rewritten counter (d per satellite: FUNCUBE-1 10, TECHSAT 1B off,
         P2-10). v2.1 (P2-13, params models/replay_v21/params.json): d = 5 and e2_min_run = 2, i.e. an E2-only twin
         counts only when >= 2 frames share one time shift (within shift_group_tol_s); E1 and exact twins unaffected.
         The run filter uses the whole stream, so in the causal trajectory an E2 twin may appear one frame early.
  E3     robust counter-time line: line weights use twin groups instead of byte-identical groups, and a cluster of
         >= 2 frames below the line whose counters re-use this pass's counter range is set aside as copies when
         deciding whether the line is usable (decision 102). A counter reset (counters below the range) is not.
Twins within the pass -> intra evidence; twins of an earlier archived pass (detect/frame_store_v2.py) -> cross
evidence, with v1's simultaneous-reception exclusion (another station's copy within concurrent_tol_s is corroboration);
counter regression and rewrite as v1. Only v1 evidence names are emitted, so detect/naming.py and detect/fusion.py
apply unchanged.
"""

from __future__ import annotations

import json
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from detect import replay_det as v1
from detect.counters import counter_codec
from detect.replay_det import INF, _clusters, s_per_count

V2_KEYS = ("e1_same_counter", "e2_near_dup_d", "robust_line")


# ------------------------------------------------------------------------------------------------ twins
def protected_mask(norad: int | None, qc: dict, data: bytes) -> np.ndarray:
    """Bytes ignored by E2: counter field; FUNCUBE-1 byte 0 (frame type); TECHSAT 1B ASCII marker."""
    m = np.zeros(len(data), bool)
    if norad == qc["funcube"]["norad"]:
        s = qc["funcube"]["seq_byte"]
        pos = [0, s, s + 1, s + 2]
    elif norad == qc["techsat"]["norad"]:
        tc = qc["techsat"]
        c = tc["counter_byte"]
        k = data.find(tc["marker"].encode())
        pos = list(range(c, c + 4)) + (list(range(k, k + len(tc["marker"]))) if k >= 0 else [])
    else:
        pos = []
    m[[i for i in pos if 0 <= i < len(m)]] = True
    return m


def e2_distance(norad: int | None, p: dict) -> int | None:
    """E2 threshold d for a satellite, None when E2 is off (yaml int keys or json str keys)."""
    d = p.get("e2_near_dup_d") or {}
    v = d.get(str(norad), d.get(norad)) if norad is not None else None
    return None if v is None else int(v)


def _how(a: bytes, ca, b: bytes, cb, dist: int | None, d_max: int | None, e1: bool) -> str | None:
    if a == b:
        return "exact"
    if e1 and ca is not None and ca == cb:
        return "same counter"
    if d_max is not None and dist is not None and dist <= d_max:
        return f"near (d = {dist})"
    return None


def twin_pairs(frames: list[dict], norad: int | None, qc: dict, p: dict) -> dict[tuple[int, int], str]:
    """{(i, j): how} for i < j, twins within the pass."""
    d_max, e1 = e2_distance(norad, p), bool(p.get("e1_same_counter", True))
    out = {}
    masks = [protected_mask(norad, qc, f["data"]) for f in frames]
    by_len = defaultdict(list)
    for i, f in enumerate(frames):
        by_len[len(f["data"])].append(i)
    dist = {}
    if d_max is not None:
        for idx in by_len.values():
            if len(idx) < 2:
                continue
            A = np.array([np.frombuffer(frames[i]["data"], np.uint8) for i in idx])
            M = np.array([masks[i] for i in idx])
            for a, i in enumerate(idx):
                dd = ((A[a + 1:] != A[a]) & ~(M[a + 1:] | M[a])).sum(axis=1)
                for b, j in enumerate(idx[a + 1:]):
                    dist[(i, j)] = int(dd[b])
    by_c = defaultdict(list)
    for i, f in enumerate(frames):
        if f["counter"] is not None:
            by_c[f["counter"]].append(i)
    cand = set(dist) | {(a, b) for g in by_c.values() for x, a in enumerate(g) for b in g[x + 1:]}
    by_sha = defaultdict(list)
    for i, f in enumerate(frames):
        by_sha[f["sha256"]].append(i)
    cand |= {(a, b) for g in by_sha.values() for x, a in enumerate(g) for b in g[x + 1:]}
    for i, j in cand:
        h = _how(frames[i]["data"], frames[i]["counter"], frames[j]["data"], frames[j]["counter"],
                 dist.get((i, j)), d_max, e1)
        if h is not None:
            out[(i, j)] = h
    n_run = _min_run(p)
    if n_run > 1:                                         # v2.1: E2-only twins count only in a common-shift run
        near = [((i, j), j, (frames[j]["t_utc"] - frames[i]["t_utc"]).total_seconds())
                for (i, j), h in out.items() if h.startswith("near")]
        keep = _keep_runs(near, p["shift_group_tol_s"], n_run)
        out = {k: h for k, h in out.items() if not h.startswith("near") or k in keep}
    return out


def archive_hits(frames: list[dict], norad: int | None, qc: dict, p: dict, store, before_utc: datetime | None,
                 exclude_obs: int | None) -> list[list[tuple]]:
    """Per frame: archived twins [(obs_id, pass_start, frame_utc, how)] from passes starting before before_utc."""
    if store is None:
        return [[] for _ in frames]
    if not hasattr(store, "index"):                      # a v1 HashStore: exact matches only
        return [[(*h, "exact") for h in store.lookup(norad, f["sha256"], before_utc, exclude_obs)] for f in frames]
    ix = store.index(norad)
    ent = ix["entries"]
    if "v2" not in ix:
        dec, _ = counter_codec(norad, qc) if norad is not None else ((lambda b: None), None)
        ix["v2"] = {"counter": [dec(e["data"]) for e in ent],
                    "mask": {L: np.array([protected_mask(norad, qc, ent[i]["data"]) for i in idxs])
                             for L, idxs in ix["by_len"].items()}}
    cnt, emask = ix["v2"]["counter"], ix["v2"]["mask"]
    ok = np.array([(before_utc is None or e["ps"] < before_utc) and (exclude_obs is None or e["obs"] != exclude_obs)
                   for e in ent], bool)
    d_max, e1 = e2_distance(norad, p), bool(p.get("e1_same_counter", True))
    by_c = defaultdict(list)
    for k, c in enumerate(cnt):
        if c is not None and ok[k]:
            by_c[c].append(k)
    out = []
    for f in frames:
        x = np.frombuffer(f["data"], np.uint8)
        mx = protected_mask(norad, qc, f["data"])
        cand, dist = set(), {}
        L = len(f["data"])
        if L in ix["by_len"]:
            idxs = ix["by_len"][L]
            A, M = ix["arr"][L], emask[L]
            dd = ((A != x) & ~(M | mx)).sum(axis=1)
            exact = np.all(A == x, axis=1)
            for r, k in enumerate(idxs):
                if not ok[k]:
                    continue
                if exact[r] or (d_max is not None and dd[r] <= d_max):
                    cand.add(k)
                    dist[k] = int(dd[r])
        if e1 and f["counter"] is not None:
            cand |= set(by_c.get(f["counter"], []))
        hits = []
        for k in sorted(cand):
            h = _how(f["data"], f["counter"], ent[k]["data"], cnt[k], dist.get(k), d_max, e1)
            if h is not None:
                hits.append((ent[k]["obs"], ent[k]["ps"], ent[k]["fu"], h))
        out.append(hits)
    n_run = _min_run(p)
    if n_run > 1:                                         # v2.1: E2-only archive twins count only in a common-shift run
        near = [((i, k), i, (frames[i]["t_utc"] - h[2]).total_seconds())
                for i, hh in enumerate(out) for k, h in enumerate(hh) if h[3].startswith("near")]
        keep = _keep_runs(near, p["shift_group_tol_s"], n_run)
        out = [[h for k, h in enumerate(hh) if not h[3].startswith("near") or (i, k) in keep] for i, hh in enumerate(out)]
    return out


def _min_run(p: dict) -> int:
    return int(p.get("e2_min_run") or 1)


def _keep_runs(items: list[tuple], tol: float, n_run: int) -> set:
    """items (key, frame index, shift s): keys whose shift cluster (chained within tol) holds >= n_run distinct frames."""
    keep = set()
    for g in _clusters([(k, it[2]) for k, it in enumerate(items)], tol):
        if len({items[k][1] for k in g}) >= n_run:
            keep |= {items[k][0] for k in g}
    return keep


def _groups(k: int, twins: dict) -> list[list[int]]:
    """Connected components of the twin graph over frames 0..k-1."""
    parent = list(range(k))

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j) in twins:
        if i < k and j < k:
            parent[root(i)] = root(j)
    g = defaultdict(list)
    for i in range(k):
        g[root(i)].append(i)
    return [sorted(v) for v in g.values()]


# ------------------------------------------------------------------------------------------------ line (E3)
def counter_line_v2(frames: list[dict], spc: float | None, exclude: set, p: dict, gsize: list[int]) -> dict:
    """v1 counter_line with twin-group weights and the lower-counter copy cluster set aside (E3)."""
    if spc is None or not frames:
        return {"res": [None] * len(frames), "offset": None, "ok": False, "frac": None, "n": 0, "copies": []}
    t0 = frames[0]["t_utc"]
    res = [f["counter"] * spc - (f["t_utc"] - t0).total_seconds() if f["counter"] is not None else None
           for f in frames]
    idx = [i for i, r in enumerate(res) if r is not None and i not in exclude]
    if len(idx) < p["line_min_frames"]:
        return {"res": res, "offset": None, "ok": False, "frac": None, "n": len(idx), "copies": []}
    tol = p["line_tol_s"]
    w = {i: 2.0 if gsize[i] == 1 else 1.0 for i in idx}
    order = sorted(idx, key=lambda i: res[i])
    vals = [res[i] for i in order]
    cw = [0.0]
    for i in order:
        cw.append(cw[-1] + w[i])
    best, best_w = None, -1.0
    for v in vals:
        x = cw[bisect_right(vals, v + tol / 2)] - cw[bisect_left(vals, v - tol / 2)]
        if x > best_w:
            best, best_w = v, x
    on = [i for i in idx if abs(res[i] - best) <= tol]
    off = statistics.median(res[i] for i in on)
    on = [i for i in idx if abs(res[i] - off) <= tol]
    copies: set[int] = set()
    if p.get("robust_line", True) and on:
        lo, hi = min(frames[i]["counter"] for i in on), max(frames[i]["counter"] for i in on)
        below = [(i, res[i]) for i in idx if res[i] < off - tol]
        for g in _clusters(below, tol):
            if len(g) >= 2 and all(lo <= frames[i]["counter"] <= hi for i in g):
                copies |= set(g)
    use = [i for i in idx if i not in copies]
    single = [i for i in use if gsize[i] == 1]
    if len(single) >= p["line_min_frames"]:
        frac = sum(abs(res[i] - off) <= tol for i in single) / len(single)
        ok = frac >= p["line_ok_min_frac"]
    else:
        tot = sum(w[i] for i in use)
        frac = sum(w[i] for i in use if abs(res[i] - off) <= tol) / tot if tot else 0.0
        ok = frac > 0.5 and sum(abs(res[i] - off) <= tol for i in use) >= p["line_min_frames"]
    return {"res": res, "offset": off, "ok": ok, "frac": frac, "n": len(idx), "copies": sorted(copies)}


# ------------------------------------------------------------------------------------------------ scoring
def score_frames(frames: list[dict], hits: list[list], twins: dict, spc: float | None, p: dict,
                 use: dict | None = None) -> tuple[list[dict], dict]:
    """v1 score_frames with twins (exact, E1, E2) in place of byte-identical groups and the E3 line."""
    use = {"cross": True, "intra": True, "regress": True, "rewrite": True, "attribution": True, "runs": True,
           **(use or {})}
    sc, n = p["scores"], len(frames)
    out = [{"score": 0.0, "kinds": [], "reasons": []} for _ in range(n)]
    tight = p["concurrent_tol_s"]["line_ok"]

    def add(i, kind, reason):
        out[i]["kinds"].append(kind)
        out[i]["reasons"].append(reason)
        out[i]["score"] = max(out[i]["score"], float(sc[kind]))

    near = []
    for f, h in zip(frames, hits):
        best = min(h, key=lambda x: abs((f["t_utc"] - x[2]).total_seconds())) if h else None
        near.append((best, (f["t_utc"] - best[2]).total_seconds()) if best else (None, None))
    far = {i for i, (b, d) in enumerate(near) if b is not None and abs(d) > tight}
    groups = _groups(n, twins)
    gsize = [1] * n
    for g in groups:
        for i in g:
            gsize[i] = len(g)
    line = counter_line_v2(frames, spc, far, p, gsize)
    res, off, tol = line["res"], line["offset"], p["line_tol_s"]
    dev = [abs(r - off) if (r is not None and off is not None) else INF for r in res]
    run_tol = p["shift_group_tol_s"]

    def runs(items, kind_run, kind_single, tol_v, reason_fn):
        gs = _clusters(items, tol_v) if use["runs"] else [[i] for i, _ in items]
        for g in gs:
            for i in g:
                add(i, kind_run if len(g) >= 2 else kind_single, reason_fn(i, len(g)))

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
            f"{info[i][0][3]} twin of a frame of an earlier pass (obs {info[i][0][0]}, received "
            f"{info[i][0][2]:%Y-%m-%d %H:%M:%S} UTC, {info[i][1] / 3600:+.2f} h ago); {k} frame(s) share this shift"))

    if use["intra"]:
        items, info, resolved, pending = [], {}, [], []
        for idxs in groups:
            if len(idxs) < 2:
                continue
            orig, how = idxs[0], "first seen taken as original"
            if use["attribution"] and line["ok"]:
                d = [dev[i] for i in idxs]
                k = min(range(len(idxs)), key=lambda j: d[j])
                if d[k] <= tol and all(d[j] > tol for j in range(len(idxs)) if j != k):
                    orig, how = idxs[k], "original = the copy on the counter-time line"
            if how.startswith("first") and len(idxs) == 2:
                pending.append(idxs)
                continue
            for i in idxs:
                if i != orig:
                    shift = (frames[i]["t_utc"] - frames[orig]["t_utc"]).total_seconds()
                    items.append((i, shift))
                    info[i] = (orig, shift, how)
                    if how.startswith("original"):
                        resolved.append(shift)
        for a, b in pending:
            s = (frames[b]["t_utc"] - frames[a]["t_utc"]).total_seconds()
            fwd = any(abs(s - r) <= run_tol for r in resolved)
            rev = any(abs(-s - r) <= run_tol for r in resolved)
            orig, copy, how = (b, a, "attributed by the common shift of the replayed block") if (rev and not fwd) \
                else (a, b, "first seen taken as original")
            shift = (frames[copy]["t_utc"] - frames[orig]["t_utc"]).total_seconds()
            items.append((copy, shift))
            info[copy] = (orig, shift, how)

        def pair_how(i):
            o = info[i][0]
            return twins.get((min(i, o), max(i, o)), "twin-group")

        runs(items, "intra_run", "intra_single", run_tol, lambda i, k: (
            f"{pair_how(i)} twin of frame {frames[info[i][0]]['filename']} of this pass ({info[i][1]:+.0f} s; "
            f"{info[i][2]}); {k} frame(s) share this shift"))

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
    diag = {"line_ok": line["ok"], "line_offset_s": off, "line_frac_on": line["frac"], "line_n": line["n"],
            "line_copies_set_aside": len(line["copies"]),
            "twins": {h: sum(1 for x in twins.values() if x.split(" (")[0] == h) for h in ("exact", "same counter", "near")}}
    return out, diag


def detect(frames: list[dict], norad: int | None, qc: dict, p: dict, store=None,
           start_utc: datetime | None = None, exclude_obs: int | None = None, causal: bool = True,
           use: dict | None = None) -> dict:
    """Per-frame replay scores of one recording; same output format as replay_det.detect.

    store: a FrameStore (exact, E1, E2 archive matches) or a v1 HashStore (exact matches only)."""
    spc = s_per_count(norad, qc)
    if start_utc is None and frames:
        start_utc = frames[0]["t_utc"]
    hits = archive_hits(frames, norad, qc, p, store, start_utc, exclude_obs)
    twins = twin_pairs(frames, norad, qc, p)
    final, diag = score_frames(frames, hits, twins, spc, p, use)
    t0 = frames[0]["t_utc"] if frames else None
    traj = [[] for _ in frames]
    if causal:
        best = [0.0] * len(frames)
        for k in range(1, len(frames) + 1):
            s, _ = score_frames(frames[:k], hits[:k], twins, spc, p, use)
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
    return {"norad": norad, "n_frames": len(frames), "diagnostics": diag, "frames": rows, "version": "v2"}


events = v1.events


def load_params(cfg: dict, block: str = "replay_v2") -> dict:
    """v2 / v2.1 parameters: the block's params file if present, else v1's parameters + the block's config keys."""
    rc2 = cfg["detectors"][block]
    pp = Path(rc2["params"])
    if pp.exists():
        return json.loads(pp.read_text(encoding="utf-8"))["params"]
    return {**v1.load_params(cfg), **{k: rc2[k] for k in V2_KEYS if k in rc2}}
