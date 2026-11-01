"""Sequence-anomaly detector on the canonical frame stream of ONE recording (DETECTOR_SPEC Phase K).

Natural behaviour is learned on TRAIN clean frames per satellite (fit_profile, saved to
models/sequence/profile.json); a satellite with fewer than profile.min_frames train frames with a
counter also uses the pooled distribution of all satellites for the timestamp-driven evidence (same station
software, 1 s file timestamps): p = max(own smoothed p, pooled p), so a value the satellite shows naturally
stays natural. Evidence per frame, each with a natural probability p (Laplace-smoothed) and a reason;
frame score = -log10(min p):
  dup_payload   identical bytes to an earlier frame of the pass (after the canonical rewrite merge);
                the later copy is flagged                                               -> duplicate
  same_counter  same counter as another frame but different bytes; the member off the counter-time line
                is flagged, else the later one. Up to near_copy_max_bytes differing bytes = near copy
                (kind same_counter -> forged); more = a different frame on that counter (kind counter_clash)
  inversion     counter lower than the previous frame's in stream order, on the stream without the frames
                already explained as non-genuine copies (dup_payload / same_counter). Swap pattern (timing
                usable, both members within 1.5 counts of the line, opposite signs) flags both, with its own
                natural rate; otherwise only the member off the line (a counter spike), so one jumped counter
                never flags its clean neighbour (kind order_spike); without usable timing both members,
                generic rate                                                                 -> swap
  time_dev      deviation of counter * s_per_count - t from the pass line (mode, detect.replay_det
                .counter_line), probability of its exact 1 s bin (natural deviations are 0, +-1 and one
                count; a tail probability would make every shift look natural). Only when timing is usable
                (line fits and few overfull seconds)                               -> timing, counter_jump
  gap           missing counts just below this frame's counter (order-free: counters absent from the
                whole pass), tail probability under the natural loss distribution          -> deletion
  content       bytes at positions that are constant in clean frames of the same frame type changed
                (leave-one-observation-out tail on TRAIN)                                   -> forged
Deletions shorter than natural losses cannot be told apart; recall per subtype is reported, not hidden.
Causal scores (prefix of the stream) are tracked as in the replay detector.

CLI: python detect/sequence_det.py --obs-dir <recording folder>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.frames_io import read_metadata, stream_from_dir  # noqa: E402
from detect.hash_store import qc_view  # noqa: E402
from detect.replay_det import counter_line, s_per_count  # noqa: E402

KINDS = ("dup_payload", "same_counter", "inversion", "time_dev", "gap", "content")


def sat_params(norad: int | None, qc: dict) -> dict:
    """Counter units: seconds per count, counts per nominal step (FUNCUBE-1 1 per frame, TECHSAT 1B 30 s)."""
    spc = s_per_count(norad, qc)
    step = 1 if norad == qc["funcube"]["norad"] else 30
    return {"spc": spc, "step": step, "max_per_s": qc["max_frames_per_second"].get(norad, 1)
            if isinstance(qc["max_frames_per_second"], dict) else qc["max_frames_per_second"]}


def frame_type(f: dict, norad: int | None, qc: dict) -> int | None:
    if f["counter"] is None:
        return None
    return (f["data"][0] & 0x3F) if norad == qc["funcube"]["norad"] else 0


# ------------------------------------------------------------------------------------------- learning
def _constants(frames: list[dict], norad, qc, min_n: int) -> dict:
    vals, cnt = defaultdict(lambda: defaultdict(set)), Counter()
    for f in frames:
        t = frame_type(f, norad, qc)
        if t is None:
            continue
        cnt[t] += 1
        for p, b in enumerate(f["data"]):
            vals[t][p].add(b)
    return {t: {p: next(iter(s)) for p, s in v.items() if len(s) == 1} for t, v in vals.items() if cnt[t] >= min_n}


def violations(f: dict, const: dict, norad, qc) -> int | None:
    t = frame_type(f, norad, qc)
    prof = const.get(str(t), const.get(t)) if t is not None else None
    if prof is None:
        return None
    return sum(f["data"][int(p)] != v for p, v in prof.items() if int(p) < len(f["data"]))


def fit_profile(passes: dict[int, list[dict]], norad: int, qc: dict, p: dict) -> dict:
    """Natural statistics of one satellite from its TRAIN clean passes {obs_id: canonical stream}."""
    sp = sat_params(norad, qc)
    dev, gaps, n_frames, n_inv, n_dup, n_same, n_swap = Counter(), Counter(), 0, 0, 0, 0, 0
    for frames in passes.values():
        c = [f["counter"] for f in frames if f["counter"] is not None]
        n_frames += len(c)
        for kind, _, _ in inversions(frames, sp, p, set()):      # same classification as at detection time
            n_swap += kind == "swap"
            n_inv += kind != "swap"
        shas = [f["sha256"] for f in frames]
        n_dup += len(shas) - len(set(shas))
        by_c = defaultdict(set)
        for f in frames:
            if f["counter"] is not None:
                by_c[f["counter"]].add(f["sha256"])
        n_same += sum(len(s) - 1 for s in by_c.values())
        cs = sorted(set(c))
        for a, b in zip(cs, cs[1:]):
            gaps[max(0, round((b - a) / sp["step"]) - 1)] += 1
        line = counter_line(frames, sp["spc"], set(), p)
        if line["ok"]:
            for r in line["res"]:
                if r is not None:
                    dev[_bin(r - line["offset"], p)] += 1
    const = _constants([f for fs in passes.values() for f in fs], norad, qc, p["content_min_type_frames"])
    loo = Counter()
    for oid, frames in passes.items():
        cl = _constants([f for o, fs in passes.items() if o != oid for f in fs], norad, qc, p["content_min_type_frames"])
        for f in frames:
            v = violations(f, cl, norad, qc)
            if v is not None:
                loo[v] += 1
    return {"n_frames": n_frames, "n_inversions": n_inv, "n_swap_pattern": n_swap, "n_dup_payload": n_dup,
            "n_same_counter": n_same,
            "dev_bins": {str(k): v for k, v in sorted(dev.items())}, "gaps": {str(k): v for k, v in sorted(gaps.items())},
            "content_const": {str(t): {str(q): v for q, v in d.items()} for t, d in const.items()},
            "content_loo": {str(k): v for k, v in sorted(loo.items())}}


def _bin(d: float, p: dict) -> str:
    a = abs(d)
    return str(int(round(a))) if a <= p["dev_max_bin_s"] else "gt"


def pooled(profiles: dict) -> dict:
    """Pooled timestamp-driven statistics (deviation bins, inversion / duplicate rates) of all satellites."""
    dev = Counter()
    for pr in profiles.values():
        dev.update({k: v for k, v in pr["dev_bins"].items()})
    return {"n_frames": sum(pr["n_frames"] for pr in profiles.values()),
            "n_inversions": sum(pr["n_inversions"] for pr in profiles.values()),
            "n_swap_pattern": sum(pr["n_swap_pattern"] for pr in profiles.values()),
            "n_dup_payload": sum(pr["n_dup_payload"] for pr in profiles.values()),
            "n_same_counter": sum(pr["n_same_counter"] for pr in profiles.values()),
            "dev_bins": dict(dev)}


class Natural:
    """Probabilities of evidence values under the learned natural behaviour of one satellite."""

    def __init__(self, model: dict, norad: int | None, p: dict):
        sats = model["satellites"]
        own = sats.get(str(norad))
        self.p = p
        self.own = own
        small = own is None or own["n_frames"] < p["min_frames"]
        # timestamp-driven statistics: own; for a small satellite max(own, pooled) (conservative)
        if not small:
            self.stats = [own]
        else:
            self.stats = [model["pooled"]] + ([own] if own is not None else [])
        self.pooled_used = small
        self.n_bins = p["dev_max_bin_s"] + 2

    def rate(self, key: str) -> float:
        return max((st[key] + 1) / (st["n_frames"] + 1) for st in self.stats)

    def dev(self, d: float) -> float:
        return max((st["dev_bins"].get(_bin(d, self.p), 0) + 1) / (sum(st["dev_bins"].values()) + self.n_bins)
                   for st in self.stats)

    def gap(self, g: int) -> float:
        if self.own is None or g <= 0:
            return 1.0
        gs = {int(k): v for k, v in self.own["gaps"].items()}
        n = sum(gs.values())
        return (sum(v for k, v in gs.items() if k >= g) + 1) / (n + 1)

    def content(self, v: int) -> float:
        if self.own is None or v <= 0:
            return 1.0
        lo = {int(k): x for k, x in self.own["content_loo"].items()}
        n = sum(lo.values())
        return (sum(x for k, x in lo.items() if k >= v) + 1) / (n + 1)


# ------------------------------------------------------------------------------------------- scoring
def timing_usable(frames: list[dict], line: dict, sp: dict, p: dict) -> bool:
    """Detector-side timing reliability: the counter-time line fits and few seconds are overfull."""
    if not line["ok"] or not frames:
        return False
    per_s = Counter(f["t_utc"] for f in frames)
    over = sum(v > sp["max_per_s"] for v in per_s.values())
    return over <= p["timing_max_overfull_frac"] * max(1, len(per_s))


def line_and_timing(frames: list[dict], sp: dict, p: dict) -> tuple[dict, bool, list]:
    """Counter-time line, detector-side timing usability and per-frame deviation (s) from the line."""
    line = counter_line(frames, sp["spc"], set(), p)
    timing = timing_usable(frames, line, sp, p)
    off = line["offset"]
    dev = [(r - off) if (r is not None and off is not None and line["ok"]) else None for r in line["res"]]
    return line, timing, dev


def inversions(frames: list[dict], sp: dict, p: dict, skip: set, lt: tuple | None = None) -> list[tuple]:
    """Order breaks on the stream without `skip`: list of (kind, flagged frame indices, reason).

    kind 'swap' (swap pattern), 'spike' (only the off-line member), 'generic' (timing not usable).
    """
    line, timing, dev = lt or line_and_timing(frames, sp, p)
    count_s = (sp["spc"] or 1.0) * sp["step"]
    keep = [i for i, f in enumerate(frames) if i not in skip and f["counter"] is not None]
    out = []
    for x, y in zip(keep, keep[1:]):
        a, b = frames[x]["counter"], frames[y]["counter"]
        if b >= a:
            continue
        dx, dy = dev[x], dev[y]
        if timing and dx is not None and dy is not None:
            lim = p["swap_max_counts"] * count_s
            if abs(dx) <= lim and abs(dy) <= lim and dx * dy < 0:
                out.append(("swap", (x, y), f"counters {a} then {b}: adjacent frames out of order (swap pattern)"))
            else:
                k = x if abs(dx) > abs(dy) else y
                out.append(("spike", (k,), f"counter {frames[k]['counter']} breaks the order of its neighbours "
                                           f"({dev[k]:+.0f} s off the counter-time line)"))
        else:
            out.append(("generic", (x, y), f"counters {a} then {b}: frames out of order (timing not usable)"))
    return out


def score_frames(frames: list[dict], viol: list, nat: Natural, sp: dict, p: dict,
                 use: dict | None = None) -> tuple[list[dict], dict]:
    use = {k: True for k in KINDS} | (use or {})
    n = len(frames)
    out = [{"p": 1.0, "kinds": [], "reasons": []} for _ in range(n)]

    def add(i, kind, prob, reason):
        out[i]["kinds"].append(kind)
        out[i]["reasons"].append(f"{reason} (p={prob:.2g})")
        out[i]["p"] = min(out[i]["p"], prob)

    line, timing, dev = line_and_timing(frames, sp, p)
    off = line["offset"]
    count_s = (sp["spc"] or 1.0) * sp["step"]                 # seconds per nominal step
    explained = set()                                         # non-genuine copies: left out of the order analysis

    def genuine_of(idxs):
        """The member on the counter-time line (timing usable), else the first seen."""
        return min(idxs, key=lambda i: (abs(dev[i]) if dev[i] is not None and timing else 0.0, i))

    if use["dup_payload"]:
        by_sha = defaultdict(list)
        for i, f in enumerate(frames):
            by_sha[f["sha256"]].append(i)
        for idxs in by_sha.values():
            if len(idxs) < 2:
                continue
            j = genuine_of(idxs)
            for i in idxs:
                if i != j:
                    explained.add(i)
                    add(i, "dup_payload", nat.rate("n_dup_payload"), f"identical bytes to frame {frames[j]['filename']} "
                        f"({(frames[i]['t_utc'] - frames[j]['t_utc']).total_seconds():+.0f} s)")

    if use["same_counter"]:
        by_c = defaultdict(list)
        for i, f in enumerate(frames):
            if f["counter"] is not None:
                by_c[f["counter"]].append(i)
        for c, idxs in by_c.items():
            shas = {frames[i]["sha256"] for i in idxs}
            if len(shas) < 2:
                continue
            # one representative per distinct content; the first-seen content with the smallest deviation is genuine
            reps = {}
            for i in idxs:
                reps.setdefault(frames[i]["sha256"], i)
            cand = list(reps.values())
            genuine = genuine_of(cand)
            for i in cand:
                if i == genuine:
                    continue
                explained.add(i)
                nd = sum(a != b for a, b in zip(frames[i]["data"], frames[genuine]["data"])) + \
                    abs(len(frames[i]["data"]) - len(frames[genuine]["data"]))
                # near copy (few bytes differ) = forged; a different frame on the same counter = counter clash
                add(i, "same_counter" if nd <= p["near_copy_max_bytes"] else "counter_clash", nat.rate("n_same_counter"),
                    f"counter {c} already carried by frame {frames[genuine]['filename']} with different bytes "
                    f"({nd} byte(s) differ)")

    if use["inversion"]:
        for kind, idx, reason in inversions(frames, sp, p, explained, (line, timing, dev)):
            pr = nat.rate("n_swap_pattern" if kind == "swap" else "n_inversions")
            for k in idx:                                     # a lone off-line frame is named by fusion, not a swap
                add(k, "order_spike" if kind == "spike" else "inversion", pr, reason)

    if use["time_dev"] and timing:
        for i, d in enumerate(dev):
            if d is None:
                continue
            pr = nat.dev(d)
            if pr < p["report_p_max"]:
                add(i, "time_dev", pr, f"{d:+.0f} s off the counter-time line (counter {frames[i]['counter']})")

    if use["gap"]:
        cs = sorted({f["counter"] for f in frames if f["counter"] is not None})
        below = {c: (cs[k - 1] if k else None) for k, c in enumerate(cs)}
        for i, f in enumerate(frames):
            c = f["counter"]
            if c is None or below[c] is None:
                continue
            g = max(0, round((c - below[c]) / sp["step"]) - 1)
            pr = nat.gap(g)
            if g > 0 and pr < p["report_p_max"]:
                add(i, "gap", pr, f"{g} frame(s) missing just before counter {c}")

    if use["content"]:
        for i, v in enumerate(viol):
            if v:
                pr = nat.content(v)
                if pr < p["report_p_max"]:
                    add(i, "content", pr, f"{v} byte(s) changed at positions constant in clean frames of this type")

    # phase of each frame time on the pass rhythm (period = one nominal step): a timing shift moves it,
    # a counter jump does not (used to name the anomaly, not to score it)
    phase = [None] * n
    if timing and n:
        t0 = frames[0]["t_utc"]
        ph = [((f["t_utc"] - t0).total_seconds()) % count_s for f in frames]
        ref = max(set(round(x) for x in ph), key=lambda v: sum(abs(((x - v + count_s / 2) % count_s) - count_s / 2) <= 1 for x in ph))
        phase = [((x - ref + count_s / 2) % count_s) - count_s / 2 for x in ph]
    for o, ph_i, d in zip(out, phase, dev):
        o["score"] = -math.log10(o["p"])
        o["phase_dev_s"] = ph_i
        o["dev_s"] = d if timing else None
    diag = {"timing_usable": timing, "line_ok": line["ok"], "line_offset_s": off, "pooled_timestamp_stats": nat.pooled_used}
    return out, diag


def detect(frames: list[dict], norad: int | None, qc: dict, model: dict, causal: bool = True,
           use: dict | None = None) -> dict:
    p = model["params"]
    sp = sat_params(norad, qc)
    nat = Natural(model, norad, p)
    own = model["satellites"].get(str(norad))
    const = own["content_const"] if own else {}
    viol = [violations(f, const, norad, qc) if f["counter"] is not None else None for f in frames]
    final, diag = score_frames(frames, viol, nat, sp, p, use)
    t0 = frames[0]["t_utc"] if frames else None
    traj = [[] for _ in frames]
    if causal:
        best = [0.0] * len(frames)
        for k in range(1, len(frames) + 1):
            s, _ = score_frames(frames[:k], viol[:k], nat, sp, p, use)
            now = (frames[k - 1]["t_utc"] - t0).total_seconds()
            for j in range(k):
                if s[j]["score"] > best[j] + 1e-12:
                    best[j] = s[j]["score"]
                    traj[j].append([now, round(best[j], 6)])
    rows = [{"filename": f["filename"], "t_utc": f["t_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
             "t_rel_s": (f["t_utc"] - t0).total_seconds(), "counter": f["counter"], "score": s["score"],
             "phase_dev_s": s["phase_dev_s"], "dev_s": s["dev_s"],
             "trajectory": tr, "evidence": s["kinds"], "reason": "; ".join(s["reasons"])}
            for f, s, tr in zip(frames, final, traj)]
    return {"norad": norad, "n_frames": len(frames), "diagnostics": diag, "frames": rows}


def main(argv: list[str] | None = None) -> int:
    import yaml
    ap = argparse.ArgumentParser(description="Sequence-anomaly detector on one recording folder")
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    sc, qc = cfg["detectors"]["sequence"], qc_view(cfg)
    d = Path(a.obs_dir)
    if not (d / "demoddata").is_dir():
        res = {"available": False, "reason": "no demoddata/ folder: frame detectors unavailable"}
    else:
        model = json.loads(Path(sc["profile"]).read_text(encoding="utf-8"))
        meta = read_metadata(d)
        frames, norad = stream_from_dir(d / "demoddata", qc, meta.get("norad"))
        res = {"available": True, **detect(frames, norad, qc, model)}
        tp = Path(sc["threshold"])
        if tp.exists():
            tau = json.loads(tp.read_text(encoding="utf-8"))["tau"]
            res["tau"] = tau
            for r in res["frames"]:
                r["alert"] = r["score"] >= tau
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt + "\n", encoding="utf-8")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
