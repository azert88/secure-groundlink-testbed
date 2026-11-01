"""Phase K evaluation of the sequence detector (DETECTOR_SPEC section 2). Labels are read here only.

Stages: fit (TRAIN clean passes from raw demoddata -> models/sequence/profile.json), score (one cache per
observation in eval/out/sequence/<split>/, --resume), calibrate (CALIB + TRAIN guard, as Phase J, decision
72), report. Test (score or report) requires the signed test lock models/sequence/lock.json.

Positives: frames with attack_type sequence; negatives: not-attacked frames; replay frames are excluded
(reported as 'fires on other attacks'). Recall per subtype; the timing subtype only on timing-reliable
observations (data/splits.json flag). Naming (detect/naming.py) is evaluated with the frozen replay
detector's cached scores at its tau.
Baselines (rule 8): energy (as Phase J), counter_step (|counter step - nominal step| in steps, stream
order), abs_time_dev (|counter-time deviation| in s, when the line fits).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.frames_io import stream_from_dir, stream_from_rows  # noqa: E402
from detect.hash_store import qc_view  # noqa: E402
from detect.naming import name_frame  # noqa: E402
from detect.replay_det import counter_line  # noqa: E402
from detect.sequence_det import KINDS, detect, fit_profile, pooled, sat_params  # noqa: E402
from eval import metrics as M  # noqa: E402
from eval.lock import require_lock  # noqa: E402
from eval.replay_eval import (FRAME_INPUT_COLS, _ci, _fpr, _tau, _utc, energy_scores, first_alert,  # noqa: E402
                              load_cfg, split_ids, variant_kind)

ABLATIONS = {f"no_{k}": {k: False} for k in KINDS}
BASELINES = ["energy", "counter_step", "abs_time_dev"]
SUBTYPES = ["deletion", "swap", "duplicate", "forged", "timing", "counter_jump"]


def out_dir(cfg: dict) -> Path:
    return Path(cfg["detectors"]["eval_dir"]) / "sequence"


def flags() -> dict:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(i): f for s in sp["satellites"].values() for i, f in s["obs"].items()}


# ------------------------------------------------------------------------------------------------ fit
def fit(cfg: dict) -> dict:
    qc, sc = qc_view(cfg), cfg["detectors"]["sequence"]
    params = {k: v for k, v in sc.items() if k not in ("profile", "threshold")}
    by_sat = {}
    for oid, norad in sorted(split_ids("train").items()):
        d = Path(cfg["download"]["raw_dir"]) / str(oid) / "demoddata"
        frames, _ = stream_from_dir(d, qc, norad)
        if frames:
            by_sat.setdefault(norad, {})[oid] = frames
    sats = {str(n): fit_profile(passes, n, qc, params) for n, passes in by_sat.items()}
    model = {"fitted_on": "train (clean passes, raw demoddata)", "params": params, "satellites": sats,
             "pooled": pooled(sats)}
    Path(sc["profile"]).parent.mkdir(parents=True, exist_ok=True)
    Path(sc["profile"]).write_text(json.dumps(model, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return model


# ------------------------------------------------------------------------------------------------ score
def score_obs(oid: int, norad: int, cfg: dict, model: dict, start) -> pd.DataFrame:
    qc = qc_view(cfg)
    fdir = Path(cfg["features"]["out_dir"]) / str(oid)
    fr = pd.read_csv(fdir / "frames.csv", usecols=FRAME_INPUT_COLS, dtype={"copy_delays_s": str})
    if fr.empty:
        return pd.DataFrame()
    wp = fdir / "windows.csv"
    win = pd.read_csv(wp, usecols=["variant_id", "t_start_s", "power_db"]) if wp.exists() else pd.DataFrame()
    wins = {k: g for k, g in win.groupby("variant_id")} if not win.empty else {}
    half = cfg["attacks"]["replay"]["airtime_s"][norad] + cfg["attacks"]["replay"]["margin_s"]
    sp, p = sat_params(norad, qc), model["params"]
    parts = []
    for vid, g in fr.groupby("variant_id", sort=True):
        frames, _ = stream_from_rows(g.to_dict("records"), qc, norad)
        res = detect(frames, norad, qc, model, causal=True)
        rows = pd.DataFrame(res["frames"])
        rows.insert(0, "variant_id", vid)
        rows.insert(0, "norad", norad)
        rows.insert(0, "obs_id", oid)
        rows["timing_usable"] = res["diagnostics"]["timing_usable"]
        rows["trajectory"] = rows["trajectory"].map(json.dumps)
        rows["evidence"] = rows["evidence"].map(lambda e: ";".join(e))
        for name, use in ABLATIONS.items():
            rows[f"abl_{name}"] = [r["score"] for r in detect(frames, norad, qc, model, causal=False, use=use)["frames"]]
        rel = [(f["t_utc"] - start).total_seconds() for f in frames]
        rows["base_energy"] = energy_scores(rel, wins.get(vid), half)
        prev, st = None, []
        for f in frames:
            c = f["counter"]
            st.append(abs(c - prev - sp["step"]) / sp["step"] if (c is not None and prev is not None) else 0.0)
            if c is not None:
                prev = c
        rows["base_counter_step"] = st
        line = counter_line(frames, sp["spc"], set(), p)
        rows["base_abs_time_dev"] = [abs(r - line["offset"]) if (r is not None and line["ok"]) else np.nan
                                     for r in line["res"]]
        parts.append(rows)
    return pd.concat(parts, ignore_index=True)


def score_split(cfg: dict, split: str, resume: bool) -> Path:
    if split == "test":
        require_lock("sequence", cfg["detectors"]["models_dir"])
    model = json.loads(Path(cfg["detectors"]["sequence"]["profile"]).read_text(encoding="utf-8"))
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    d = out_dir(cfg) / split
    d.mkdir(parents=True, exist_ok=True)
    ids = split_ids(split)
    for i, (oid, norad) in enumerate(sorted(ids.items())):
        pth = d / f"{oid}.csv"
        if resume and pth.exists():
            continue
        df = score_obs(oid, norad, cfg, model, _utc(obs.loc[oid, "start_utc"]))
        tmp = pth.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(pth)
        print(f"[K score {split}] {i + 1}/{len(ids)} obs {oid}: {len(df)} frames", file=sys.stderr, flush=True)
    return d


# ------------------------------------------------------------------------------------------------ labels
def load_scored(cfg: dict, split: str) -> pd.DataFrame:
    if split == "test":
        require_lock("sequence", cfg["detectors"]["models_dir"])
    d, rd = out_dir(cfg) / split, Path(cfg["detectors"]["eval_dir"]) / "replay" / split
    fl = flags()
    parts = []
    for oid in sorted(split_ids(split)):
        pth = d / f"{oid}.csv"
        if not pth.exists() or pth.stat().st_size < 5:
            continue
        s = pd.read_csv(pth)
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
        rp = rd / f"{oid}.csv"
        if rp.exists():
            r = pd.read_csv(rp, usecols=["variant_id", "filename", "score", "evidence"]).rename(
                columns={"score": "replay_score", "evidence": "replay_evidence"})
            m = m.merge(r, on=["variant_id", "filename"], how="left")
        m["timing_reliable_obs"] = bool(fl[oid]["timing_reliable"])
        parts.append(m)
    df = pd.concat(parts, ignore_index=True)
    df["kind"] = df["variant_id"].map(variant_kind)
    df["pos"] = (df["is_attack"] == 1) & (df["attack_type"] == "sequence")
    df["neg"] = df["is_attack"] == 0
    df["neg_cat"] = np.where(df["kind"].isin(["clean", "benign"]), df["kind"], "other")
    return df


def score_columns(df: pd.DataFrame) -> dict[str, str]:
    cols = {"main": "score"} | {f"abl_{k}": f"abl_{k}" for k in ABLATIONS} | {f"base_{k}": f"base_{k}" for k in BASELINES}
    return {k: v for k, v in cols.items() if v in df.columns}


# ------------------------------------------------------------------------------------------------ calibrate
def calibrate(cfg: dict) -> dict:
    """Same rule as Phase J (decision 72): tau = max(calib tau, train-guard tau), pooled and per satellite."""
    df, tr = load_scored(cfg, "calib"), load_scored(cfg, "train")
    alpha, strict = cfg["detectors"]["target_fpr"], cfg["detectors"]["strict_fpr"]
    neg, tneg = df[df.neg], tr[tr.neg]
    taus, parts, fprs = {}, {}, {}
    for name, col in score_columns(df).items():
        extra = np.concatenate([df.loc[df.pos, col].to_numpy(), tr.loc[tr.pos, col].to_numpy()])
        t_cal = M.threshold_for_fpr(neg[col].to_numpy(), alpha, neg.norad.to_numpy(), extra_candidates=extra)
        t_tr = M.threshold_for_fpr(tneg[col].to_numpy(), alpha, tneg.norad.to_numpy(), extra_candidates=extra)
        taus[name], parts[name] = max(t_cal, t_tr), {"calib": t_cal, "train_guard": t_tr}
        fprs[name] = {"calib": _fpr(neg, col, taus[name]), "train": _fpr(tneg, col, taus[name])}
    extra = np.concatenate([df.loc[df.pos, "score"].to_numpy(), tr.loc[tr.pos, "score"].to_numpy()])
    tau_strict = max(M.threshold_for_fpr(neg.score.to_numpy(), strict, neg.norad.to_numpy(), extra_candidates=extra),
                     M.threshold_for_fpr(tneg.score.to_numpy(), strict, tneg.norad.to_numpy(), extra_candidates=extra))
    out = {"tau": taus["main"], "taus": taus, "tau_parts": parts, "target_fpr": alpha, "fpr_at_tau": fprs,
           "tau_strict": tau_strict, "strict_fpr": strict, "rule": "alert if score >= tau",
           "calibrated_on": "calib (train guard)",
           "n_not_attacked_frames": {"calib": int(len(neg)), "train": int(len(tneg))},
           "n_sequence_frames": {"calib": int(df.pos.sum()), "train": int(tr.pos.sum())}}
    Path(cfg["detectors"]["sequence"]["threshold"]).write_text(json.dumps(out, indent=1, sort_keys=True, default=float) + "\n",
                                                               encoding="utf-8")
    return out


# ------------------------------------------------------------------------------------------------ report
def evaluate(df: pd.DataFrame, col: str, tau: float, bs: dict, seed: int, full: bool) -> dict:
    e = df[df.pos | df.neg].copy()
    e["alert"] = e[col].fillna(-np.inf) >= tau
    out = {"tau": tau, "pooled": M.binary(e.pos, e.alert),
           "fpr_by_category": {c: {"fpr": float(g.alert.mean()), "n": int(len(g))} for c, g in e[e.neg].groupby("neg_cat")}}
    po = M.per_obs(e, "pos", "alert")
    out["per_obs"] = {k: M.bootstrap_ci(po[k].to_numpy(), bs["n"], bs["ci"], seed) for k in ("precision", "recall", "f1", "fpr")}
    out["auc"] = M.auc(e.pos, e[col])
    rec = {}
    for st in SUBTYPES:
        g = e[e.pos & (e.subtype == st)]
        if st == "timing":
            g = g[g.timing_reliable_obs]
        for norad, h in [("all", g)] + list(g.groupby("norad")):
            if len(h):
                rec.setdefault(st, {})[str(norad)] = {"n": int(len(h)), "recall": float(h.alert.mean())}
    out["recall_by_subtype"] = rec
    out["by_norad"] = {str(k): M.binary(g.pos, g.alert) for k, g in e.groupby("norad")}
    if full:
        ev = []
        for (oid, vid), g in e[e.pos].groupby(["obs_id", "variant_id"]):
            st = g.subtype.iloc[0]
            if st == "timing" and not g.timing_reliable_obs.iloc[0]:
                continue
            g = g.sort_values("t_rel_s")
            ta = min((first_alert(tr, tau) for tr in g.trajectory), default=math.nan, key=lambda x: (math.isnan(x), x))
            ev.append({"obs_id": oid, "subtype": st, "detected": bool(g.alert.any()),
                       "delay_s": ta - float(g.t_rel_s.iloc[0]) if not math.isnan(ta) else math.nan})
        ev = pd.DataFrame(ev)
        out["events"] = {"n": int(len(ev)), "detected": int(ev.detected.sum()),
                         "per_obs_detection_rate": M.bootstrap_ci(ev.groupby("obs_id").detected.mean().to_numpy(), bs["n"], bs["ci"], seed),
                         "delay_s_median": float(ev.delay_s.median()), "delay_s_max": float(ev.delay_s.max()),
                         "by_subtype": {k: {"n": int(len(g)), "detected": int(g.detected.sum())} for k, g in ev.groupby("subtype")}}
        out["online_fpr"] = float(e[e.neg].trajectory.map(lambda tr: not math.isnan(first_alert(tr, tau))).mean())
        other = df[(df.is_attack == 1) & (df.attack_type != "sequence")]
        out["fires_on_other_attacks"] = {f"{a}/{s}": {"n": int(len(g)), "alert_rate": float((g[col] >= tau).mean())}
                                         for (a, s), g in other.groupby(["attack_type", "subtype"])}
    return out


def naming(df: pd.DataFrame, tau_seq: float, tau_rep: float) -> dict:
    """Confusion of the fusion name (detect/naming.py) against the truth, on frames either detector alerts."""
    if "replay_score" not in df.columns:
        return {}
    d = df.copy()
    ra, sa = d.replay_score.fillna(-1) >= tau_rep, d.score >= tau_seq
    split = lambda v: [] if not isinstance(v, str) else v.split(";")
    spc = {39444: 5.0, 25397: 1.0}
    nn = lambda v: None if pd.isna(v) else float(v)
    d["name"] = [name_frame(a, split(re), b, split(se), nn(ph), nn(dv), spc.get(int(no)))
                 for a, re, b, se, ph, dv, no in zip(ra, d.replay_evidence, sa, d.evidence, d.phase_dev_s, d.dev_s, d.norad)]
    d["truth"] = np.where(d.is_attack == 0, "not_attacked",
                          np.where(d.attack_type == "replay", np.where(d.subtype == "cross_pass", "replay_cross_pass", "replay"),
                                   d.subtype.astype(str)))
    a = d[d.name.notna()]
    tab = a.groupby(["truth", "name"]).size().unstack(fill_value=0)
    both = d[ra & sa]
    return {"confusion": {t: {n: int(v) for n, v in row.items() if v} for t, row in tab.iterrows()},
            "name_accuracy_on_attacked_alerts": float((a[a.truth != "not_attacked"].name == a[a.truth != "not_attacked"].truth).mean()),
            "flagged_by_both": {"n": int(len(both)), "names": both.name.value_counts().to_dict(),
                                "truth": both.truth.value_counts().to_dict()}}


def report(cfg: dict, split: str) -> dict:
    df = load_scored(cfg, split)
    th = json.loads(Path(cfg["detectors"]["sequence"]["threshold"]).read_text(encoding="utf-8"))
    tau_rep = json.loads(Path(cfg["detectors"]["replay"]["threshold"]).read_text(encoding="utf-8"))["tau"]
    bs, seed = cfg["detectors"]["bootstrap"], cfg["seed"]
    res = {"split": split, "n_obs": int(df.obs_id.nunique()), "n_frames": int(len(df)),
           "n_sequence_frames": int(df.pos.sum()), "n_not_attacked_frames": int(df.neg.sum()),
           "main": evaluate(df, "score", th["tau"], bs, seed, True),
           "main_strict": evaluate(df, "score", th["tau_strict"], bs, seed, False),
           "baselines": {k: evaluate(df, f"base_{k}", th["taus"][f"base_{k}"], bs, seed, False) for k in BASELINES},
           "ablations": {k: evaluate(df, f"abl_{k}", th["taus"][f"abl_{k}"], bs, seed, False) for k in ABLATIONS},
           "naming": naming(df, th["tau"], tau_rep), "tau_replay": tau_rep, "strict_fpr": th["strict_fpr"]}
    d = out_dir(cfg)
    (d / f"report_{split}.json").write_text(json.dumps(res, indent=1, default=float) + "\n", encoding="utf-8")
    (d / f"report_{split}.md").write_text(markdown(res), encoding="utf-8")
    return res


def markdown(r: dict) -> str:
    m = r["main"]
    L = [f"# Sequence detector - {r['split']}", "",
         f"{r['n_obs']} observations, {r['n_frames']} frames ({r['n_sequence_frames']} sequence-attacked, "
         f"{r['n_not_attacked_frames']} not attacked). tau = {_tau(m['tau'])} (score = -log10 p >= tau).", "",
         "| metric (per observation, 95% CI) | value |", "|---|---|"]
    L += [f"| {k} | {_ci(v)} |" for k, v in m["per_obs"].items()]
    p = m["pooled"]
    L += [f"| ROC AUC (pooled) | {m['auc']:.3f} |", "",
          f"Pooled: tp {p['tp']}, fp {p['fp']}, fn {p['fn']}, tn {p['tn']}, precision {p['precision']:.3f}, "
          f"recall {p['recall']:.3f}, F1 {p['f1']:.3f}, FPR {p['fpr']:.4f}. FPR by category: "
          + ", ".join(f"{k} {v['fpr']:.4f} (n={v['n']})" for k, v in m["fpr_by_category"].items())
          + f"; online FPR {m['online_fpr']:.4f}.", "",
          "## Recall per subtype (timing: timing-reliable observations only)", "",
          "| subtype | all | FUNCUBE-1 (39444) | TECHSAT 1B (25397) |", "|---|---|---|---|"]
    for st in SUBTYPES:
        v = m["recall_by_subtype"].get(st, {})
        cell = lambda k: f"{v[k]['recall']:.3f} (n={v[k]['n']})" if k in v else "-"
        L.append(f"| {st} | {cell('all')} | {cell('39444')} | {cell('25397')} |")
    ev = m["events"]
    L += ["", f"Events: {ev['detected']} / {ev['n']} detected, per-obs rate {_ci(ev['per_obs_detection_rate'])}, "
              f"median delay {ev['delay_s_median']:.0f} s (max {ev['delay_s_max']:.0f} s); by subtype "
          + ", ".join(f"{k} {v['detected']}/{v['n']}" for k, v in ev["by_subtype"].items()) + ".",
          "Fires on other attacks: " + (", ".join(f"{k} {v['alert_rate']:.2f} (n={v['n']})"
                                                  for k, v in m["fires_on_other_attacks"].items()) or "none"), "",
          "## Baselines and ablations (thresholds by the same rule)", "",
          "| detector | tau | recall | FPR | F1 | per-obs F1 | AUC |", "|---|---|---|---|---|---|---|"]
    for grp in ("baselines", "ablations"):
        for k, v in r[grp].items():
            q = v["pooled"]
            L.append(f"| {grp[:-1]} {k} | {_tau(v['tau'])} | {q['recall']:.3f} | {q['fpr']:.4f} | {q['f1']:.3f} | "
                     f"{_ci(v['per_obs']['f1'])} | {v['auc']:.3f} |")
    st = r["main_strict"]
    L += ["", f"Strict point (alternative only, target {r['strict_fpr']:g}): tau {_tau(st['tau'])}, recall "
              f"{st['pooled']['recall']:.3f}, FPR {st['pooled']['fpr']:.4f}.", ""]
    nm = r["naming"]
    if nm:
        L += [f"## Naming (detect/naming.py; replay tau {r['tau_replay']:g})", "",
              f"Name accuracy on alerted attacked frames: {nm['name_accuracy_on_attacked_alerts']:.3f}. Frames flagged "
              f"by both detectors: {nm['flagged_by_both']['n']}, named {nm['flagged_by_both']['names']}, truth "
              f"{nm['flagged_by_both']['truth']}.", "", "| truth | names given |", "|---|---|"]
        L += [f"| {t} | {', '.join(f'{n} {c}' for n, c in row.items())} |" for t, row in nm["confusion"].items()]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase K: sequence detector fit / score / calibrate / report")
    ap.add_argument("stage", choices=["fit", "score", "calibrate", "report"])
    ap.add_argument("--split", choices=["train", "calib", "test"], default="calib")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.stage == "fit":
        m = fit(cfg)
        print(json.dumps({k: {x: v[x] for x in ("n_frames", "n_inversions", "n_dup_payload", "n_same_counter", "dev_bins", "gaps")}
                          for k, v in m["satellites"].items()}, indent=1))
    elif a.stage == "score":
        print(json.dumps({"cache": score_split(cfg, a.split, a.resume).as_posix()}))
    elif a.stage == "calibrate":
        r = calibrate(cfg)
        print(json.dumps({k: r[k] for k in ("taus", "tau_parts", "tau_strict")}, indent=1, default=float))
    else:
        print(markdown(report(cfg, a.split)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
