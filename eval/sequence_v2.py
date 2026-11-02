"""Sequence detector v2 (post hoc, decision 100): leave-one-station-out check, gate, final fit, one test report.

Stages
  loso    train + calib, folds = stations: for each station, v1 and v2 profiles are fitted on the clean passes of all
          OTHER stations (raw demoddata) and score every variant of the held-out station (frozen frames.csv). Each
          version's threshold comes from its own out-of-fold not-attacked frames (1 % pooled and per satellite).
          Gate (fixed before running): v2 worst-station FPR < v1 worst-station FPR AND v2 FUNCUBE-1 recall >= v1.
          The 'v1_no_content' variant (v1 with the content evidence off) is checked under the same folds (fallback).
          If the gate passes: final v2 profile on all train + calib clean passes -> models/sequence_v2/{profile,
          threshold}.json (threshold = the OOF one). Output eval/out/sequence_v2/loso_report.{json,md}.
  report  test, requires the sequence and sequence_v2 locks: v2 scored once, side by side with v1 (official).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import sequence_det as S  # noqa: E402
from detect import sequence_v2 as V2  # noqa: E402
from detect.frames_io import stream_from_dir, stream_from_rows  # noqa: E402
from detect.hash_store import qc_view  # noqa: E402
from eval import metrics as M  # noqa: E402
from eval.replay_eval import FRAME_INPUT_COLS, split_ids, variant_kind  # noqa: E402

OUT = Path("eval/out/sequence_v2")
MD = Path("models/sequence_v2")
VERSIONS = ("v1", "v2", "v1_no_content")


def flags() -> dict:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(i): f for s in sp["satellites"].values() for i, f in s["obs"].items()}


def params(cfg: dict) -> dict:
    return {k: v for k, v in cfg["detectors"]["sequence"].items() if k not in ("profile", "threshold")}


def clean_passes(cfg: dict, ids: dict[int, int]) -> dict[int, tuple[int, list]]:
    qc = qc_view(cfg)
    out = {}
    for oid, norad in sorted(ids.items()):
        frames, _ = stream_from_dir(Path(cfg["download"]["raw_dir"]) / str(oid) / "demoddata", qc, norad)
        if frames:
            out[oid] = (norad, frames)
    return out


def variants(cfg: dict, oid: int, norad: int):
    """[(variant_id, frames, labels df)] of one observation from the frozen tables."""
    qc = qc_view(cfg)
    d = Path(cfg["features"]["out_dir"]) / str(oid)
    fr = pd.read_csv(d / "frames.csv", usecols=FRAME_INPUT_COLS + ["idx"], dtype={"copy_delays_s": str})
    if fr.empty:
        return []
    lab = pd.read_csv(d / "labels.csv")
    lab = lab[lab["unit"] == "frame"].rename(columns={"index": "idx"})[["variant_id", "idx", "is_attack", "attack_type", "subtype"]]
    fr = fr.merge(lab, on=["variant_id", "idx"], how="left")
    out = []
    for vid, g in fr.groupby("variant_id", sort=True):
        frames, _ = stream_from_rows(g.to_dict("records"), qc, norad)
        out.append((vid, frames, g.set_index("filename")))
    return out


def build(version: str, passes: dict, station: dict, qc: dict, p: dict) -> dict:
    by_sat = {}
    for oid, (norad, fr) in passes.items():
        by_sat.setdefault(norad, {})[oid] = fr
    if version == "v2":
        return V2.fit_model(by_sat, station, qc, p)
    sats = {str(n): S.fit_profile(ps, n, qc, p) for n, ps in by_sat.items()}
    return {"params": p, "satellites": sats, "pooled": S.pooled(sats)}


def score_obs(version: str, model: dict, vs: list, norad: int, qc: dict) -> list[dict]:
    rows = []
    for vid, frames, lab in vs:
        if version == "v2":
            res = V2.detect(frames, norad, qc, model, causal=False)
        else:
            res = S.detect(frames, norad, qc, model, causal=False,
                           use={"content": False} if version == "v1_no_content" else None)
        for r in res["frames"]:
            l = lab.loc[r["filename"]]
            rows.append({"variant_id": vid, "kind": variant_kind(vid), "filename": r["filename"], "score": r["score"],
                         "evidence": ";".join(r["evidence"]), "is_attack": int(l["is_attack"]),
                         "attack_type": l["attack_type"] if isinstance(l["attack_type"], str) else "",
                         "subtype": l["subtype"] if isinstance(l["subtype"], str) else ""})
    return rows


def summary(df: pd.DataFrame, alpha: float, tau: float | None = None) -> dict:
    neg, pos = df[df["is_attack"] == 0], df[(df["is_attack"] == 1) & (df["attack_type"] == "sequence")]
    if tau is None:
        tau = M.threshold_for_fpr(neg["score"].to_numpy(), alpha, groups=neg["norad"].to_numpy(),
                                  extra_candidates=pos["score"].to_numpy())
    a_neg, a_pos = neg["score"] >= tau, pos["score"] >= tau
    per_st = neg.assign(a=a_neg).groupby("station")["a"].mean()
    fc = pos["norad"] == 39444
    po = df[(df["is_attack"] == 0) | ((df["attack_type"] == "sequence"))].assign(
        y=lambda d: d["is_attack"] == 1, yh=lambda d: d["score"] >= tau)
    pob = M.per_obs(po, "y", "yh")
    return {"tau": float(tau), "fpr": float(a_neg.mean()),
            "fpr_clean": float(a_neg[neg["kind"] == "clean"].mean()) if (neg["kind"] == "clean").any() else None,
            "fpr_benign": float(a_neg[neg["kind"] == "benign"].mean()) if (neg["kind"] == "benign").any() else None,
            "worst_station_fpr": float(per_st.max()), "n_stations_fpr_above_2pct": int((per_st > 0.02).sum()),
            "per_station_fpr": {str(k): round(float(v), 4) for k, v in per_st.items()},
            "recall": float(a_pos.mean()) if len(pos) else None,
            "recall_funcube": float(a_pos[fc].mean()) if fc.any() else None,
            "recall_techsat": float(a_pos[~fc].mean()) if (~fc).any() else None,
            "per_obs_recall": float(np.nanmean(pob["recall"])) if len(pob) else None,
            "per_obs_f1": float(np.nanmean(pob["f1"])) if len(pob) else None,
            "recall_by_subtype": {f"{k[0]}/{k[1]}": {"n": int(len(g)), "recall": round(float((g["score"] >= tau).mean()), 3)}
                                  for k, g in pos.groupby(["subtype", "norad"])},
            "false_alarm_evidence": neg[a_neg]["evidence"].str.split(";").explode().value_counts().to_dict(),
            "n_neg": int(len(neg)), "n_pos": int(len(pos))}


def loso(cfg: dict) -> dict:
    qc, p, alpha = qc_view(cfg), params(cfg), cfg["detectors"]["target_fpr"]
    fl = flags()
    ids = {**split_ids("train"), **split_ids("calib")}
    station = {o: int(fl[o]["station_id"]) for o in ids}
    passes = clean_passes(cfg, ids)
    vs = {o: variants(cfg, o, n) for o, n in ids.items()}
    rows = {v: [] for v in VERSIONS}
    for k, s in enumerate(sorted(set(station.values()))):
        held = [o for o in ids if station[o] == s and vs[o]]
        if not held:
            continue
        rest = {o: x for o, x in passes.items() if station[o] != s}
        for v in VERSIONS:
            model = build(v, rest, station, qc, p)
            for o in held:
                for r in score_obs(v, model, vs[o], ids[o], qc):
                    rows[v].append({"obs_id": o, "norad": ids[o], "station": s, **r})
        print(f"[seq v2 loso] station {s} ({k + 1}), {len(held)} obs", file=sys.stderr, flush=True)
    dfs = {v: pd.DataFrame(r) for v, r in rows.items()}
    rep = {"folds": "leave-one-station-out", "n_stations": len(set(station.values())), **{v: summary(d, alpha) for v, d in dfs.items()}}
    v1, v2 = rep["v1"], rep["v2"]
    rep["gate"] = {"worst_station_lower": v2["worst_station_fpr"] < v1["worst_station_fpr"],
                   "funcube_recall_not_lower": v2["recall_funcube"] >= v1["recall_funcube"]}
    rep["gate"]["pass"] = all(rep["gate"].values())
    nc = rep["v1_no_content"]
    rep["fallback_gate_no_content"] = {"worst_station_lower": nc["worst_station_fpr"] < v1["worst_station_fpr"],
                                       "funcube_recall_not_lower": nc["recall_funcube"] >= v1["recall_funcube"]}
    OUT.mkdir(parents=True, exist_ok=True)
    for v, d in dfs.items():
        d.to_csv(OUT / f"oof_{v}.csv", index=False)
    if rep["gate"]["pass"]:
        model = build("v2", passes, station, qc, p)
        model["fitted_on"] = "train + calib clean passes (raw demoddata)"
        MD.mkdir(parents=True, exist_ok=True)
        (MD / "profile.json").write_text(json.dumps(model, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        th = {"tau": v2["tau"], "target_fpr": alpha, "version": 2,
              "rule": "tau on leave-one-station-out scores (train + calib) for <= target FPR on not-attacked frames, "
                      "pooled and per satellite"}
        (MD / "threshold.json").write_text(json.dumps(th, indent=1) + "\n", encoding="utf-8")
    (OUT / "loso_report.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "loso_report.md").write_text(markdown(rep, "Leave-one-station-out on train + calib"), encoding="utf-8")
    return rep


def report_test(cfg: dict) -> dict:
    from eval.lock import require_lock
    require_lock("sequence")
    require_lock("sequence_v2")
    qc, alpha = qc_view(cfg), cfg["detectors"]["target_fpr"]
    fl = flags()
    model = json.loads((MD / "profile.json").read_text(encoding="utf-8"))
    tau = json.loads((MD / "threshold.json").read_text(encoding="utf-8"))["tau"]
    rows = []
    for o, n in sorted(split_ids("test").items()):
        vs = variants(cfg, o, n)
        rows += [{"obs_id": o, "norad": n, "station": int(fl[o]["station_id"]), **r} for r in score_obs("v2", model, vs, n, qc)]
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "test_scores_v2.csv", index=False)
    v1 = json.loads(Path("eval/out/sequence/report_test.json").read_text(encoding="utf-8"))["main"]
    rep = {"v2": summary(df, alpha, tau),
           "v1_official": {"tau": v1["tau"], "per_obs_recall": v1["per_obs"]["recall"][0], "per_obs_f1": v1["per_obs"]["f1"][0],
                           "fpr": v1["pooled"]["fpr"] if "fpr" in v1["pooled"] else None,
                           "fpr_by_category": v1["fpr_by_category"], "recall_by_subtype": v1["recall_by_subtype"]}}
    (OUT / "report_test.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "report_test.md").write_text(markdown_test(rep), encoding="utf-8")
    return rep


def _f(v) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def markdown(rep: dict, title: str) -> str:
    L = [f"# Sequence v2: {title}", "", "| version | tau | FPR | worst station | stations > 2 % | recall | FUNCUBE-1 recall | per-obs F1 |",
         "|---|---|---|---|---|---|---|---|"]
    for v in VERSIONS:
        s = rep[v]
        L.append(f"| {v} | {s['tau']:.4f} | {_f(s['fpr'])} | {_f(s['worst_station_fpr'])} | {s['n_stations_fpr_above_2pct']} | "
                 f"{_f(s['recall'])} | {_f(s['recall_funcube'])} | {_f(s['per_obs_f1'])} |")
    L += ["", f"Gate (fixed in advance): {rep['gate']}", f"Fallback (v1 without content) under the same folds: "
          f"{rep['fallback_gate_no_content']}", "", "Per-station FPR: " + "; ".join(f"{v}: {rep[v]['per_station_fpr']}" for v in VERSIONS)]
    return "\n".join(L) + "\n"


def markdown_test(rep: dict) -> str:
    v2, v1 = rep["v2"], rep["v1_official"]
    return "\n".join([
        "# Sequence detector on TEST: v1 (official) vs v2 (post hoc, decision 100)", "",
        "| version | tau | per-obs recall | per-obs F1 | FPR clean / benign |", "|---|---|---|---|---|",
        f"| v1 (official) | {v1['tau']:.4f} | {v1['per_obs_recall']:.3f} | {v1['per_obs_f1']:.3f} | "
        f"{v1['fpr_by_category'].get('clean', {}).get('fpr', float('nan')):.4f} / {v1['fpr_by_category'].get('benign', {}).get('fpr', float('nan')):.4f} |",
        f"| v2 | {v2['tau']:.4f} | {v2['per_obs_recall']:.3f} | {v2['per_obs_f1']:.3f} | {_f(v2['fpr_clean'])} / {_f(v2['fpr_benign'])} |",
        "", f"v2 per-station FPR {v2['per_station_fpr']}; worst {v2['worst_station_fpr']:.4f}; recall FUNCUBE-1 "
        f"{_f(v2['recall_funcube'])}, TECHSAT 1B {_f(v2['recall_techsat'])}.", f"v2 recall by subtype / satellite: {v2['recall_by_subtype']}.",
        f"v1 recall by subtype: {v1['recall_by_subtype']}.", f"v2 false-alarm evidence: {v2['false_alarm_evidence']}.", ""])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["loso", "report"])
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    rep = loso(cfg) if a.stage == "loso" else report_test(cfg)
    print(json.dumps({k: v for k, v in rep.items() if k in ("gate", "fallback_gate_no_content")} or
                     {"v2": {k: rep["v2"][k] for k in ("tau", "per_obs_recall", "fpr_clean", "fpr_benign", "worst_station_fpr")}},
                     indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
