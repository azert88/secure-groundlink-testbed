"""Phase O: near-copy replay evasion against the LOCKED fusion on TEST (decision 79). Labels are read here only.

Every TEST recipe that contains a replay (10 standard combos per observation with frames, the pure replay variants and
the cross-pass evasion variant; frozen recipes, drawn before any detector) is applied twice:
  exact      as in the dataset (byte-identical copies), the paired control;
  near_copy  every replayed frame has ONE byte changed outside the counter field (attacks.replay.near_copy, seeded
             per recording and frame), so the hash of a copy no longer matches its original.
Each recording is written as a raw folder with demoddata/ and metadata.json only (replay and sequence evidence are
frame-only; the audio detectors cannot see a byte change) and run through detect/run.py with the locked fusion.
Nothing is tuned; the fusion and every detector are used exactly as locked.

Stages:  run [--workers N] [--resume]  ->  eval/out/evasion_near_copy/runs/<obs_id>.json
         report                         ->  eval/out/evasion_near_copy/report.{json,md}
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

OUT = Path("eval/out/evasion_near_copy")
MODES = ("exact", "near_copy")


def make_near_copy(res: dict, norad: int, qc: dict, obs_id: int, variant_id: str) -> int:
    """Change one byte of every replayed frame in place (res from attacks.apply.apply_recipe). Returns the count."""
    from attacks.common import recipe_seed
    from attacks.replay import near_copy
    n = 0
    for k, f in enumerate(res["frames"]):
        if f.get("attack_type") == "replay":
            f["hex"], f["near_copy_byte"] = near_copy(f["hex"], norad, qc, recipe_seed(obs_id, f"{variant_id}:near_copy:{k}"))
            n += 1
    return n


def score(out: dict, res: dict) -> dict:
    """Replay detection of one recording against its truth (frames labelled replay by the recipe)."""
    fu = out["fusion"]
    rep = {f["filename"] for f in res["frames"] if f.get("attack_type") == "replay"}
    attacked = {f["filename"] for f in res["frames"] if f.get("is_attack")}
    named = {f["filename"] for f in fu["frames"] if f["name"] in ("replay", "replay_cross_pass")}
    alerted = {f["filename"] for f in fu["frames"]}
    raw = out.get("raw", {}).get("replay") or {"frames": []}
    ev = [e for f in raw["frames"] if f["filename"] in rep and f["score"] >= out["detectors"]["replay"]["tau"]
          for e in f["evidence"]]
    return {"verdict": fu["verdict"], "replay_frames": len(rep), "replay_tp_frames": len(named & rep),
            "replay_fp_frames": len(named - rep), "replay_detected": bool(named & rep),
            "replay_frames_alerted_any": len(alerted & rep), "any_alert_on_replay": bool(alerted & rep),
            "false_frame_alerts": len(alerted - attacked), "evidence_on_replay": sorted(set(ev)),
            "alert_names": fu["alert_names"]}


def run_obs(args: tuple) -> dict:
    oid, config, scratch = args
    from attacks.apply import apply_recipe
    from build_features import load_clean
    from detect.hash_store import qc_view
    from detect.run import run
    from eval.run_standard_test import write_folder
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    qc = qc_view(cfg)
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    flags = {int(i): f for s in sp["satellites"].values() for i, f in s["obs"].items()}
    clean = load_clean(oid, cfg, obs.loc[oid], {**flags[oid], "audio": False})   # frames only
    cache = {}

    def load_source(o):
        if o not in cache:
            cache[o] = load_clean(int(o), cfg, obs.loc[int(o)], {**flags[int(o)], "audio": False})
        return cache[o]

    rows = []
    for p in sorted((Path(cfg["attacks"]["recipes_dir"]) / str(oid)).glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        rop = next((o for o in rec["operations"] if o["type"] == "replay"), None)
        if rop is None:
            continue
        rec = {**rec, "operations": [{**o, "audio": False} if o["type"] == "replay" else o for o in rec["operations"]
                                     if o["type"] in ("replay", "sequence")]}   # frame operations only
        for mode in MODES:
            res = apply_recipe(rec, clean, load_source)
            res["audio"] = None
            n_mod = make_near_copy(res, clean["norad"], qc, oid, rec["variant_id"]) if mode == "near_copy" else 0
            d = Path(tempfile.mkdtemp(prefix=f"nc_{oid}_", dir=scratch))
            try:
                write_folder(d, res, clean)
                out = run(d, cfg, full=True)
            finally:
                shutil.rmtree(d, ignore_errors=True)
            rows.append({"variant_id": rec["variant_id"], "group": rec["group"], "subtype": rop["subtype"], "mode": mode,
                         "n_modified": n_mod, "runtime_s": out["runtime_s"], **score(out, res)})
    r = {"obs_id": oid, "norad": int(obs.loc[oid, "norad"]), "station": int(flags[oid]["station_id"]),
         "timing_reliable": bool(flags[oid]["timing_reliable"]), "recordings": rows}
    (OUT / "runs").mkdir(parents=True, exist_ok=True)
    tmp = OUT / "runs" / f"{oid}.tmp"
    tmp.write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
    tmp.replace(OUT / "runs" / f"{oid}.json")
    return {"obs_id": oid, "n": len(rows)}


def stage_run(config: str, workers: int, resume: bool, scratch: str | None) -> None:
    from eval.run_standard_test import require_locks
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    require_locks(cfg["detectors"]["models_dir"])
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    ids = sorted(int(o) for s in sp["satellites"].values() for o in s["splits"]["test"] if s["obs"][str(o)]["frames"])
    todo = [o for o in ids if not (resume and (OUT / "runs" / f"{o}.json").exists())]
    scratch = scratch or tempfile.gettempdir()
    t0 = time.perf_counter()
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=1) as pool:
        for i, r in enumerate(pool.imap_unordered(run_obs, [(o, config, scratch) for o in todo])):
            print(f"[O near-copy] {i + 1}/{len(todo)} {r} {time.perf_counter() - t0:.0f} s", file=sys.stderr, flush=True)


def report(config: str) -> dict:
    from eval.metrics import bootstrap_ci
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    bs, seed = cfg["detectors"]["bootstrap"], cfg["seed"]
    rows = []
    for p in sorted((OUT / "runs").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        rows += [{"obs_id": r["obs_id"], "norad": r["norad"], "station": r["station"],
                  "timing_reliable": r["timing_reliable"], **x} for x in r["recordings"]]
    df = pd.DataFrame(rows)
    df["not_accepted"] = df["verdict"] != "accept"
    rep = {"n_observations": int(df["obs_id"].nunique()), "n_recordings_per_mode": int((df["mode"] == "exact").sum()),
           "ci": "per-observation mean, percentile bootstrap over observations", "modes": {}}
    for mode, g in df.groupby("mode"):
        m = {}
        for col in ("replay_detected", "any_alert_on_replay", "not_accepted"):
            v, lo, hi = bootstrap_ci(g.groupby("obs_id")[col].mean().to_numpy(float), bs["n"], bs["ci"], seed)
            m[col] = [round(v, 4), round(lo, 4), round(hi, 4)]
        tp, fp = int(g["replay_tp_frames"].sum()), int(g["replay_fp_frames"].sum())
        fn = int(g["replay_frames"].sum()) - tp
        m["replay_frames"] = {"tp": tp, "fp": fp, "fn": fn, "recall": round(tp / max(tp + fn, 1), 4),
                              "precision": round(tp / max(tp + fp, 1), 4)}
        m["events_detected"] = f"{int(g['replay_detected'].sum())} / {len(g)}"
        m["by_satellite"] = {str(k): round(float(s["replay_detected"].mean()), 3) for k, s in g.groupby("norad")}
        m["not_accepted_by_satellite"] = {str(k): round(float(s["not_accepted"].mean()), 3) for k, s in g.groupby("norad")}
        m["by_timing_reliable"] = {str(k): round(float(s["replay_detected"].mean()), 3) for k, s in g.groupby("timing_reliable")}
        m["by_group"] = {k: round(float(s["replay_detected"].mean()), 3) for k, s in g.groupby("group")}
        m["evidence_on_replay_frames"] = pd.Series([e for v in g["evidence_on_replay"] for e in v]).value_counts().to_dict()
        m["verdicts"] = g["verdict"].value_counts().to_dict()
        m["false_frame_alerts_per_recording"] = round(float(g["false_frame_alerts"].mean()), 3)
        rep["modes"][mode] = m
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "recordings.csv", index=False)
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(markdown(rep), encoding="utf-8")
    return rep


def markdown(r: dict) -> str:
    def c(v):
        return f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"
    L = ["# Near-copy replay evasion (Phase O), locked fusion, TEST split", "",
         f"{r['n_observations']} test observations, {r['n_recordings_per_mode']} recordings with a replay per mode "
         "(frames only). Values: per-observation mean [95 % bootstrap CI].", "",
         "| mode | replay detected (named replay) | any alert on a replayed frame | recording not accepted | "
         "frame recall | frame precision | events |", "|---|---|---|---|---|---|---|"]
    for k, m in r["modes"].items():
        L.append(f"| {k} | {c(m['replay_detected'])} | {c(m['any_alert_on_replay'])} | {c(m['not_accepted'])} | "
                 f"{m['replay_frames']['recall']:.3f} | {m['replay_frames']['precision']:.3f} | {m['events_detected']} |")
    L.append("")
    for k, m in r["modes"].items():
        L.append(f"- {k}: by satellite {m['by_satellite']}; by timing reliable {m['by_timing_reliable']}; "
                 f"by group {m['by_group']}; evidence {m['evidence_on_replay_frames']}; verdicts {m['verdicts']}.")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Near-copy replay evasion on TEST with the locked fusion")
    ap.add_argument("stage", choices=["run", "report"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--scratch", default=None)
    a = ap.parse_args(argv)
    if a.stage == "run":
        stage_run(a.config, a.workers, a.resume, a.scratch)
    else:
        print(json.dumps(report(a.config), indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
