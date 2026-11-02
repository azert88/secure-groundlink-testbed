"""Phase N development evaluation on TRAIN + CALIB (called by eval/fusion_eval.py dev). Labels are read here only.

Per recording (observation x variant) of train + calib, the frozen detectors' cached outputs are fused with
detect/fusion.py:
  replay, sequence      per-frame score caches of Phases J and K (eval/out/{replay,sequence}/<split>/)
  interference v2       station-out-of-fold window scores (eval/out/interference_v2/oof_scores.npy, decision 92),
                        calibrated and thresholded with the frozen v2 bundle, k-of-n smoothing, events as in
                        detect/interference_det.py
  line screen           eval/out/fusion/lines/<split>/<obs>.json (eval/fusion_eval.py lines)
Steps: (1) choose the line screen's frequency cap: highest dev recall among caps whose line false alarms on
recordings without interference are <= target_fpr (pooled) with no station above 2 x target; (2) fit the
confidence maps (isotonic, alert correct vs not); (3) report false alarms per station, recall by duration
(segment vs full pass) and per evidence, and write models/fusion/fusion.json.
"""

from __future__ import annotations

import json
import math
import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from detect import fusion as F
from detect.hash_store import qc_view
from detect.interference_det import k_of_n
from detect.sequence_det import sat_params
from eval.fusion_eval import OUT, downlink_train, split_obs
from eval.interference_eval import load_split

CAPS = {"none": {"cap": "none"}, "f_edge": {"cap": "f_edge", "f_edge_margin_hz": 100.0},
        "band_guard": {"cap": "band_guard", "band_guard_hz": 500.0}}
BASE_CFG = {"quarantine_min_score": {}, "confidence": {}, "sequence_disabled_evidence": ["content"],
            "line_screen": {"cap": "none", "f_edge_margin_hz": 100.0, "band_guard_hz": 500.0, "line_match_hz": 100.0,
                            "precision_dev": 0.5}}


def stations() -> dict[int, int]:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(o): int(v["station_id"]) for s in sp["satellites"].values() for o, v in s["obs"].items()}


def _frames(path: Path, score_col: str = "score", drop: tuple = ()) -> dict[str, list[dict]]:
    """Cached per-frame results; score_col / drop select an ablation (sequence without content, decision 100)."""
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    df["score"] = df[score_col]
    out = {}
    for vid, g in df.groupby("variant_id", sort=False):
        rows = []
        for r in g.to_dict("records"):
            ev = r.get("evidence")
            evl = [] if not isinstance(ev, str) else [e for e in ev.split(";") if e not in drop]
            rs = r["reason"] if isinstance(r["reason"], str) else ""
            if drop and "content" in drop:
                rs = "; ".join(x for x in rs.split("; ") if "constant in clean frames" not in x)
            rows.append({"filename": r["filename"], "t_utc": r["t_utc"], "t_rel_s": r["t_rel_s"], "score": float(r["score"]),
                         "evidence": evl, "reason": rs,
                         "trajectory": json.loads(r["trajectory"]) if isinstance(r["trajectory"], str) else [],
                         "phase_dev_s": None if pd.isna(r.get("phase_dev_s", np.nan)) else float(r["phase_dev_s"]),
                         "dev_s": None if pd.isna(r.get("dev_s", np.nan)) else float(r["dev_s"])})
        out[vid] = rows
    return out


def interference_result(g: pd.DataFrame, s: np.ndarray, p: np.ndarray, th: dict) -> dict:
    """Events of one recording from window scores, same construction as detect/interference_det.Detector.run."""
    t = g["t_start_s"].to_numpy(float)
    alert = k_of_n(s >= th["tau_smoothed"], th["k"], th["n"])
    hop = float(np.median(np.diff(t))) if len(t) > 1 else 0.5
    pf = g["peak_freq_hz"].to_numpy(float)
    ev, i = [], 0
    while i < len(t):
        if not alert[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(t) and alert[j + 1]:
            j += 1
        top = i + int(np.argmax(s[i:j + 1]))
        ev.append({"t_start_s": float(t[i]), "t_end_s": float(t[j] + 2 * hop), "n_windows": int(j - i + 1),
                   "max_probability": round(float(p[i:j + 1].max()), 4), "peak_freq_hz": float(pf[top]),
                   "reason": f"narrowband interference (window model): strongest line at {pf[top]:.0f} Hz"})
        i = j + 1
    return {"events": ev, "windows": {"t_start_s": t.tolist()}}


def truth(oid: int, cfg: dict) -> dict:
    """Recording-level ground truth per variant: attacked frames, interference ops (from labels and recipes)."""
    d = Path(cfg["features"]["out_dir"]) / str(oid)
    lab = pd.read_csv(d / "labels.csv")
    fr = pd.read_csv(d / "frames.csv", usecols=["variant_id", "idx", "filename"])
    fl = lab[lab["unit"] == "frame"].merge(fr, left_on=["variant_id", "index"], right_on=["variant_id", "idx"])
    out = {}
    for p in sorted((Path(cfg["attacks"]["recipes_dir"]) / str(oid)).glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        vid = rec["variant_id"]
        g = fl[fl["variant_id"] == vid]
        att = g[g["is_attack"] == 1]
        out[vid] = {"group": rec["group"], "attacked_frames": dict(zip(att["filename"], att["attack_type"] + ":" +
                                                                         att["subtype"].fillna(""))),
                    "replay": bool((att["attack_type"] == "replay").any()),
                    "sequence": bool((att["attack_type"] == "sequence").any()),
                    "sequence_subtype": ";".join(sorted(set(att.loc[att["attack_type"] == "sequence", "subtype"]))),
                    "interference": [o for o in rec["operations"] if o["type"] == "interference"],
                    "benign": [o["type"] for o in rec["operations"] if o["type"] in ("gain", "noise_rise", "fading",
                                                                                     "edge_attenuation")]}
    return out


def interf_correct(a: dict, ops: list[dict], duration: float) -> bool:
    """An interference alert is correct if it overlaps an injected interferer in time (window model) or in
    frequency (line screen; f0 +- bw / 2 + 100 Hz, drift included)."""
    for o in ops:
        segs = o["segments"]
        f0 = o.get("f0_hz") or 0.0
        half = (o.get("bw_hz") or 0.0) / 2 + 100.0 + abs(o.get("drift_hz_s") or 0.0) * duration
        if "line_screen" in a["evidence"] and "line_hz" in a:
            if a["line_hz"][0] - half <= f0 <= a["line_hz"][1] + half:
                return True
        if "window_model" in a["evidence"]:
            if any(min(a["t_end_s"], b) - max(a["t_start_s"], s0) > 0 for s0, b in segs):
                return True
    return False


def is_full(op: dict, duration: float) -> bool:
    return sum(b - a for a, b in op["segments"]) >= 0.95 * duration


def collect(cfg: dict) -> list[dict]:
    """Per recording: detector outputs ready for fusion + truth (train + calib)."""
    qc = qc_view(cfg)
    tau_r = json.loads(Path(cfg["detectors"]["replay"]["threshold"]).read_text(encoding="utf-8"))["tau"]
    tau_s = json.loads(Path(cfg["detectors"]["sequence"]["threshold"]).read_text(encoding="utf-8"))["tau"]
    with open("models/interference_v2/model.pkl", "rb") as fh:
        b = pickle.load(fh)
    th, cal = b["threshold"], b["calibration"]
    win = pd.concat([load_split("train"), load_split("calib")], ignore_index=True)
    oof = np.load("eval/out/interference_v2/oof_scores.npy")[1]
    assert len(oof) == len(win), "OOF scores do not match the window caches"
    prob = cal["iso"].predict(oof) if cal["method"] == "isotonic" else 1 / (1 + np.exp(-(cal["a"] * oof + cal["b"])))
    win_idx = {k: v for k, v in win.groupby(["obs_id", "variant_id"], sort=False).indices.items()}
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    st = stations()
    recs = []
    for split in ("train", "calib"):
        for oid in split_obs(split):
            norad = int(obs.loc[oid, "norad"])
            rep = _frames(Path(cfg["detectors"]["eval_dir"]) / "replay" / split / f"{oid}.csv")
            off = tuple(BASE_CFG["sequence_disabled_evidence"])
            seq = _frames(Path(cfg["detectors"]["eval_dir"]) / "sequence" / split / f"{oid}.csv",
                          "abl_no_content" if off == ("content",) else "score", off)
            lp = OUT / "lines" / split / f"{oid}.json"
            lines = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else None
            tr = truth(oid, cfg)
            spc = sat_params(norad, qc)["spc"]
            dur = float(obs.loc[oid, "duration_s"]) if pd.notna(obs.loc[oid, "duration_s"]) else math.nan
            for vid, t in tr.items():
                ix = win_idx.get((oid, vid))
                inter = None
                if ix is not None:
                    g = win.iloc[ix]
                    inter = interference_result(g, oof[ix], prob[ix], th)
                recs.append({"obs_id": oid, "variant_id": vid, "split": split, "norad": norad, "station": st[oid],
                             "spc": spc, "duration_s": dur, "replay": {"frames": rep[vid]} if vid in rep else None,
                             "sequence": {"frames": seq[vid]} if vid in seq else None, "interference": inter,
                             "lines": (lines or {}).get("variants", {}).get(vid),
                             "f_edge_hz": float(obs.loc[oid, "f_edge_hz"]) if pd.notna(obs.loc[oid, "f_edge_hz"]) else None,
                             "band_hi_hz": float(obs.loc[oid, "feature_band_hi_hz"]) if pd.notna(obs.loc[oid, "feature_band_hi_hz"]) else None,
                             "truth": t, "tau_r": tau_r, "tau_s": tau_s})
    return recs


def run_fusion(r: dict, fcfg: dict) -> dict:
    return F.fuse(r["replay"], r["sequence"], r["interference"], r["lines"], fcfg, r["tau_r"], r["tau_s"], r["spc"],
                  r["f_edge_hz"], r["band_hi_hz"])


def label_alerts(r: dict, fu: dict) -> list[dict]:
    """Each alert with its correctness (for the confidence maps and false-alert counts)."""
    t = r["truth"]
    out = []
    for a in fu["alerts"]:
        if a["alert"] == "interference":
            ok = interf_correct(a, t["interference"], r["duration_s"])
        else:
            ok = any(f in t["attacked_frames"] for f in a["frames"])
        out.append({**{k: a[k] for k in ("alert", "name", "evidence_score", "confidence", "decision")},
                    "evidence": a.get("evidence", []), "correct": bool(ok)})
    return out


def fit_map(x: list[float], y: list[bool]) -> dict | None:
    if len(x) < 5 or len(set(y)) < 1:
        return None
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(np.asarray(x, float), np.asarray(y, float))
    xs, ys = iso.X_thresholds_.tolist(), iso.y_thresholds_.tolist()
    # Laplace-shrink the ends so a confidence is never exactly 0 or 1 on few samples
    n = len(x)
    ys = [(v * n + 0.5) / (n + 1) for v in ys]
    return {"x": [round(v, 6) for v in xs], "y": [round(v, 6) for v in ys], "n": n, "n_correct": int(sum(y))}


def _rate(a) -> float:
    a = list(a)
    return float(np.mean(a)) if a else float("nan")


def evaluate(recs: list[dict], fcfg: dict, alpha: float) -> dict:
    rows, alerts = [], []
    for r in recs:
        fu = run_fusion(r, fcfg)
        la = label_alerts(r, fu)
        t = r["truth"]
        attacked = t["replay"] or t["sequence"] or bool(t["interference"])
        names = {a["name"] if a["alert"] != "interference" else "interference" for a in fu["alerts"]}
        row = {"obs_id": r["obs_id"], "variant_id": r["variant_id"], "split": r["split"], "station": r["station"],
               "norad": r["norad"], "group": t["group"], "attacked": attacked, "verdict": fu["verdict"],
               "confidence": fu["confidence_attacked"], "any_alert": bool(fu["alerts"]),
               "has_replay": t["replay"], "has_sequence": t["sequence"], "has_interf": bool(t["interference"]),
               "replay_alert": any(a["alert"] == "replay" and a["correct"] for a in la),
               "sequence_alert": any(a["alert"] == "sequence" and a["correct"] for a in la),
               "interf_alert": any(a["alert"] == "interference" and a["correct"] for a in la),
               "interf_model": any(a["alert"] == "interference" and a["correct"] and "window_model" in a["evidence"] for a in la),
               "interf_line": any(a["alert"] == "interference" and a["correct"] and "line_screen" in a["evidence"] for a in la),
               "false_interf": any(a["alert"] == "interference" and not a["correct"] for a in la),
               "false_line": any(a["alert"] == "interference" and not a["correct"] and "line_screen" in a["evidence"] for a in la),
               "false_frame": any(a["alert"] != "interference" and not a["correct"] for a in la),
               "quarantine": fu["verdict"] == "quarantine",
               "replay_alert_q": any(a["alert"] == "replay" and a["correct"] and a["decision"] == "quarantine" for a in la),
               "sequence_alert_q": any(a["alert"] == "sequence" and a["correct"] and a["decision"] == "quarantine" for a in la),
               "interf_alert_q": any(a["alert"] == "interference" and a["correct"] and a["decision"] == "quarantine" for a in la),
               "has_frames": r["replay"] is not None,
               "has_audio": r["interference"] is not None}
        if t["interference"]:
            o = t["interference"][0]
            row.update(interf_full=is_full(o, r["duration_s"]), snr_db=o["snr_db"], shape=o["shape"])
        rows.append(row)
        alerts += [{**a, "obs_id": r["obs_id"], "variant_id": r["variant_id"], "station": r["station"]} for a in la]
    return {"rows": pd.DataFrame(rows), "alerts": pd.DataFrame(alerts)}


def summarize(df: pd.DataFrame, alpha: float) -> dict:
    neg = df[~df["attacked"]]
    ni = df[~df["has_interf"] & df["has_audio"]]
    it = df[df["has_interf"]]
    s = {"n_recordings": int(len(df)), "n_not_attacked": int(len(neg)),
         "recording_false_alarm_rate": _rate(neg["any_alert"]),
         "recording_false_alarm_rate_clean": _rate(neg.loc[neg["group"] == "clean", "any_alert"]),
         "recording_false_alarm_rate_benign": _rate(neg.loc[neg["group"] == "benign", "any_alert"]),
         "false_alarm_by_station": {str(k): {"n": int(len(g)), "rate": round(_rate(g["any_alert"]), 4)}
                                    for k, g in neg.groupby("station")},
         "false_interference_alert_rate_no_interf": _rate(ni["false_interf"]),
         "false_line_alert_rate_no_interf": _rate(ni["false_line"]),
         "false_line_by_station": {str(k): round(_rate(g["false_line"]), 4) for k, g in ni.groupby("station")},
         "recording_quarantine_false_alarm_rate": _rate(neg["quarantine"]),
         "quarantine_false_alarm_by_station": {str(k): round(_rate(g["quarantine"]), 4) for k, g in neg.groupby("station")
                                               if g["quarantine"].any()},
         "verdicts_not_attacked": neg["verdict"].value_counts().to_dict(),
         "verdicts_attacked": df[df["attacked"]]["verdict"].value_counts().to_dict()}
    for name, flag in (("replay", "has_replay"), ("sequence", "has_sequence")):
        g = df[df[flag]]
        s[f"{name}_recall"] = _rate(g[f"{name}_alert"])
        s[f"{name}_recall_quarantine"] = _rate(g[f"{name}_alert_q"])
    s["interference_recall"] = {}
    for dur, g in [("all", it), ("segment", it[it["interf_full"] == False]), ("full_pass", it[it["interf_full"] == True])]:  # noqa: E712
        s["interference_recall"][dur] = {"n": int(len(g)), "either": _rate(g["interf_alert"]),
                                         "either_quarantine": _rate(g["interf_alert_q"]),
                                         "window_model": _rate(g["interf_model"]), "line_screen": _rate(g["interf_line"])}
    bins = it.assign(b=(it["snr_db"] // 10 * 10).astype(int))
    s["interference_recall_by_snr10"] = {f"{k}": {"n": int(len(g)), "either": round(_rate(g["interf_alert"]), 3),
                                                  "line": round(_rate(g["interf_line"]), 3),
                                                  "model": round(_rate(g["interf_model"]), 3)}
                                         for k, g in bins.groupby("b")}
    att = df[df["attacked"]]
    s["recording_detection_rate"] = _rate(att["any_alert"])
    return s


def ece_rec(df: pd.DataFrame, bins: int = 10) -> float:
    p, y = df["confidence"].to_numpy(float), df["attacked"].to_numpy(float)
    e, idx = 0.0, np.minimum((p * bins).astype(int), bins - 1)
    for b in range(bins):
        m = idx == b
        if m.any():
            e += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(e)


def run_dev(cfg: dict) -> dict:
    alpha = cfg["detectors"]["target_fpr"]
    recs = collect(cfg)
    # (1) frequency cap of the line screen (no confidence maps needed: alerts only)
    caps = {}
    for name, c in CAPS.items():
        fc = json.loads(json.dumps(BASE_CFG))
        fc["line_screen"].update(c)
        ev = evaluate(recs, fc, alpha)
        df = ev["rows"]
        ni = df[~df["has_interf"] & df["has_audio"]]
        it = df[df["has_interf"]]
        per_st = ni.groupby("station")["false_line"].mean()
        caps[name] = {"false_line_rate": _rate(ni["false_line"]), "worst_station": float(per_st.max()),
                      "line_recall": _rate(it["interf_line"]), "either_recall": _rate(it["interf_alert"]),
                      "line_recall_full_pass": _rate(it.loc[it["interf_full"] == True, "interf_line"])}  # noqa: E712
    ok = [k for k, v in caps.items() if v["false_line_rate"] <= alpha and v["worst_station"] <= 2 * alpha]
    cap = max(ok, key=lambda k: (round(caps[k]["line_recall"], 4), k == "f_edge")) if ok else "f_edge"
    fc = json.loads(json.dumps(BASE_CFG))
    fc["line_screen"].update(CAPS[cap])
    # (2) recording-level quarantine points: <= alpha of eligible recordings carry a false alert of the family
    ev = evaluate(recs, fc, alpha)
    al, df0 = ev["alerts"], ev["rows"]
    key = al["obs_id"].astype(str) + "/" + al["variant_id"]
    qms, qinfo = {}, {}
    for fam, sel, n_elig in (("sequence", al["alert"] == "sequence", int(df0["has_frames"].sum())),
                             ("interference_model", (al["alert"] == "interference")
                              & al["evidence"].map(lambda e: "window_model" in e), int(df0["has_audio"].sum()))):
        f = al[sel & ~al["correct"]].groupby(key[sel & ~al["correct"]])["evidence_score"].max().sort_values(ascending=False)
        m = int(np.floor(alpha * n_elig))
        qms[fam] = round(float(f.iloc[m]) + 1e-6, 6) if len(f) > m else 0.0
        qinfo[fam] = {"eligible_recordings": n_elig, "recordings_with_false_alert": int(len(f)), "allowed": m,
                      "min_score": qms[fam]}
    fc["quarantine_min_score"] = qms
    ni = df0[~df0["has_interf"] & df0["has_audio"]]
    fc["line_screen"]["quarantine"] = bool(_rate(ni["false_line"]) <= alpha)
    # (3) confidence maps from the alerts' correctness
    ev = evaluate(recs, fc, alpha)
    al = ev["alerts"]
    maps = {}
    for key, sel in (("replay", al["alert"] == "replay"), ("sequence", al["alert"] == "sequence"),
                     ("interference_model", (al["alert"] == "interference") & al["evidence"].map(lambda e: e == ["window_model"]))):
        g = al[sel]
        maps[key] = fit_map(g["evidence_score"].tolist(), g["correct"].tolist())
    ln = al[(al["alert"] == "interference") & al["evidence"].map(lambda e: e == ["line_screen"])]
    fc["confidence"] = maps
    fc["line_screen"]["precision_dev"] = round((int(ln["correct"].sum()) + 1) / (len(ln) + 2), 4)
    fc["line_screen"]["precision_dev_counts"] = {"correct": int(ln["correct"].sum()), "n": int(len(ln))}
    # (4) final dev report with the maps
    ev = evaluate(recs, fc, alpha)
    df = ev["rows"]
    summ = summarize(df, alpha)
    summ["ece_recording"] = ece_rec(df)
    fc.update({"version": 1, "fitted_on": "train + calib recordings (interference v2: station-out-of-fold scores)",
               "line_screen_params": {k: cfg["qc"][k] for k in ("line_threshold_db", "line_min_frac_windows",
                                                                 "line_max_width_hz", "signal_band_margin_hz")},
               "downlink": downlink_train(), "cap_selection": {"rule": "highest dev line recall among caps with "
                                                               "false-line rate <= target and worst station <= 2 x target",
                                                               "candidates": caps, "chosen": cap},
               "interference_model": "models/interference_v2/model.pkl",
               "quarantine_selection": qinfo,
               "decision_rule": "quarantine: replay, line screen (if its dev false-line rate <= target), sequence / "
                                "interference-model alerts with evidence >= quarantine_min_score; flag: other alerts; "
                                "accept: no alert"})
    fc["line_screen"]["downlink"] = fc.pop("downlink")
    Path("models/fusion").mkdir(parents=True, exist_ok=True)
    Path("models/fusion/fusion.json").write_text(json.dumps(fc, indent=1) + "\n", encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "dev_recordings.csv", index=False)
    rep = {"summary": {"cap": cap, "quarantine_min_score": qms, **summ}, "quarantine_selection": qinfo, "caps": caps, "confidence_maps": {k: (None if v is None else
                                                                              {"n": v["n"], "n_correct": v["n_correct"]})
                                                                          for k, v in maps.items()},
           "line_precision_dev": fc["line_screen"]["precision_dev_counts"]}
    (OUT / "report_dev.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "report_dev.md").write_text(markdown_dev(rep), encoding="utf-8")
    return rep


def _p(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.3f}"


def markdown_dev(rep: dict) -> str:
    s = rep["summary"]
    L = ["# Phase N fusion: development evaluation (train + calib)", "",
         f"Line-screen frequency cap chosen: **{s['cap']}**", "",
         "| cap | false-line rate (no interference) | worst station | line recall | either recall | line recall full pass |",
         "|---|---|---|---|---|---|"]
    for k, v in rep["caps"].items():
        L.append(f"| {k} | {_p(v['false_line_rate'])} | {_p(v['worst_station'])} | {_p(v['line_recall'])} | "
                 f"{_p(v['either_recall'])} | {_p(v['line_recall_full_pass'])} |")
    L += ["", f"Recording-level quarantine points (<= 1 % of dev recordings with a false alert): {s['quarantine_min_score']}", "",
          f"Recordings {s['n_recordings']}, not attacked {s['n_not_attacked']}: not accepted (flag or quarantine) "
          f"{_p(s['recording_false_alarm_rate'])} (clean {_p(s['recording_false_alarm_rate_clean'])}, benign "
          f"{_p(s['recording_false_alarm_rate_benign'])}); **quarantined {_p(s['recording_quarantine_false_alarm_rate'])}** "
          f"(stations: {s['quarantine_false_alarm_by_station']}); recording ECE {_p(s['ece_recording'])}", "",
          "| station | not-attacked recordings | false-alarm rate |", "|---|---|---|"]
    for k, v in s["false_alarm_by_station"].items():
        L.append(f"| {k} | {v['n']} | {v['rate']:.3f} |")
    L += ["", "| interference duration | n | either (any tier) | either, quarantine tier | window model | line screen |",
          "|---|---|---|---|---|---|"]
    for k, v in s["interference_recall"].items():
        L.append(f"| {k} | {v['n']} | {_p(v['either'])} | {_p(v['either_quarantine'])} | {_p(v['window_model'])} | "
                 f"{_p(v['line_screen'])} |")
    L += ["", f"Replay recall (recording) {_p(s['replay_recall'])} (quarantine tier {_p(s['replay_recall_quarantine'])}), "
          f"sequence recall {_p(s['sequence_recall'])} (quarantine tier {_p(s['sequence_recall_quarantine'])}); "
          f"false interference alerts on recordings without interference {_p(s['false_interference_alert_rate_no_interf'])}."]
    return "\n".join(L) + "\n"
