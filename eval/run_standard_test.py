"""Phase N: the official standard scenario, end to end, on every TEST observation (labels read here only).

Scenario (challenge Track 1): replay of a valid 5-20-frame subsequence + one injected sequence anomaly + narrowband
interference at a hidden snr_db. The frozen dataset holds 10 such recipes per test observation ('combo_*', drawn
before any detector existed, each with its own seed = SHA-256 of obs:variant; snr_db ~ U[-10, +30] dB); they are the
seeds of this test. The clean and benign test variants of the same observations are run too, as the negatives
(recording-level false alarms, the tie-breaker).
For each recording: the recipe is applied to the raw observation in memory, written as a raw SatNOGS-like folder
(audio.ogg, demoddata/ with decoder-branch and rewrite copies, metadata.json with satellite, pass start and id only),
and detect/run.py runs on that folder exactly as on the jury's recording. Scoring against the frozen labels.
Requires the signed fusion lock (and the detector locks); run once.

Stages:  run [--workers N] [--resume]   ->  eval/out/standard_test/runs/<obs_id>.json
         report                          ->  eval/out/standard_test/report.{json,md}
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import shutil
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

OUT = Path("eval/out/standard_test")
GROUPS = ("combo", "clean", "benign")
LOCKS = ("replay", "sequence", "interference_v2", "fusion")


def require_locks(models_dir: str = "models") -> None:
    """Every lock must verify. The interference v2 lock (Phase L) still lists the Phase L detect/run.py, which Phase N
    replaced as a new version (decision 91): for that lock only detect/run.py may differ; the current run.py and all v2
    model files are covered by the fusion lock."""
    from eval.lock import LockError, lock_path, require_lock
    from trust import manifest as tm
    for name in LOCKS:
        if name != "interference_v2":
            require_lock(name, models_dir)
            continue
        m = json.loads(lock_path(name, models_dir).read_text(encoding="utf-8"))
        if not tm.verify(m):
            raise LockError(f"{name}: signature does not verify")
        bad = [f for f in tm.file_mismatches(m) if f != "detect/run.py"]
        if bad:
            raise LockError(f"{name}: locked files changed since freezing: {bad}")


# ---------------------------------------------------------------------------------------- materialize
def write_folder(d: Path, res: dict, clean: dict) -> None:
    """Raw SatNOGS-like folder of an applied recipe."""
    from attacks.replay import frame_name
    (d / "demoddata").mkdir(parents=True, exist_ok=True)
    for f in res["frames"]:
        data = bytes.fromhex(f["hex"])
        (d / "demoddata" / f["filename"]).write_bytes(data)
        if int(f.get("decoder_branches", 1) or 1) >= 2 and f["filename"].endswith("_g0"):
            (d / "demoddata" / (f["filename"][:-1] + "1")).write_bytes(data)
        for k, delay in enumerate(f.get("copy_delays_s") or []):
            name = frame_name(clean["obs_id"], clean["start"], f["t"] + float(delay), f["filename"]) + f"_r{k}"
            (d / "demoddata" / name).write_bytes(data)
    if res["audio"] is not None:
        y = np.asarray(res["audio"], dtype=np.float32)
        # Vorbis in 4096-sample blocks (decision 61: one large write overflows the stack on Windows)
        with sf.SoundFile(str(d / "audio.ogg"), "w", clean["sr"], 1, format="OGG", subtype="VORBIS") as fh:
            for i in range(0, len(y), 4096):
                fh.write(y[i:i + 4096])
    (d / "metadata.json").write_text(json.dumps({"id": clean["obs_id"], "norad_cat_id": clean["norad"],
                                                 "start": clean["start"].strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n",
                                     encoding="utf-8")


def score(rec: dict, out: dict, t: dict, duration: float) -> dict:
    """Recording-level results against the truth (eval/fusion_dev.truth)."""
    from eval.fusion_dev import interf_correct
    fu = out["fusion"]
    seq_files = {f for f, k in t["attacked_frames"].items() if k.startswith("sequence")}
    rep_files = {f for f, k in t["attacked_frames"].items() if k.startswith("replay")}
    seq_op = next((o for o in rec["operations"] if o["type"] == "sequence"), None)
    r = {"variant_id": rec["variant_id"], "group": rec["group"], "verdict": fu["verdict"],
         "confidence": fu["confidence_attacked"], "alert_names": fu["alert_names"], "n_alerts": len(fu["alerts"]),
         "runtime_s": out["runtime_s"], "peak_traced_mb": out["peak_traced_mb"], "timing_s": out["timing_s"],
         "attacked": rec["group"] == "combo", "benign_ops": t["benign"]}
    named_replay = {f["filename"] for f in fu["frames"] if f["name"] in ("replay", "replay_cross_pass")}
    alerted = {f["filename"] for f in fu["frames"]}
    r["replay_frames"] = len(rep_files)
    r["replay_tp_frames"] = len(named_replay & rep_files)
    r["replay_fp_frames"] = len(named_replay - rep_files)
    r["replay_detected"] = bool(named_replay & rep_files) if rep_files else None
    r["sequence_subtype"] = seq_op["subtype"] if seq_op else None
    r["sequence_detected"] = bool(alerted & seq_files) if seq_files else None
    names = {f["name"] for f in fu["frames"] if f["filename"] in seq_files}
    r["sequence_named_correctly"] = (seq_op["subtype"] in names) if seq_files else None
    r["false_frame_alerts"] = len(alerted - set(t["attacked_frames"]))
    ia = [a for a in fu["alerts"] if a["alert"] == "interference"]
    ok = [a for a in ia if interf_correct(a, t["interference"], duration)]
    r["interference_detected"] = bool(ok) if t["interference"] else None
    r["interference_false_alerts"] = len(ia) - len(ok)
    if t["interference"]:
        o = t["interference"][0]
        r.update(snr_db=o["snr_db"], shape=o["shape"], interf_full=sum(b - a for a, b in o["segments"]) >= 0.95 * duration,
                 interf_by_model=any("window_model" in a["evidence"] for a in ok),
                 interf_by_line=any("line_screen" in a["evidence"] for a in ok))
        starts = [a["t_start_s"] for a in ok if "window_model" in a["evidence"]]
        seg0 = min(s for s, _ in o["segments"])
        r["interference_delay_s"] = max(0.0, min(starts) - seg0) if starts else None
    rp = [a for a in fu["alerts"] if a["alert"] == "replay" and set(a["frames"]) & rep_files]
    if rp:
        a = rp[0]
        r["replay_delay_s"] = (a["first_online_alert_rel_s"] - a["t_first_rel_s"]) if a["first_online_alert_rel_s"] is not None else None
    if rec["group"] == "combo":
        r["all_three_detected"] = bool(r["replay_detected"] and r["sequence_detected"] and r["interference_detected"])
    r["alerts"] = [{k: a.get(k) for k in ("alert", "name", "confidence", "decision", "evidence", "reason")}
                   for a in fu["alerts"]]
    return r


def run_obs(args: tuple) -> dict:
    oid, config, scratch = args
    from attacks.apply import apply_recipe
    from build_features import load_clean
    from detect.run import run
    from eval.fusion_dev import truth
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    flags = {int(i): f for s in sp["satellites"].values() for i, f in s["obs"].items()}
    clean = load_clean(oid, cfg, obs.loc[oid], flags[oid])
    tr = truth(oid, cfg)
    dur = float(obs.loc[oid, "duration_s"])
    rows = []
    for p in sorted((Path(cfg["attacks"]["recipes_dir"]) / str(oid)).glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec["group"] not in GROUPS:
            continue
        res = apply_recipe(rec, clean)
        d = Path(tempfile.mkdtemp(prefix=f"std_{oid}_", dir=scratch))
        try:
            write_folder(d, res, clean)
            out = run(d, cfg)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        rows.append(score(rec, out, tr[rec["variant_id"]], dur))
    res = {"obs_id": oid, "norad": int(obs.loc[oid, "norad"]), "station": int(flags[oid]["station_id"]),
           "timing_reliable": bool(flags[oid]["timing_reliable"]), "recordings": rows}
    OUT.joinpath("runs").mkdir(parents=True, exist_ok=True)
    tmp = OUT / "runs" / f"{oid}.tmp"
    tmp.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    tmp.replace(OUT / "runs" / f"{oid}.json")
    return {"obs_id": oid, "n": len(rows)}


def stage_run(config: str, workers: int, resume: bool, scratch: str | None) -> None:
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    require_locks(cfg["detectors"]["models_dir"])
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    ids = sorted(int(o) for s in sp["satellites"].values() for o in s["splits"]["test"]
                 if s["obs"][str(o)]["frames"] and s["obs"][str(o)]["audio"])
    todo = [o for o in ids if not (resume and (OUT / "runs" / f"{o}.json").exists())]
    scratch = scratch or tempfile.gettempdir()
    print(f"[N standard test] {len(todo)} of {len(ids)} test observations", file=sys.stderr, flush=True)
    t0 = time.perf_counter()
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=1) as pool:
        for i, r in enumerate(pool.imap_unordered(run_obs, [(o, config, scratch) for o in todo])):
            print(f"[N standard test] {i + 1}/{len(todo)} {r} {time.perf_counter() - t0:.0f} s", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------- report
def _ci(per_obs: pd.Series, bs: dict, seed: int) -> list:
    from eval.metrics import bootstrap_ci
    m, lo, hi = bootstrap_ci(per_obs.to_numpy(float), bs["n"], bs["ci"], seed)
    return [round(m, 4), round(lo, 4), round(hi, 4)]


def report(config: str) -> dict:
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    bs, seed = cfg["detectors"]["bootstrap"], cfg["seed"]
    rows = []
    for p in sorted((OUT / "runs").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        for x in r["recordings"]:
            rows.append({"obs_id": r["obs_id"], "norad": r["norad"], "station": r["station"],
                         "timing_reliable": r["timing_reliable"], **{k: v for k, v in x.items() if k != "alerts"}})
    df = pd.DataFrame(rows)
    df["alarm"] = df["verdict"] != "accept"
    att, neg = df[df["group"] == "combo"], df[df["group"] != "combo"]

    def per_obs(d, col):
        return d.groupby("obs_id")[col].apply(lambda s: s.dropna().astype(float).mean())

    rep = {"n_test_observations": int(df["obs_id"].nunique()), "n_standard_recordings": int(len(att)),
           "n_negative_recordings": int(len(neg)), "seeds_per_observation": int(att.groupby("obs_id").size().median()),
           "ci": "per-observation mean, percentile bootstrap over observations"}
    for k in ("replay_detected", "sequence_detected", "sequence_named_correctly", "interference_detected",
              "all_three_detected", "alarm"):
        rep[f"standard_{k}"] = _ci(per_obs(att, k), bs, seed)
    tp, fp, fn = att["replay_tp_frames"].sum(), att["replay_fp_frames"].sum(), (att["replay_frames"] - att["replay_tp_frames"]).sum()
    rep["replay_frames_pooled"] = {"precision": round(tp / max(tp + fp, 1), 4), "recall": round(tp / max(tp + fn, 1), 4),
                                   "f1": round(2 * tp / max(2 * tp + fp + fn, 1), 4)}
    rep["false_frame_alerts_per_standard_recording"] = round(float(att["false_frame_alerts"].mean()), 3)
    rep["false_interference_alerts_per_standard_recording"] = round(float(att["interference_false_alerts"].mean()), 3)
    rep["negatives_false_alarm"] = _ci(per_obs(neg, "alarm"), bs, seed)
    rep["negatives_false_alarm_clean"] = round(float(neg.loc[neg["group"] == "clean", "alarm"].mean()), 4)
    rep["negatives_false_alarm_benign"] = round(float(neg.loc[neg["group"] == "benign", "alarm"].mean()), 4)
    rep["negatives_false_alarm_by_station"] = {str(k): round(float(g["alarm"].mean()), 4) for k, g in neg.groupby("station")}
    rep["negatives_alert_names"] = pd.Series([n for v in neg["alert_names"] for n in v]).value_counts().to_dict()
    rep["verdicts"] = {g: d["verdict"].value_counts().to_dict() for g, d in df.groupby("group")}
    rep["sequence_by_subtype"] = {k: {"n": int(len(g)), "detected": round(float(g["sequence_detected"].astype(float).mean()), 3),
                                      "named": round(float(g["sequence_named_correctly"].astype(float).mean()), 3)}
                                  for k, g in att.groupby("sequence_subtype")}
    rep["sequence_by_timing"] = {str(k): round(float(g["sequence_detected"].astype(float).mean()), 3)
                                 for k, g in att.groupby("timing_reliable")}
    it = att.dropna(subset=["snr_db"])
    rep["interference_by_duration"] = {("full_pass" if k else "segment"): {
        "n": int(len(g)), "detected": round(float(g["interference_detected"].astype(float).mean()), 3),
        "by_window_model": round(float(g["interf_by_model"].astype(float).mean()), 3),
        "by_line_screen": round(float(g["interf_by_line"].astype(float).mean()), 3)} for k, g in it.groupby("interf_full")}
    rep["interference_by_snr10"] = {str(int(k)): {"n": int(len(g)), "detected": round(float(g["interference_detected"].astype(float).mean()), 3)}
                                    for k, g in it.groupby(it["snr_db"] // 10 * 10)}
    rep["interference_by_shape"] = {k: round(float(g["interference_detected"].astype(float).mean()), 3) for k, g in it.groupby("shape")}
    rep["per_satellite"] = {str(k): {c: round(float(g[c].dropna().astype(float).mean()), 3) for c in
                                     ("replay_detected", "sequence_detected", "interference_detected", "all_three_detected")}
                            for k, g in att.groupby("norad")}
    y, pconf = df["attacked"].astype(float).to_numpy(), df["confidence"].astype(float).to_numpy()
    idx = np.minimum((pconf * 10).astype(int), 9)
    rep["ece_recording"] = round(float(sum((idx == b).mean() * abs(pconf[idx == b].mean() - y[idx == b].mean())
                                           for b in range(10) if (idx == b).any())), 4)
    lat = {"replay_delay_s_median": att["replay_delay_s"].dropna().median() if "replay_delay_s" in att else None,
           "replay_delay_s_max": att["replay_delay_s"].dropna().max() if "replay_delay_s" in att else None,
           "interference_delay_s_median": att["interference_delay_s"].dropna().median(),
           "interference_delay_s_max": att["interference_delay_s"].dropna().max(),
           "line_screen": "offline: decides at the end of the pass"}
    rep["latency"] = {k: (round(float(v), 2) if isinstance(v, (int, float, np.floating)) and not math.isnan(v) else v)
                      for k, v in lat.items()}
    tm = pd.DataFrame(list(df["timing_s"]))
    rep["efficiency"] = {"runtime_s_median": round(float(df["runtime_s"].median()), 2),
                         "runtime_s_max": round(float(df["runtime_s"].max()), 2),
                         "peak_traced_mb_max": round(float(df["peak_traced_mb"].max()), 1),
                         "stage_s_median": {c: round(float(tm[c].median()), 3) for c in tm.columns}}
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "recordings.csv", index=False)
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(markdown(rep), encoding="utf-8")
    return rep


def markdown(r: dict) -> str:
    def c(v):
        return f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"
    L = ["# Standard test (Track 1 scenario), end to end on raw folders, TEST split", "",
         f"{r['n_test_observations']} test observations x {r['seeds_per_observation']} seeds = {r['n_standard_recordings']} "
         f"attacked recordings; {r['n_negative_recordings']} clean / benign recordings as negatives. "
         "Values: per-observation mean [95 % bootstrap CI over observations].", "",
         "| metric | value |", "|---|---|",
         f"| replay detected | {c(r['standard_replay_detected'])} |",
         f"| sequence anomaly detected | {c(r['standard_sequence_detected'])} |",
         f"| sequence anomaly named correctly | {c(r['standard_sequence_named_correctly'])} |",
         f"| interference detected | {c(r['standard_interference_detected'])} |",
         f"| all three detected | {c(r['standard_all_three_detected'])} |",
         f"| attacked recording not accepted | {c(r['standard_alarm'])} |",
         f"| **false alarm (clean / benign recording not accepted)** | {c(r['negatives_false_alarm'])} |",
         f"| replay frames P / R / F1 (pooled) | {r['replay_frames_pooled']['precision']:.3f} / "
         f"{r['replay_frames_pooled']['recall']:.3f} / {r['replay_frames_pooled']['f1']:.3f} |",
         f"| recording ECE | {r['ece_recording']:.3f} |", "",
         f"False alarms: clean {r['negatives_false_alarm_clean']:.3f}, benign {r['negatives_false_alarm_benign']:.3f}; "
         f"by station {r['negatives_false_alarm_by_station']}; alert names on negatives {r['negatives_alert_names']}.",
         f"Sequence by subtype: {r['sequence_by_subtype']}.",
         f"Interference by duration: {r['interference_by_duration']}; by snr_db (10 dB bins): {r['interference_by_snr10']}.",
         f"Latency: {r['latency']}. Efficiency: {r['efficiency']}.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Official standard scenario on the test observations, end to end")
    ap.add_argument("stage", choices=["run", "report"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--scratch", default=None, help="folder for the temporary recording folders")
    a = ap.parse_args(argv)
    if a.stage == "run":
        stage_run(a.config, a.workers, a.resume, a.scratch)
    else:
        print(json.dumps(report(a.config), indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
