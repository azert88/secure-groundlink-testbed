"""Phase J evaluation of the replay detector (DETECTOR_SPEC section 1). Labels are read here only.

Stages (run in order):
  fit        TRAIN only: build the hash store from raw demoddata (all archived passes, no labels), measure
             natural rates on TRAIN clean passes (repeats, rewrites, concurrent receptions, counter-time
             line), write models/replay/params.json.
  score      run the detector (+ ablations + baselines) on every variant of a split's observations; one
             cache file per observation (eval/out/replay/<split>/<obs>.csv), --resume skips done ones.
             The history of each recording = archived passes that start before it, never itself.
  calibrate  CALIB only: thresholds for target_fpr on not-attacked frames (pooled and per satellite) for
             the detector, each ablation and each baseline -> models/replay/threshold.json.
  report     metrics of a split -> eval/out/replay/report_<split>.{json,md}.
Test (score or report) requires a valid signed test lock (eval/lock.py, rule 10).

Baselines (rule 8): energy (max |power_db - variant median| over the windows around the frame),
n_earlier_same_bytes (number of EARLIER frames with the same bytes in this pass, plus archived copies, no
time rule),
counter_le_max (literal: counter <= highest counter seen so far in time order).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.frames_io import stream_from_dir, stream_from_rows  # noqa: E402
from detect.hash_store import HashStore, build_from_raw, qc_view  # noqa: E402
from detect.replay_det import counter_line, detect, s_per_count  # noqa: E402
from eval import metrics as M  # noqa: E402
from eval.lock import require_lock  # noqa: E402

ABLATIONS = {"no_cross": {"cross": False}, "no_intra": {"intra": False}, "no_regress": {"regress": False},
             "no_rewrite": {"rewrite": False}, "no_attribution": {"attribution": False}, "no_runs": {"runs": False}}
BASELINES = ["energy", "n_earlier_same_bytes", "counter_le_max"]
BINARY_BASELINES = ["n_earlier_same_bytes", "counter_le_max"]   # also reported at their natural threshold 1
FMT = "%Y-%m-%dT%H:%M:%SZ"
FRAME_INPUT_COLS = ["variant_id", "filename", "hex", "decoder_branches", "n_copies", "copy_delays_s"]


def _utc(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def load_cfg(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def split_ids(split: str) -> dict[int, int]:
    """obs_id -> norad of a split (data/splits.json)."""
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(o): int(n) for n, s in sp["satellites"].items() for o in s["splits"][split]}


def variant_kind(vid: str) -> str:
    return re.sub(r"_\d+$", "", vid)


def out_dir(cfg: dict) -> Path:
    return Path(cfg["detectors"]["eval_dir"]) / "replay"


# ------------------------------------------------------------------------------------------------ fit
def fit(cfg: dict) -> dict:
    qc, rc = qc_view(cfg), cfg["detectors"]["replay"]
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    store = build_from_raw(Path(cfg["download"]["raw_dir"]), sorted(obs.index), qc)
    store.save(Path(rc["store"]))
    train = split_ids("train")
    stats = defaultdict(lambda: defaultdict(list))
    for oid, norad in sorted(train.items()):
        frames, _ = stream_from_dir(Path(cfg["download"]["raw_dir"]) / str(oid) / "demoddata", qc, norad)
        if not frames:
            continue
        s = stats[norad]
        s["n_frames"].append(len(frames))
        s["n_rewritten"].append(sum(f["n_copies"] > 0 for f in frames))
        shas = [f["sha256"] for f in frames]
        s["n_intra_repeats"].append(len(shas) - len(set(shas)))
        start = _utc(obs.loc[oid, "start_utc"])
        for f in frames:
            for h in store.lookup(norad, f["sha256"], start, oid):
                s["concurrent_dt_s"].append((f["t_utc"] - h[2]).total_seconds())
        line = counter_line(frames, s_per_count(norad, qc), set(), rc)
        if line["offset"] is not None:
            s["line_frac_on"].append(line["frac"])
            s["line_ok"].append(int(line["ok"]))
            s["dev_s"] += [abs(r - line["offset"]) for r in line["res"] if r is not None]
    train_stats = {}
    for norad, s in stats.items():
        nf = sum(s["n_frames"])
        dev = np.asarray(s["dev_s"])
        cdt = np.abs(np.asarray(s["concurrent_dt_s"], dtype=float))
        train_stats[str(norad)] = {
            "observations": len(s["n_frames"]), "frames": nf,
            "natural_intra_repeat_rate": sum(s["n_intra_repeats"]) / nf if nf else None,
            "natural_rewrite_rate": sum(s["n_rewritten"]) / nf if nf else None,
            "concurrent_receptions": int(len(cdt)), "concurrent_abs_dt_max_s": float(cdt.max()) if len(cdt) else None,
            "line_fitted_obs": len(s["line_ok"]), "line_ok_obs": int(sum(s["line_ok"])),
            "line_dev_q50_q99_max_s": [float(np.quantile(dev, q)) for q in (0.5, 0.99)] + [float(dev.max())] if len(dev) else None,
            "share_frames_off_line": float((dev > rc["line_tol_s"]).mean()) if len(dev) else None}
    checks = {k: (v["concurrent_abs_dt_max_s"] or 0) <= rc["concurrent_tol_s"]["line_ok"] for k, v in train_stats.items()}
    params = {k: v for k, v in rc.items() if k not in ("store", "params", "threshold")}
    out = {"fitted_on": "train", "params": params, "train_stats": train_stats,
           "checks": {"concurrent_dt_within_tol": checks}, "store_frames": store.n_frames()}
    Path(rc["params"]).parent.mkdir(parents=True, exist_ok=True)
    Path(rc["params"]).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out


# ------------------------------------------------------------------------------------------------ score
def energy_scores(frames_rel: list[float], win: pd.DataFrame | None, half: float) -> list[float]:
    if win is None or win.empty:
        return [math.nan] * len(frames_rel)
    t, p = win["t_start_s"].to_numpy(), win["power_db"].to_numpy()
    med = np.nanmedian(p)
    out = []
    for x in frames_rel:
        m = (t + 1.0 >= x - half) & (t <= x + half)
        out.append(float(np.nanmax(np.abs(p[m] - med))) if m.any() else math.nan)
    return out


def score_obs(oid: int, norad: int, cfg: dict, store: HashStore, params: dict, start: datetime) -> pd.DataFrame:
    qc = qc_view(cfg)
    fdir = Path(cfg["features"]["out_dir"]) / str(oid)
    fr = pd.read_csv(fdir / "frames.csv", usecols=FRAME_INPUT_COLS, dtype={"copy_delays_s": str})
    if fr.empty:
        return pd.DataFrame()
    wp = fdir / "windows.csv"
    win = pd.read_csv(wp, usecols=["variant_id", "t_start_s", "power_db"]) if wp.exists() else pd.DataFrame()
    wins = {k: g for k, g in win.groupby("variant_id")} if not win.empty else {}
    half = cfg["attacks"]["replay"]["airtime_s"][norad] + cfg["attacks"]["replay"]["margin_s"]
    parts = []
    for vid, g in fr.groupby("variant_id", sort=True):
        frames, _ = stream_from_rows(g.to_dict("records"), qc, norad)
        main = detect(frames, norad, qc, params, store, start, oid, causal=True)
        rows = pd.DataFrame(main["frames"])
        rows.insert(0, "variant_id", vid)
        rows.insert(0, "norad", norad)
        rows.insert(0, "obs_id", oid)
        rows["line_ok"] = main["diagnostics"]["line_ok"]
        rows["trajectory"] = rows["trajectory"].map(json.dumps)
        rows["evidence"] = rows["evidence"].map(lambda e: ";".join(e))
        for name, use in ABLATIONS.items():
            rows[f"abl_{name}"] = [r["score"] for r in detect(frames, norad, qc, params, store, start, oid,
                                                                causal=False, use=use)["frames"]]
        # baselines
        rel = [(f["t_utc"] - start).total_seconds() for f in frames]
        rows["base_energy"] = energy_scores(rel, wins.get(vid), half)
        seen, early = defaultdict(int), []
        for f in frames:
            early.append(seen[f["sha256"]] + len(store.lookup(norad, f["sha256"], start, oid)))
            seen[f["sha256"]] += 1
        rows["base_n_earlier_same_bytes"] = early
        top, le = None, []
        for f in frames:
            c = f["counter"]
            le.append(float(c is not None and top is not None and c <= top))
            if c is not None:
                top = c if top is None else max(top, c)
        rows["base_counter_le_max"] = le
        parts.append(rows)
    return pd.concat(parts, ignore_index=True)


def score_split(cfg: dict, split: str, resume: bool) -> Path:
    if split == "test":
        require_lock("replay", cfg["detectors"]["models_dir"])
    rc = cfg["detectors"]["replay"]
    params = json.loads(Path(rc["params"]).read_text(encoding="utf-8"))["params"]
    store = HashStore.load(Path(rc["store"]))
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    d = out_dir(cfg) / split
    d.mkdir(parents=True, exist_ok=True)
    ids = split_ids(split)
    for i, (oid, norad) in enumerate(sorted(ids.items())):
        p = d / f"{oid}.csv"
        if resume and p.exists():
            continue
        df = score_obs(oid, norad, cfg, store, params, _utc(obs.loc[oid, "start_utc"]))
        tmp = p.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(p)                       # atomic: an interrupted run never leaves a partial cache
        print(f"[J score {split}] {i + 1}/{len(ids)} obs {oid}: {len(df)} frames", file=sys.stderr, flush=True)
    return d


# ------------------------------------------------------------------------------------------------ labels
def load_scored(cfg: dict, split: str) -> pd.DataFrame:
    """Score caches of a split joined with the frame labels (eval only)."""
    if split == "test":
        require_lock("replay", cfg["detectors"]["models_dir"])
    d = out_dir(cfg) / split
    parts = []
    for oid in sorted(split_ids(split)):
        p = d / f"{oid}.csv"
        if not p.exists() or p.stat().st_size < 5:
            continue
        s = pd.read_csv(p)
        if s.empty:
            continue
        fdir = Path(cfg["features"]["out_dir"]) / str(oid)
        fr = pd.read_csv(fdir / "frames.csv", usecols=["variant_id", "idx", "filename"])
        lab = pd.read_csv(fdir / "labels.csv")
        lab = lab[lab.unit == "frame"].rename(columns={"index": "idx"})
        m = s.merge(fr, on=["variant_id", "filename"], how="left").merge(
            lab[["variant_id", "idx", "is_attack", "attack_type", "subtype", "evasion"]], on=["variant_id", "idx"], how="left")
        if m["is_attack"].isna().any():
            raise RuntimeError(f"obs {oid}: frames without labels")
        parts.append(m)
    df = pd.concat(parts, ignore_index=True)
    df["kind"] = df["variant_id"].map(variant_kind)
    df["pos"] = (df["is_attack"] == 1) & (df["attack_type"] == "replay")
    df["neg"] = df["is_attack"] == 0
    df["neg_cat"] = np.where(df["kind"].isin(["clean", "benign"]), df["kind"], "other")
    return df


# ------------------------------------------------------------------------------------------------ calibrate
def score_columns(df: pd.DataFrame) -> dict[str, str]:
    cols = {"main": "score"}
    cols.update({f"abl_{k}": f"abl_{k}" for k in ABLATIONS})
    cols.update({f"base_{k}": f"base_{k}" for k in BASELINES})
    return {k: v for k, v in cols.items() if v in df.columns}


def _fpr(neg: pd.DataFrame, col: str, tau: float) -> dict:
    a = neg[col].fillna(-np.inf) >= tau
    return {"pooled": float(a.mean()), **{str(k): float(a[neg.norad == k].mean()) for k in sorted(neg.norad.unique())}}


def calibrate(cfg: dict) -> dict:
    """tau = max(tau on CALIB not-attacked frames, tau on TRAIN not-attacked frames).

    CALIB sets the threshold (rule 2); the TRAIN guard enforces the same target on the natural rates learned
    on train (calib holds no TECHSAT 1B replays and few rewrites, so calib alone cannot see every benign
    pattern). Both are pooled and per satellite. Test is never used.
    """
    df, tr = load_scored(cfg, "calib"), load_scored(cfg, "train")
    alpha = cfg["detectors"]["target_fpr"]
    neg, tneg = df[df.neg], tr[tr.neg]
    taus, fprs, parts = {}, {}, {}
    for name, col in score_columns(df).items():
        extra = np.concatenate([df.loc[df.pos, col].to_numpy(), tr.loc[tr.pos, col].to_numpy()])
        t_cal = M.threshold_for_fpr(neg[col].to_numpy(), alpha, neg["norad"].to_numpy(), extra_candidates=extra)
        t_tr = M.threshold_for_fpr(tneg[col].to_numpy(), alpha, tneg["norad"].to_numpy(), extra_candidates=extra)
        taus[name] = max(t_cal, t_tr)
        parts[name] = {"calib": t_cal, "train_guard": t_tr}
        fprs[name] = {"calib": _fpr(neg, col, taus[name]), "train": _fpr(tneg, col, taus[name])}
    strict = cfg["detectors"]["strict_fpr"]
    extra = np.concatenate([df.loc[df.pos, "score"].to_numpy(), tr.loc[tr.pos, "score"].to_numpy()])
    tau_strict = max(M.threshold_for_fpr(neg["score"].to_numpy(), strict, neg["norad"].to_numpy(), extra_candidates=extra),
                     M.threshold_for_fpr(tneg["score"].to_numpy(), strict, tneg["norad"].to_numpy(), extra_candidates=extra))
    out = {"tau": taus["main"], "taus": taus, "tau_parts": parts, "target_fpr": alpha, "fpr_at_tau": fprs,
           "tau_strict": tau_strict, "strict_fpr": strict,
           "fpr_at_tau_strict": {"calib": _fpr(neg, "score", tau_strict), "train": _fpr(tneg, "score", tau_strict)},
           "rule": "alert if score >= tau", "calibrated_on": "calib (train guard)",
           "n_not_attacked_frames": {"calib": int(len(neg)), "train": int(len(tneg))},
           "n_replay_frames": {"calib": int(df.pos.sum()), "train": int(tr.pos.sum())}}
    p = Path(cfg["detectors"]["replay"]["threshold"])
    p.write_text(json.dumps(out, indent=1, sort_keys=True, default=float) + "\n", encoding="utf-8")
    return out


# ------------------------------------------------------------------------------------------------ report
def first_alert(traj: str, tau: float) -> float:
    for t, s in json.loads(traj):
        if s >= tau:
            return float(t)
    return math.nan


def evaluate(df: pd.DataFrame, col: str, tau: float, bs: dict, seed: int, with_events: bool) -> dict:
    e = df[df.pos | df.neg].copy()
    e["alert"] = e[col].fillna(-np.inf) >= tau
    out = {"tau": tau, "pooled": M.binary(e.pos, e.alert)}
    out["fpr_by_category"] = {c: {"fpr": float(g.alert.mean()), "n": int(len(g))}
                              for c, g in e[e.neg].groupby("neg_cat")}
    po = M.per_obs(e, "pos", "alert")
    out["per_obs"] = {k: M.bootstrap_ci(po[k].to_numpy(), bs["n"], bs["ci"], seed) for k in ("precision", "recall", "f1", "fpr")}
    out["per_obs_n"] = {"obs": int(len(po)), "obs_with_replay": int(po[["tp", "fn"]].sum(axis=1).gt(0).sum())}
    out["eer"] = M.bootstrap_stat(e, lambda d: M.eer(d.pos, d[col]), n=bs["n"] // 4, ci=bs["ci"], seed=seed)
    out["auc"] = M.auc(e.pos, e[col])
    if with_events:
        ev = []
        for (oid, vid), g in e[e.pos].groupby(["obs_id", "variant_id"]):
            g = g.sort_values("t_rel_s")
            t_alert = min((first_alert(tr, tau) for tr in g.trajectory), default=math.nan, key=lambda x: (math.isnan(x), x))
            t0 = float(g.t_rel_s.iloc[0])
            ev.append({"obs_id": oid, "variant_id": vid, "norad": int(g.norad.iloc[0]), "subtype": g.subtype.iloc[0],
                       "evasion": int(g.evasion.iloc[0]), "n_copies": int(len(g)), "detected": bool(g.alert.any()),
                       "delay_s": t_alert - t0 if not math.isnan(t_alert) else math.nan,
                       "copies_until_alert": int((g.t_rel_s <= t_alert).sum()) if not math.isnan(t_alert) else None})
        ev = pd.DataFrame(ev)
        if not ev.empty:
            det = ev.groupby("obs_id").detected.mean().to_numpy()
            out["events"] = {"n": int(len(ev)), "detected": int(ev.detected.sum()),
                             "per_obs_detection_rate": M.bootstrap_ci(det, bs["n"], bs["ci"], seed),
                             "delay_s_median": float(ev.delay_s.median()), "delay_s_max": float(ev.delay_s.max()),
                             "copies_until_alert_median": float(ev.copies_until_alert.dropna().median())
                             if ev.copies_until_alert.notna().any() else None,
                             "by_subtype": {f"{k[0]}{'_evasion' if k[1] else ''}":
                                            {"n": int(len(g)), "detected": int(g.detected.sum())}
                                            for k, g in ev.groupby(["subtype", "evasion"])}}
        # online false alarms: not-attacked frames that were ever alerted while the pass was arriving
        on = e[e.neg].trajectory.map(lambda tr: not math.isnan(first_alert(tr, tau)))
        out["online_fpr"] = float(on.mean())
    out["by_norad"] = {str(k): {**M.binary(g.pos, g.alert), "eer": M.eer(g.pos, g[col])} for k, g in e.groupby("norad")}
    sub = {}
    for k, g in e[e.pos].groupby(["subtype", "evasion"]):
        sub[f"{k[0]}{'_evasion' if k[1] else ''}"] = {"n": int(len(g)), "recall": float(g.alert.mean())}
    out["recall_by_subtype"] = sub
    seq = df[(df.is_attack == 1) & (df.attack_type != "replay")]
    out["fires_on_other_attacks"] = {f"{a}/{s}": {"n": int(len(g)), "alert_rate": float((g[col].fillna(-np.inf) >= tau).mean())}
                                     for (a, s), g in seq.groupby(["attack_type", "subtype"])}
    return out


def report(cfg: dict, split: str) -> dict:
    df = load_scored(cfg, split)
    th = json.loads(Path(cfg["detectors"]["replay"]["threshold"]).read_text(encoding="utf-8"))
    bs, seed = cfg["detectors"]["bootstrap"], cfg["seed"]
    res = {"split": split, "n_obs": int(df.obs_id.nunique()), "n_frames": int(len(df)),
           "n_replay_frames": int(df.pos.sum()), "n_not_attacked_frames": int(df.neg.sum()),
           "main": evaluate(df, "score", th["taus"]["main"], bs, seed, True),
           "main_strict": evaluate(df, "score", th["tau_strict"], bs, seed, True)}
    res["baselines"] = {k: evaluate(df, f"base_{k}", th["taus"][f"base_{k}"], bs, seed, False) for k in BASELINES}
    res["baselines_natural"] = {k: evaluate(df, f"base_{k}", 1.0, bs, seed, False) for k in BINARY_BASELINES}
    res["ablations"] = {k: evaluate(df, f"abl_{k}", th["taus"][f"abl_{k}"], bs, seed, False) for k in ABLATIONS}
    res["energy_coverage"] = float(df.base_energy.notna().mean())
    res["strict_fpr"] = th["strict_fpr"]
    d = out_dir(cfg)
    (d / f"report_{split}.json").write_text(json.dumps(res, indent=1, default=float) + "\n", encoding="utf-8")
    (d / f"report_{split}.md").write_text(markdown(res), encoding="utf-8")
    return res


def _tau(t: float) -> str:
    """Threshold for display; a tau just above a score level (nextafter) is shown as '> level'."""
    if math.isinf(t):
        return "inf"
    r = round(t, 9)
    return f"> {r:g}" if t > r else f"{t:.4g}"


def _ci(t) -> str:
    v, lo, hi = t
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.3f} [{lo:.3f}, {hi:.3f}]"


def markdown(r: dict) -> str:
    m = r["main"]
    L = [f"# Replay detector — {r['split']}", "",
         f"{r['n_obs']} observations, {r['n_frames']} frames ({r['n_replay_frames']} replay, "
         f"{r['n_not_attacked_frames']} not attacked). Threshold tau = {_tau(m['tau'])} (score >= tau).", "",
         "## Per observation (mean, 95% bootstrap CI over observations)", "",
         "| metric | value |", "|---|---|"]
    L += [f"| {k} | {_ci(v)} |" for k, v in m["per_obs"].items()]
    L += [f"| EER (pooled, CI) | {_ci(m['eer'])} |", f"| ROC AUC (pooled) | {m['auc']:.3f} |", "",
          "## Pooled", "", "| tp | fp | fn | tn | precision | recall | F1 | FPR |", "|---|---|---|---|---|---|---|---|",
          "| {tp} | {fp} | {fn} | {tn} | {precision:.3f} | {recall:.3f} | {f1:.3f} | {fpr:.4f} |".format(**m["pooled"]), "",
          "FPR by category: " + ", ".join(f"{k} {v['fpr']:.4f} (n={v['n']})" for k, v in m["fpr_by_category"].items())
          + f"; online (ever alerted) FPR {m.get('online_fpr', float('nan')):.4f}", ""]
    if "events" in m:
        ev = m["events"]
        L += ["## Events", "", f"{ev['detected']} / {ev['n']} replay events detected; per-observation detection rate "
              f"{_ci(ev['per_obs_detection_rate'])}; median delay {ev['delay_s_median']:.1f} s (max {ev['delay_s_max']:.1f} s), "
              f"median copies until alert {ev['copies_until_alert_median']}.", "",
              "| subtype | events | detected |", "|---|---|---|"]
        L += [f"| {k} | {v['n']} | {v['detected']} |" for k, v in ev["by_subtype"].items()]
        L.append("")
    L += ["## Per satellite", "", "| NORAD | tp | fp | fn | tn | recall | FPR | EER |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {k} | {v['tp']} | {v['fp']} | {v['fn']} | {v['tn']} | {v['recall']:.3f} | {v['fpr']:.4f} | {v['eer']:.3f} |"
          for k, v in m["by_norad"].items()]
    L += ["", "Fires on other attacks: " + (", ".join(f"{k} {v['alert_rate']:.2f} (n={v['n']})"
                                                     for k, v in m["fires_on_other_attacks"].items()) or "none"), "",
          "## Baselines and ablations (same splits, thresholds for the same target FPR on calib)", "",
          "| detector | tau | recall | FPR | F1 | per-obs F1 | EER |", "|---|---|---|---|---|---|---|"]
    names = {"baselines": "baseline", "baselines_natural": "baseline (natural tau=1, FPR not controlled)",
             "ablations": "ablation"}
    for grp in ("baselines", "baselines_natural", "ablations"):
        for k, v in r[grp].items():
            p = v["pooled"]
            L.append(f"| {names[grp]} {k} | {_tau(v['tau'])} | {p['recall']:.3f} | {p['fpr']:.4f} | {p['f1']:.3f} | "
                     f"{_ci(v['per_obs']['f1'])} | {_ci(v['eer'])} |")
    st = r["main_strict"]
    L += ["", f"Strict operating point (target {r.get('strict_fpr', 0.001):g} FPR): tau {_tau(st['tau'])}, recall "
          f"{st['pooled']['recall']:.3f}, FPR {st['pooled']['fpr']:.4f}, per-obs F1 {_ci(st['per_obs']['f1'])}, "
          f"events {st.get('events', {}).get('detected')} / {st.get('events', {}).get('n')}.",
          f"Energy baseline coverage (frames with audio windows): {r['energy_coverage']:.2f}.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase J: replay detector fit / score / calibrate / report")
    ap.add_argument("stage", choices=["fit", "score", "calibrate", "report"])
    ap.add_argument("--split", choices=["train", "calib", "test"], default="calib")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.stage == "fit":
        r = fit(cfg)
        print(json.dumps({"train_stats": r["train_stats"], "checks": r["checks"]}, indent=1))
    elif a.stage == "score":
        print(json.dumps({"cache": score_split(cfg, a.split, a.resume).as_posix()}))
    elif a.stage == "calibrate":
        r = calibrate(cfg)
        print(json.dumps({k: r[k] for k in ("taus", "tau_parts", "fpr_at_tau")}, indent=1, default=float))
    else:
        r = report(cfg, a.split)
        print(markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
