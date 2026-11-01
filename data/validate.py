"""Phase H: dataset validation (docs/DATASET_SPEC.md section 8) -> validation.out_md / out_json.

Checks (exit code 1 if a hard check fails):
  1 leakage            each observation in one split; test stations absent from train; recipes and
                       cross-pass sources stay in their split; detect/ never mentions data/labels
  2 label consistency  replay: L labelled copies per replay op; sequence: exactly one anomaly
                       (2 frames for swap, else 1); frame counts add up; interference windows match
                       the recipe segments (>= 0.5 s overlap); window count unchanged
  3 shortcut test      within-observation, benign-matched test on structure cues (duration, number
                       of windows) and level cues (post-chain peak and RMS, mean power_db); logic in
                       data/diag_shortcut.py (decisions 63-65). `--only shortcut` reruns it from the
                       cue cache and patches the existing report
  4 trivial baseline   single-feature window AUC per SNR bin, test split (reported)
  5 distribution       counts, SNR histogram, attacked fractions, attack positions (reported)
  6 reproducibility    2 observations rebuilt twice: byte-identical to Phase G and to Phase D
  7 recipe round-trip  attacked audio written to WAV, read back and re-processed: identical rows
Labels are read here only (eval-side code), never by detect/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
from build_features import load_clean, process_observation, recipes_digest, tables_from_result  # noqa: E402
from build_tables import common_chain, write_csv  # noqa: E402
from attacks.apply import apply_recipe  # noqa: E402
from diag_shortcut import NOT_ATTACK_GROUPS, assemble, from_cache, shortcut_markdown, shortcut_report  # noqa: E402


# ---------------------------------------------------------------- 1 leakage
def leakage(splits: dict, recipes: dict, detect_dir: Path) -> dict:
    """recipes: {(obs_id, variant_id): recipe}. Returns {"ok", "problems", "info"}."""
    problems, info = [], {}
    split_of = {}
    for norad, sat in splits["satellites"].items():
        for split, ids in sat["splits"].items():
            for i in ids:
                if i in split_of:
                    problems.append(f"obs {i} in {split_of[i]} and {split}")
                split_of[i] = split
        stations = {s: {sat["obs"][str(i)]["station_id"] for i in ids} for s, ids in sat["splits"].items()}
        shared = stations.get("test", set()) & stations.get("train", set())
        if shared:
            problems.append(f"{norad}: test stations also in train: {sorted(shared)}")
        info[norad] = {"test_stations": sorted(stations.get("test", set())),
                       "test_stations_in_calib": sorted(stations.get("test", set()) & stations.get("calib", set())),
                       "n_obs": {s: len(ids) for s, ids in sat["splits"].items()}}
    for (i, vid), rec in recipes.items():
        if i not in split_of:
            problems.append(f"recipe {i}/{vid}: observation not in splits")
        elif rec["split"] != split_of[i]:
            problems.append(f"recipe {i}/{vid}: split {rec['split']} != {split_of[i]}")
        for op in rec["operations"]:
            src = op.get("source_obs")
            if op["type"] == "replay" and src not in (None, i) and split_of.get(src) != split_of.get(i):
                problems.append(f"recipe {i}/{vid}: cross-pass source {src} in split {split_of.get(src)}")
    pat = re.compile(r"labels")
    hits = [f"{p.name}:{n + 1}" for p in sorted(detect_dir.glob("*.py"))
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines()) if pat.search(line)]
    if hits:
        problems.append(f"detect/ mentions labels: {hits}")
    info["detect_files_scanned"] = len(list(detect_dir.glob("*.py")))
    return {"ok": not problems, "problems": problems, "info": info}


# ---------------------------------------------------------------- 2 label consistency
def expected_frame_delta(rec: dict) -> int:
    """Change in frame count implied by the recipe."""
    d = 0
    for op in rec["operations"]:
        if op["type"] == "replay":
            d += len(op["source_filenames"])
        elif op["type"] == "sequence":
            if op["subtype"] == "deletion":
                d -= len(op["filenames"])
            elif op["subtype"] in ("duplicate", "forged"):
                d += 1
    return d


def label_consistency(rec: dict, lab: pd.DataFrame, n_frames: int, n_frames_clean: int,
                      t_start: np.ndarray | None, win_s: float, min_overlap_s: float) -> list[str]:
    """Problems of one variant. lab = labels rows of the variant; t_start = clean window starts."""
    vid, ops = rec["variant_id"], rec["operations"]
    fl, wl = lab[lab.unit == "frame"], lab[lab.unit == "window"]
    out = []
    att = fl[fl.is_attack == 1]
    n_rep = sum(len(o["source_filenames"]) for o in ops if o["type"] == "replay")
    seq = [o for o in ops if o["type"] == "sequence"]
    n_seq = sum(2 if o["subtype"] == "swap" else 1 for o in seq)
    if (att.attack_type == "replay").sum() != n_rep:
        out.append(f"{vid}: {(att.attack_type == 'replay').sum()} replay frame labels, recipe has {n_rep}")
    if len(seq) > 1:
        out.append(f"{vid}: {len(seq)} sequence anomalies (max 1)")
    if (att.attack_type == "sequence").sum() != n_seq:
        out.append(f"{vid}: {(att.attack_type == 'sequence').sum()} sequence frame labels, expected {n_seq}")
    if len(att) != n_rep + n_seq:
        out.append(f"{vid}: {len(att)} attacked frames, expected {n_rep + n_seq}")
    if n_frames != n_frames_clean + expected_frame_delta(rec):
        out.append(f"{vid}: {n_frames} frames, expected {n_frames_clean + expected_frame_delta(rec)}")
    if t_start is None:
        return out
    if len(wl) != len(t_start):
        out.append(f"{vid}: {len(wl)} windows, clean has {len(t_start)}")
        return out
    is_att = wl.sort_values("index").is_attack.to_numpy() == 1
    if rec["group"] in NOT_ATTACK_GROUPS and is_att.any():
        out.append(f"{vid}: {is_att.sum()} attacked windows in a {rec['group']} variant")
    segs = [s for o in ops if o["type"] == "interference" for s in o["segments"]]
    exp = np.array([sum(max(0.0, min(t + win_s, b) - max(t, a)) for a, b in segs) >= min_overlap_s for t in t_start])
    audio_replay = any(o["type"] == "replay" and o.get("audio") for o in ops)
    if (exp & ~is_att).any():
        out.append(f"{vid}: {(exp & ~is_att).sum()} interference windows not labelled")
    if not audio_replay and (is_att & ~exp).any():
        out.append(f"{vid}: {(is_att & ~exp).sum()} attacked windows outside the interference segments")
    snrs = {o["snr_db"] for o in ops if o["type"] == "interference"}
    got = wl[(wl.attack_type == "interference") & (wl.is_attack == 1)].snr_db
    if len(got) and not np.allclose(got, list(snrs)[0]):
        out.append(f"{vid}: window snr_db differs from recipe {snrs}")
    return out


# ---------------------------------------------------------------- 3 shortcut cues
def cue_rows(obs_id: int, cfg: dict) -> pd.DataFrame:
    """Post-chain duration, peak and RMS of every audio variant of one observation (cached)."""
    out = Path(cfg["validation"]["cues_dir"]) / f"{obs_id}.csv"
    rdir = Path(cfg["attacks"]["recipes_dir"]) / str(obs_id)
    digest = recipes_digest(rdir)
    if out.exists():
        old = pd.read_csv(out)
        if len(old) and (old.recipes_sha256 == digest).all():
            return old
    obs_tab = pd.read_csv(Path(cfg["tables"]["out_dir"]) / "observations.csv").set_index("obs_id")
    splits = json.loads(Path(cfg["splits"]["out_json"]).read_text(encoding="utf-8"))
    flags = {int(i): f for s in splits["satellites"].values() for i, f in s["obs"].items()}
    clean = load_clean(obs_id, cfg, obs_tab.loc[obs_id], flags[obs_id])
    cache = {}

    def load_source(i):
        if i not in cache:
            cache[i] = load_clean(i, cfg, obs_tab.loc[i], flags[i])
        return cache[i]

    rows = []
    for p in sorted(rdir.glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        res = apply_recipe(rec, clean, load_source)
        y = common_chain(res["audio"], clean["sr"], cfg["tables"]["chain"]).astype(np.float64)
        rows.append({"obs_id": obs_id, "variant_id": rec["variant_id"], "duration_s": len(y) / clean["sr"],
                     "peak_abs": float(np.max(np.abs(y))), "rms_db": float(10 * np.log10(np.mean(y ** 2) + 1e-20)),
                     "recipes_sha256": digest})
    df = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(df, out)
    return pd.read_csv(out)


def _cue_worker(args):
    obs_id, config_path = args
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    return cue_rows(obs_id, cfg)


# ---------------------------------------------------------------- 4 single-feature AUC per SNR bin
def snr_bin(snr: np.ndarray, width: float) -> np.ndarray:
    return np.round(np.asarray(snr, float) / width) * width


def single_feature_auc(win: pd.DataFrame, features: list[str], width: float) -> list[dict]:
    """win: windows joined with labels (split, norad, is_attack, attack_type, snr_db, overlap_frac).

    Positives: interference-attacked windows; negatives: windows with no attack overlap (clean,
    benign and unattacked parts). Orientation of each feature fixed on train, AUC reported on test.
    """
    out = []
    pos_all = (win.is_attack == 1) & (win.attack_type == "interference")
    neg_all = (win.is_attack == 0) & (win.overlap_frac == 0)
    for norad, g in win.groupby("norad"):
        pos, neg = pos_all[g.index], neg_all[g.index]
        tr, te = g.split == "train", g.split == "test"
        bins = snr_bin(g.snr_db, width)
        for f in features:
            v = g[f].to_numpy(float)
            ok = ~np.isnan(v)
            m = (tr & (pos | neg)).to_numpy() & ok
            sign = 1.0 if roc_auc_score(pos.to_numpy()[m], v[m]) >= 0.5 else -1.0
            nm = (te & neg).to_numpy() & ok
            row = {"norad": int(norad), "feature": f, "direction": "+" if sign > 0 else "-", "n_neg": int(nm.sum())}
            for b in sorted(set(bins[(te & pos).to_numpy()])):
                pm = (te & pos).to_numpy() & ok & (bins == b)
                y = np.r_[np.ones(pm.sum()), np.zeros(nm.sum())]
                row[f"{b:+.0f}"] = round(float(roc_auc_score(y, sign * np.r_[v[pm], v[nm]])), 3)
                row[f"n{b:+.0f}"] = int(pm.sum())
            out.append(row)
    return out


# ---------------------------------------------------------------- 6 / 7 reproducibility and round-trip
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def variant_lines(path: Path, obs_id: int, vid: str) -> list[str]:
    pre = f"{obs_id},{vid},"
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith(pre)]


def reproducibility(obs_ids: list[int], cfg: dict, config_path: str) -> list[dict]:
    """Rebuild each observation twice into temporary folders; compare with Phase G and Phase D files."""
    out = []
    feat = Path(cfg["features"]["out_dir"])
    for i in obs_ids:
        hashes = {"phase_g": {f: sha(feat / str(i) / f"{f}.csv") for f in ("frames", "windows", "labels")}}
        for run in ("rebuild_1", "rebuild_2"):
            with tempfile.TemporaryDirectory() as tmp:
                c = {**cfg, "features": {**cfg["features"], "out_dir": tmp}}
                cpath = Path(tmp) / "config.yaml"
                cpath.write_text(yaml.safe_dump(c), encoding="utf-8")
                process_observation((i, str(cpath)))
                hashes[run] = {f: sha(Path(tmp) / str(i) / f"{f}.csv") for f in ("frames", "windows", "labels")}
        tables = Path(cfg["tables"]["out_dir"])
        clean_vid = cfg["tables"]["clean_variant"]
        phase_d = {f: variant_lines(tables / f"{f}.csv", i, clean_vid) == variant_lines(feat / str(i) / f"{f}.csv", i, clean_vid)
                   for f in ("frames", "windows")}
        same = hashes["phase_g"] == hashes["rebuild_1"] == hashes["rebuild_2"]
        out.append({"obs_id": i, "byte_identical": same, "clean_equals_phase_d": phase_d,
                    "sha256": hashes["phase_g"], "ok": same and all(phase_d.values())})
    return out


def roundtrip(obs_id: int, variants: list[str], cfg: dict) -> list[dict]:
    """Materialize attacked audio (before the chain) as WAV, read back, re-process, compare rows."""
    obs_tab = pd.read_csv(Path(cfg["tables"]["out_dir"]) / "observations.csv").set_index("obs_id")
    splits = json.loads(Path(cfg["splits"]["out_json"]).read_text(encoding="utf-8"))
    flags = {int(i): f for s in splits["satellites"].values() for i, f in s["obs"].items()}
    clean = load_clean(obs_id, cfg, obs_tab.loc[obs_id], flags[obs_id])
    feat = Path(cfg["features"]["out_dir"]) / str(obs_id)
    out = []
    for vid in variants:
        p = Path(cfg["attacks"]["recipes_dir"]) / str(obs_id) / f"{vid}.json"
        if not p.exists():
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        res = apply_recipe(rec, clean, lambda j: load_clean(j, cfg, obs_tab.loc[j], flags[j]))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / f"{obs_id}_{vid}.wav"
            subtype = "DOUBLE" if res["audio"].dtype == np.float64 else "FLOAT"
            sf.write(str(wav), res["audio"], clean["sr"], subtype=subtype)
            back, sr = sf.read(str(wav), dtype=res["audio"].dtype)
            audio_equal = bool(sr == clean["sr"] and np.array_equal(back, res["audio"]))
            tabs = tables_from_result({**res, "audio": back}, rec, clean, cfg)
            same = {}
            for name, df in zip(("frames", "windows", "labels"), tabs):
                write_csv(df, Path(tmp) / f"{name}.csv")
                same[name] = variant_lines(Path(tmp) / f"{name}.csv", obs_id, vid) == variant_lines(feat / f"{name}.csv", obs_id, vid)
        out.append({"obs_id": obs_id, "variant_id": vid, "wav_subtype": subtype, "audio_equal": audio_equal,
                    "rows_identical": same, "ok": audio_equal and all(same.values())})
    return out


# ---------------------------------------------------------------- 5 distribution
def hist(values, width: float) -> dict:
    b = snr_bin(values, width)
    return {f"{k:+.0f}": int(v) for k, v in sorted(Counter(b.tolist()).items())}


def distribution(recipes: dict, info: dict, labels: pd.DataFrame, nframes: pd.DataFrame, width: float) -> dict:
    rec_df = pd.DataFrame([{"obs_id": i, "variant_id": v, "group": r["group"], "split": r["split"],
                            "norad": r["norad"], "station_id": info[i]["station_id"]} for (i, v), r in recipes.items()])
    d = {"variants_by_split_satellite_group": rec_df.groupby(["norad", "split", "group"]).size().unstack(fill_value=0)}
    obs_df = rec_df.drop_duplicates("obs_id")
    d["stations"] = (rec_df.groupby(["norad", "split", "station_id"])
                     .agg(n_obs=("obs_id", "nunique"), n_variants=("variant_id", "size")).reset_index())
    d["obs_by_split_satellite"] = obs_df.groupby(["norad", "split"]).size().unstack(fill_value=0)
    ops = []
    for (i, v), r in recipes.items():
        for op in r["operations"]:
            if op["type"] in ("replay", "sequence", "interference"):
                ops.append({"norad": r["norad"], "split": r["split"], "type": op["type"], "subtype": op.get("subtype", ""),
                            "evasion": int(op.get("evasion", 0) or (r["group"] == "evasion")), "group": r["group"],
                            "snr_db": op.get("snr_db", np.nan), "L": len(op.get("source_filenames", [])) or np.nan})
            elif r["group"] in ("benign", "clean") or op["type"] in ("gain", "noise_rise", "fading", "edge_attenuation"):
                ops.append({"norad": r["norad"], "split": r["split"], "type": "benign", "subtype": op["type"],
                            "evasion": 0, "group": r["group"], "snr_db": np.nan, "L": np.nan})
    ops = pd.DataFrame(ops)
    d["operations_by_type_subtype"] = ops.groupby(["type", "subtype", "split"]).size().unstack(fill_value=0)
    it = ops[ops.type == "interference"]
    d["snr_hist"] = {f"{g}/{s}": hist(x.snr_db, width) for (g, s), x in it.groupby(["group", "split"])}
    rp = ops[ops.type == "replay"]
    d["replay_length"] = {"min": int(rp.L.min()), "median": float(rp.L.median()), "max": int(rp.L.max())}
    w = labels[labels.unit == "window"]
    att_var = w.groupby(["obs_id", "variant_id"]).is_attack.max()
    w = w.join(att_var.rename("variant_attacked"), on=["obs_id", "variant_id"])
    w = w.merge(rec_df[["obs_id", "variant_id", "split", "norad"]], on=["obs_id", "variant_id"])
    d["attacked_window_frac"] = (w.groupby(["norad", "split"])
                                 .apply(lambda g: pd.Series({"all_variants": g.is_attack.mean(),
                                                             "attacked_variants": g[g.variant_attacked == 1].is_attack.mean(),
                                                             "n_windows": len(g)}), include_groups=False))
    inr = w[(w.is_attack == 1) & (w.attack_type == "interference")].drop_duplicates(["obs_id", "variant_id"]).inr_db
    d["inr_db"] = {"p5": round(float(inr.quantile(0.05)), 1), "median": round(float(inr.median()), 1),
                   "p95": round(float(inr.quantile(0.95)), 1)}
    f = labels[labels.unit == "frame"].merge(rec_df[["obs_id", "variant_id", "split", "norad"]], on=["obs_id", "variant_id"])
    d["attacked_frame_frac"] = f.groupby(["norad", "split"]).is_attack.mean().unstack()
    d["frame_labels"] = f[f.is_attack == 1].groupby(["attack_type", "subtype"]).size()
    # position of the anomaly in the pass (principle 4: not always at the end), in quarters
    fa = f[f.is_attack == 1].join(nframes, on=["obs_id", "variant_id"])
    first = fa.groupby(["obs_id", "variant_id", "attack_type"]).agg(idx=("index", "min"), n=("n_frames", "first")).reset_index()
    first["quarter"] = np.minimum((4 * first.idx / first.n).astype(int), 3) + 1
    pos = {t: {f"Q{q}": int(c) for q, c in sorted(Counter(g.quarter).items())} for t, g in first.groupby("attack_type")}
    seg = [op_["segments"][0][0] / info[i]["duration_s"] for (i, v), r in recipes.items() for op_ in r["operations"]
           if op_["type"] == "interference" and info[i]["duration_s"] and op_["segments"][0][0] > 0]
    pos["interference (segment onset)"] = {f"Q{q}": int(c) for q, c in sorted(Counter(np.minimum((4 * np.array(seg)).astype(int), 3) + 1).items())}
    d["anomaly_position_quarters"] = pos
    return d


# ---------------------------------------------------------------- report
def md_table(df: pd.DataFrame) -> str:
    df = df.reset_index() if not isinstance(df.index, pd.RangeIndex) else df
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns) + "\n"
    fmt = lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)
    return head + "".join("| " + " | ".join(fmt(x) for x in row) + " |\n" for row in df.itertuples(index=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--workers", type=int, default=1, help="processes for the shortcut-cue pass")
    ap.add_argument("--only", choices=["shortcut"], help="rerun only this check from the cache and patch the report")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    vc, qc = cfg["validation"], cfg["qc"]
    win_s, min_ov = qc["psd"]["win_s"], cfg["features"]["window_min_overlap_s"]
    splits = json.loads(Path(cfg["splits"]["out_json"]).read_text(encoding="utf-8"))
    obs_tab = pd.read_csv(Path(cfg["tables"]["out_dir"]) / "observations.csv").set_index("obs_id")
    rdir, feat = Path(cfg["attacks"]["recipes_dir"]), Path(cfg["features"]["out_dir"])
    ids = sorted(int(p.name) for p in rdir.iterdir() if p.is_dir())
    recipes = {(i, p.stem): json.loads(p.read_text(encoding="utf-8")) for i in ids for p in sorted((rdir / str(i)).glob("*.json"))}
    info = {i: {"station_id": int(obs_tab.loc[i, "station_id"]), "duration_s": float(obs_tab.loc[i, "duration_s"])} for i in ids}
    report, fails = {}, []
    if args.only == "shortcut":
        return only_shortcut(cfg, recipes)

    print("[H] 1 leakage", file=sys.stderr, flush=True)
    report["leakage"] = leakage(splits, recipes, ROOT / "detect")
    fails += [] if report["leakage"]["ok"] else ["leakage"]

    # shortcut cues first (slow, cached per observation), before the Phase G tables are held in memory
    print("[H] 3a shortcut cues (post-chain peak / RMS)", file=sys.stderr, flush=True)
    rt = json.loads((feat / "runtime.json").read_text(encoding="utf-8"))
    audio_ids = [i for i in ids if rt[str(i)]["audio_s"] > 0]
    ctx = mp.get_context("spawn")
    with ctx.Pool(args.workers, maxtasksperchild=1) as pool:
        cues = pd.concat(list(pool.imap_unordered(_cue_worker, [(i, args.config) for i in audio_ids])), ignore_index=True)

    print("[H] 2 label consistency (loading Phase G tables)", file=sys.stderr, flush=True)
    labs, wins, nfr, problems = [], [], [], []
    for i in ids:
        lab = pd.read_csv(feat / str(i) / "labels.csv")
        fr = pd.read_csv(feat / str(i) / "frames.csv", usecols=["obs_id", "variant_id"])
        w = pd.read_csv(feat / str(i) / "windows.csv")
        n = fr.groupby("variant_id").size()
        ts = w[w.variant_id == "clean"].sort_values("win_idx").t_start_s.to_numpy() if len(w) else None
        for vid, g in lab.groupby("variant_id"):
            problems += [f"{i}/{p}" for p in label_consistency(recipes[(i, vid)], g, int(n.get(vid, 0)),
                                                                int(n.get("clean", 0)), ts, win_s, min_ov)]
        if set(lab.variant_id) != {v for (j, v) in recipes if j == i}:
            problems.append(f"{i}: label variants differ from recipes")
        labs.append(lab)
        wins.append(w)
        nfr.append(n.rename("n_frames").reset_index().assign(obs_id=i))
    labels = pd.concat(labs, ignore_index=True)
    windows = pd.concat(wins, ignore_index=True)
    nframes = pd.concat(nfr, ignore_index=True).set_index(["obs_id", "variant_id"])
    del labs, wins
    report["label_consistency"] = {"ok": not problems, "n_variants": len(recipes), "problems": problems[:50],
                                   "n_problems": len(problems)}
    fails += [] if not problems else ["label_consistency"]

    print("[H] 3 shortcut test", file=sys.stderr, flush=True)
    wagg = windows.groupby(["obs_id", "variant_id"]).agg(n_windows=("win_idx", "size"), mean_power_db=("power_db", "mean"))
    report["shortcut"] = shortcut_report(assemble(cues, wagg, recipes), vc, cfg["seed"])
    fails += [] if report["shortcut"]["ok"] else ["shortcut"]

    print("[H] 4 single-feature AUC per SNR bin", file=sys.stderr, flush=True)
    wl = labels[labels.unit == "window"].rename(columns={"index": "win_idx"})
    wj = windows.merge(wl[["obs_id", "variant_id", "win_idx", "is_attack", "attack_type", "snr_db", "overlap_frac"]],
                       on=["obs_id", "variant_id", "win_idx"])
    wj["split"] = [recipes[(i, v)]["split"] for i, v in zip(wj.obs_id, wj.variant_id)]
    wj["norad"] = [recipes[(i, v)]["norad"] for i, v in zip(wj.obs_id, wj.variant_id)]
    report["single_feature_auc"] = single_feature_auc(wj.reset_index(drop=True), vc["single_features"], vc["snr_bin_db"])
    del wj

    print("[H] 5 distribution", file=sys.stderr, flush=True)
    dist = distribution(recipes, info, labels, nframes, vc["snr_bin_db"])

    print("[H] 6 reproducibility", file=sys.stderr, flush=True)
    del windows, labels
    repro_ids = []
    for norad in sorted({r["norad"] for r in recipes.values()}):
        cand = [int(k) for k, v in rt.items() if k != "_progress" and v["norad"] == norad and v["audio_s"] > 0
                and (int(k), "replay_00") in recipes]
        repro_ids.append(min(cand, key=lambda k: rt[str(k)]["audio_s"]))
    report["reproducibility"] = reproducibility(repro_ids, cfg, args.config)
    fails += [] if all(r["ok"] for r in report["reproducibility"]) else ["reproducibility"]

    print("[H] 7 recipe round-trip", file=sys.stderr, flush=True)
    rt_obs = next(i for i in repro_ids if all((i, v) in recipes for v in vc["roundtrip_variants"]))
    report["roundtrip"] = roundtrip(rt_obs, vc["roundtrip_variants"], cfg)
    fails += [] if all(r["ok"] for r in report["roundtrip"]) else ["roundtrip"]

    report["distribution"] = {k: (v.reset_index().to_dict(orient="records") if isinstance(v, (pd.DataFrame, pd.Series)) else v)
                              for k, v in dist.items()}
    report["failed_checks"] = fails
    Path(vc["out_json"]).write_text(json.dumps(report, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n",
                                    encoding="utf-8")
    write_markdown(Path(vc["out_md"]), report, dist, vc)
    print(json.dumps({"failed_checks": fails, "shortcut_chain_traces": report["shortcut"]["n_chain_trace_failures"],
                      "label_problems": len(problems), "leakage_problems": report["leakage"]["problems"]}, indent=1))
    return 1 if fails else 0


def only_shortcut(cfg: dict, recipes: dict) -> int:
    """Rerun check 3 from the cue cache; replace it in the existing JSON and Markdown reports."""
    vc = cfg["validation"]
    jpath, mpath = Path(vc["out_json"]), Path(vc["out_md"])
    report = json.loads(jpath.read_text(encoding="utf-8"))
    report["shortcut"] = shortcut_report(from_cache(cfg, recipes), vc, cfg["seed"])
    fails = [f for f in report["failed_checks"] if f != "shortcut"] + ([] if report["shortcut"]["ok"] else ["shortcut"])
    order = ["leakage", "label_consistency", "shortcut", "reproducibility", "roundtrip"]
    report["failed_checks"] = sorted(fails, key=lambda f: order.index(f) if f in order else len(order))
    jpath.write_text(json.dumps(report, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n",
                     encoding="utf-8")
    md = mpath.read_text(encoding="utf-8")
    a, b = md.index("## 3. "), md.index("## 4. ")
    md = md[:a] + "\n".join(shortcut_markdown(report["shortcut"])) + "\n" + md[b:]
    failed = ", ".join(report["failed_checks"]) or "none"
    md = re.sub(r"Failed checks: [^\n]*\.\n", lambda m: f"Failed checks: {failed}.\n", md, count=1)
    mpath.write_text(md, encoding="utf-8")
    print(json.dumps({"failed_checks": report["failed_checks"], "shortcut_ok": report["shortcut"]["ok"],
                      "rule_failures": report["shortcut"]["n_rule_failures"],
                      "chain_trace_failures": report["shortcut"]["n_chain_trace_failures"]}, indent=1))
    return 1 if report["failed_checks"] else 0


def write_markdown(path: Path, rep: dict, dist: dict, vc: dict) -> None:
    ok = lambda b: "PASS" if b else "FAIL"
    L = ["# Dataset validation report (Phase H)", "",
         "Generated by `data/validate.py` (docs/DATASET_SPEC.md section 8). Failed checks: "
         + (", ".join(rep["failed_checks"]) or "none") + ".", ""]
    lk = rep["leakage"]
    L += ["## 1. Leakage — " + ok(lk["ok"]), ""]
    L += [f"- {n}: test stations {v['test_stations']}, also in calib: {v['test_stations_in_calib'] or 'none'}, "
          f"observations {v['n_obs']}" for n, v in lk["info"].items() if n != "detect_files_scanned"]
    L += [f"- detect/: {lk['info']['detect_files_scanned']} files scanned, no mention of labels" if lk["ok"] else "",
          *[f"- problem: {p}" for p in lk["problems"]], ""]
    L += shortcut_markdown(rep["shortcut"])
    L += ["## 4. Single-feature window AUC per SNR bin (test split)", "",
          "Positives: interference-attacked windows in the SNR bin (snr_db = SIR, bin centre ± 2.5 dB); negatives: "
          "test windows with no attack overlap. Feature direction fixed on train.", ""]
    sf_df = pd.DataFrame(rep["single_feature_auc"])
    bcols = sorted([c for c in sf_df.columns if re.fullmatch(r"[+-]\d+", c)], key=int)
    L += [md_table(sf_df[["norad", "feature", "direction"] + bcols].fillna("")), ""]
    ncols = [f"n{c}" for c in bcols]
    L += ["Positive windows per bin: " + "; ".join(f"{int(r['norad'])}: " + ", ".join(f"{c} {int(r[n])}" for c, n in zip(bcols, ncols) if n in r and r[n] == r[n])
                                                   for r in rep["single_feature_auc"][::len(vc['single_features'])]), ""]
    L += ["## 6. Reproducibility — " + ok(all(r["ok"] for r in rep["reproducibility"])), ""]
    L += [f"- obs {r['obs_id']}: rebuilt twice, byte-identical to Phase G: {r['byte_identical']}; clean variant = "
          f"Phase D tables: {r['clean_equals_phase_d']}" for r in rep["reproducibility"]]
    L += ["", "## 7. Recipe round-trip — " + ok(all(r["ok"] for r in rep["roundtrip"])), ""]
    L += [f"- obs {r['obs_id']} {r['variant_id']}: WAV ({r['wav_subtype']}) read back equal: {r['audio_equal']}; "
          f"rows identical: {r['rows_identical']}" for r in rep["roundtrip"]]
    lc = rep["label_consistency"]
    L += ["", f"## 2. Label consistency — {ok(lc['ok'])} ({lc['n_variants']} variants, {lc['n_problems']} problems)", ""]
    L += [f"- {p}" for p in lc["problems"]]
    L += ["", "## 5. Distribution", "", "### Observations per satellite and split", "", md_table(dist["obs_by_split_satellite"]),
          "### Variants per satellite, split and group", "", md_table(dist["variants_by_split_satellite_group"]),
          "### Stations", "", md_table(dist["stations"]),
          "### Operations per type and subtype", "", md_table(dist["operations_by_type_subtype"]),
          f"Replay length L: {dist['replay_length']}.", "",
          "### SNR (SIR) histogram of interference operations (5 dB bins)", ""]
    L += [f"- {k}: {v}" for k, v in dist["snr_hist"].items()]
    L += ["", f"INR of interference variants (dB): {dist['inr_db']}.", "",
          "### Attacked fractions", "", md_table(dist["attacked_window_frac"]), "Attacked frame fraction:", "",
          md_table(dist["attacked_frame_frac"]), "### Frame labels per attack type and subtype", "",
          md_table(dist["frame_labels"].rename("n").to_frame()),
          "### Anomaly position in the pass (quarters of the frame list / of the duration)", ""]
    L += [f"- {k}: {v}" for k, v in dist["anomaly_position_quarters"].items()]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
