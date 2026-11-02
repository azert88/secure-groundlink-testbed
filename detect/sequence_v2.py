"""Sequence detector, version 2 (post hoc, decisions 84 / 85 changes (a) and (b) only; v1 stays the official result).

Everything is version 1 (detect/sequence_det.py: evidence, scoring, reasons) except:
  (a) small-satellite pooling: a satellite with fewer than min_frames train frames uses the POOLED timestamp-driven
      statistics, raised to its own smoothed p only for values it actually shows (count > 0). Version 1 took
      max(own, pooled) for every value, so the Laplace prior of 36 TECHSAT 1B frames capped any never-seen event at
      p >= 1/37 (score 1.57 < tau): TECHSAT 1B could never alert (decision 81).
  (b) content profile checked across stations: a byte position is kept as constant only if its frame type has
      >= content_min_type_frames clean frames at >= 2 stations (constancy seen across receivers, not one station's
      software), and the natural violation distribution (content_loo) is computed leave-one-STATION-out (constants
      learned without the station, violations counted on it), so the tail reflects a new receiver.
Nothing else changes (same parameters, same naming rule).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from detect import sequence_det as S


class NaturalV2(S.Natural):
    def __init__(self, model: dict, norad: int | None, p: dict):
        super().__init__(model, norad, p)
        self.pooled = model["pooled"]

    def rate(self, key: str) -> float:
        if not self.pooled_used:
            return super().rate(key)
        pr = (self.pooled[key] + 1) / (self.pooled["n_frames"] + 1)
        if self.own is not None and self.own.get(key, 0) > 0:
            pr = max(pr, (self.own[key] + 1) / (self.own["n_frames"] + 1))
        return pr

    def dev(self, d: float) -> float:
        if not self.pooled_used:
            return super().dev(d)
        b = S._bin(d, self.p)
        pd_ = self.pooled["dev_bins"]
        pr = (pd_.get(b, 0) + 1) / (sum(pd_.values()) + self.n_bins)
        if self.own is not None and self.own["dev_bins"].get(b, 0) > 0:
            od = self.own["dev_bins"]
            pr = max(pr, (od[b] + 1) / (sum(od.values()) + self.n_bins))
        return pr


def constants_across_stations(passes: dict, station: dict, norad, qc, min_n: int) -> dict:
    """Constant byte positions per frame type, kept only for types seen (>= min_n frames) at >= 2 stations."""
    const = S._constants([f for fs in passes.values() for f in fs], norad, qc, min_n)
    per_st = defaultdict(Counter)
    for oid, fs in passes.items():
        for f in fs:
            t = S.frame_type(f, norad, qc)
            if t is not None:
                per_st[t][station[oid]] += 1
    return {t: v for t, v in const.items() if sum(n >= min_n for n in per_st[t].values()) >= 2}


def fit_profile_v2(passes: dict[int, list[dict]], station: dict[int, int], norad: int, qc: dict, p: dict) -> dict:
    prof = S.fit_profile(passes, norad, qc, p)                 # v1 statistics (rates, deviations, gaps)
    mn = p["content_min_type_frames"]
    const = constants_across_stations(passes, station, norad, qc, mn)
    loo = Counter()
    for s in sorted(set(station[o] for o in passes)):
        rest = {o: fs for o, fs in passes.items() if station[o] != s}
        if not rest:
            continue
        cl = S._constants([f for fs in rest.values() for f in fs], norad, qc, mn)
        for o, fs in passes.items():
            if station[o] != s:
                continue
            for f in fs:
                v = S.violations(f, cl, norad, qc)
                if v is not None:
                    loo[v] += 1
    prof["content_const"] = {str(t): {str(q): v for q, v in d.items()} for t, d in const.items()}
    prof["content_loo"] = {str(k): v for k, v in sorted(loo.items())}
    prof["content_rule"] = "types seen at >= 2 stations; violation tail leave-one-station-out"
    return prof


def fit_model(passes_by_sat: dict, station: dict, qc: dict, p: dict) -> dict:
    sats = {str(n): fit_profile_v2(ps, station, n, qc, p) for n, ps in passes_by_sat.items()}
    return {"version": 2, "params": p, "satellites": sats, "pooled": S.pooled(sats)}


def detect(frames: list[dict], norad: int | None, qc: dict, model: dict, causal: bool = True,
           use: dict | None = None) -> dict:
    """Same output as detect.sequence_det.detect, with NaturalV2."""
    p = model["params"]
    sp = S.sat_params(norad, qc)
    nat = NaturalV2(model, norad, p)
    own = model["satellites"].get(str(norad))
    const = own["content_const"] if own else {}
    viol = [S.violations(f, const, norad, qc) if f["counter"] is not None else None for f in frames]
    final, diag = S.score_frames(frames, viol, nat, sp, p, use)
    t0 = frames[0]["t_utc"] if frames else None
    traj = [[] for _ in frames]
    if causal:
        best = [0.0] * len(frames)
        for k in range(1, len(frames) + 1):
            s, _ = S.score_frames(frames[:k], viol[:k], nat, sp, p, use)
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
    return {"norad": norad, "n_frames": len(frames), "diagnostics": diag, "frames": rows, "version": 2}
