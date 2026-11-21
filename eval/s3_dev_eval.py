"""S3 dev evaluation: interference v3 (station templates) vs v2, LOSO on train + calib (docs/GATES_PHASE2_S3.md).

The gate addendum and docs/S3_PLAN.md are signed (models/gates_p2_s3/lock.json); this script checks that lock first.
The held-out set is never read.

Stages (each resumable; --stage all runs them in order):
  recipes  dev SIR grid, seed tag "s3-dev": per train + calib clean recording, one narrowband interferer at SIR
           0 / 10 / 20 / 25 / 30 dB, data/make_recipes.plan_interference with fixed snr_db, seed
           recipe_seed(obs_id, "s3-dev/snrgrid_<+dd>") -> data/recipes_s3dev/<obs_id>/s3dev_snrgrid_<+dd>.json
  score    per recording (Phase 1 recipes clean / benign / interference / combo / replay / sequence + the SIR grid):
           apply the recipe to the clean pass, common chain, window PSD (cached float16 in data/cache/s3_spectra/rec),
           template score T_w with the pass's own chronological enrollment (k <= 3, detect/interference_v3.py),
           interference v2 raw score from the station-out-of-fold v2 model (the 6 GroupKFold folds of decision 92,
           retrained once each, checked against eval/out/interference_v2/oof_scores.npy, cached in out/s3_dev/),
           line screen (run.py settings) on whole-pass interference recordings. One JSON per recording in
           out/s3_dev/rec/. Clean pass: frames from data/tables/frames.csv (local demoddata is incomplete, P2-11),
           audio from data/raw or data/raw_enroll, SHA-256 checked against data/raw/manifest.csv; the clean variant and
           interference_00 of every pass are checked against the stored Phase 1 window features.
  report   LOSO: tau_T of held-out station s set on the other stations' scored not-attacked windows (<= 1 % pooled and
           per satellite, v2 rule); gate (a), (b), (c), detection-vs-SIR curve, by k and pooled, v2 next to v3;
           out/s3_dev/report.{json,md}; final tau_T on all scored stations -> models/interference_v3/threshold.json.

--version v3.1 (docs/GATES_PHASE2_S3_V31.md, signed, models/gates_p2_s3_v31/lock.json; output out/s3_dev_v31/):
  score    no audio processing: per v3 record of out/s3_dev/rec/, the template score is recomputed from the cached
           spectra (data/cache/s3_spectra/rec, .../enroll) for v3.1 and the ungated ablations fix1_only / fix2_only
           (detect/interference_v31.py); v2 scores, labels, line screen are those of the v3 record. One JSON per
           recording in out/s3_dev_v31/rec/, with a check of v3 recomputed from the float16 cache vs the stored v3 T.
  report   same computation as v3 for each variant; gate on v3.1 only; v3 (out/s3_dev/report.json) next to it;
           out/s3_dev_v31/report.{json,md}; final tau_T -> models/interference_v31/threshold.json.

Example (1 worker):  python eval/s3_dev_eval.py --stage all
                     python eval/s3_dev_eval.py --version v3.1 --stage all
Smoke:               python eval/s3_dev_eval.py --stage score --only 15004025:clean,15004025:interference_00
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
from build_tables import FMT  # noqa: E402
from make_recipes import plan_interference  # noqa: E402
from attacks.apply import apply_recipe  # noqa: E402
from attacks.common import burst_intervals, clean_label, recipe_seed, window_labels  # noqa: E402
from attacks.interference import reference_powers  # noqa: E402
from build_tables import frame_rows  # noqa: E402
from detect import fusion as F  # noqa: E402
from detect import interference_v3 as V3  # noqa: E402
from detect import interference_v31 as V31  # noqa: E402
from detect import line_screen as LS  # noqa: E402
from detect.audio_front import common_chain, window_psds  # noqa: E402
from detect.counters import counter_codec  # noqa: E402
from detect.features import FEATURES, window_features  # noqa: E402
from detect.interference_det import derive, k_of_n  # noqa: E402
from eval.lock import require_lock  # noqa: E402

OUT = Path("out/s3_dev")
REC = OUT / "rec"
OUT31 = Path("out/s3_dev_v31")
REC31 = OUT31 / "rec"
SPEC = V3.SPECTRA_CACHE
RECIPES = Path("data/recipes_s3dev")
GROUPS = ("clean", "benign", "interference", "combo", "replay", "sequence")
SIR_GRID = [0, 10, 20, 25, 30]
N_FOLDS = 6
V2_TH = Path("models/interference_v2/threshold.json")
OOF = Path("eval/out/interference_v2/oof_scores.npy")


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime(FMT)


# ------------------------------------------------------------------------------------------- inputs
def dev_obs() -> pd.DataFrame:
    o = pd.read_csv("data/tables/observations.csv")
    o = o[o["split"].isin(["train", "calib"]) & (o["selected_audio"] == 1)]
    return o.set_index("obs_id").sort_index()


def split_flags() -> dict[int, dict]:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(i): f for s in sp["satellites"].values() for i, f in s["obs"].items()}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def audio_path(obs_id: int) -> Path:
    """Phase 1 audio of a dev pass: data/raw, else the identical file in data/raw_enroll (SHA-256 checked)."""
    m = pd.read_csv("data/raw/manifest.csv", usecols=["obs_id", "file", "sha256"])
    want = m[(m["obs_id"] == obs_id) & (m["file"] == "audio.ogg")]["sha256"].iloc[0]
    for d in (Path("data/raw"), Path("data/raw_enroll")):
        p = d / str(obs_id) / "audio.ogg"
        if p.exists() and sha256_file(p) == want:
            return p
    raise FileNotFoundError(f"no local audio of {obs_id} matching data/raw/manifest.csv")


_FRAMES = None


def clean_frames(obs_id: int) -> list[dict]:
    """data/build_tables.load_clean_frames output, rebuilt from the clean rows of data/tables/frames.csv."""
    global _FRAMES
    if _FRAMES is None:
        f = pd.read_csv("data/tables/frames.csv", dtype={"hex": str, "copy_delays_s": str})
        _FRAMES = {i: g for i, g in f[f["variant_id"] == "clean"].groupby("obs_id")}
    g = _FRAMES.get(obs_id)
    if g is None:
        return []
    out = []
    for r in g.itertuples():
        cd = [] if not isinstance(r.copy_delays_s, str) or not r.copy_delays_s else [float(v) for v in r.copy_delays_s.split(";")]
        out.append({"t": float(r.t_rel_s), "filename": r.filename, "hex": r.hex, "decoder_branches": int(r.decoder_branches),
                    "n_copies": int(r.n_copies), "copy_delays_s": cd, **clean_label()})
    return out


def load_clean_p2(obs_id: int, cfg: dict, obs: pd.Series, fl: dict) -> dict:
    """data/build_features.load_clean with the two local-data substitutions above."""
    from datetime import datetime, timezone
    qc, tb = {**cfg["qc"], **cfg["canonical"]}, cfg["tables"]
    t0 = datetime.strptime(obs["start_utc"], FMT).replace(tzinfo=timezone.utc)
    norad = int(obs["norad"])
    dec, enc = counter_codec(norad, qc)
    frames = clean_frames(obs_id)
    reliable = bool(fl["timing_reliable"])
    c = {"obs_id": obs_id, "norad": norad, "start": t0, "frames": frames, "timing_reliable": reliable,
         "order_by_counter": norad == qc["funcube"]["norad"], "f_low": qc["psd"]["f_low_hz"],
         "f_edge": obs["f_edge_hz"], "f_drop": obs["f_drop_hz"], "band_hi": obs["feature_band_hi_hz"],
         "encode_counter": enc, "decode_counter": dec, "audio": None, "sr": None, "ref": None}
    x, sr = sf.read(str(audio_path(obs_id)), dtype="float32")
    rows = frame_rows(frames, t0, reliable, c["order_by_counter"], tb["burst_gap_s"], dec)
    p = {**qc["psd"], "chunk_windows": tb["features_chunk_windows"]}
    intervals = burst_intervals(rows, tb["burst_margin_s"]) if reliable else None
    c.update(audio=x, sr=sr, ref=reference_powers(x, sr, p, c["f_low"], c["f_edge"], c["band_hi"], intervals))
    return c


# ------------------------------------------------------------------------------------------- stage: recipes
def sir_recipes(cfg: dict, obs: pd.DataFrame) -> int:
    n = 0
    for i, o in obs.iterrows():
        for snr in SIR_GRID:
            vid = f"s3dev_snrgrid_{snr:+d}"
            p = RECIPES / str(i) / f"{vid}.json"
            seed = recipe_seed(int(i), f"s3-dev/snrgrid_{snr:+d}")
            op = plan_interference(np.random.default_rng(seed), float(o["duration_s"]), float(o["feature_band_hi_hz"]),
                                   cfg["attacks"], snr_db=float(snr))
            rec = {"obs_id": int(i), "variant_id": vid, "group": "s3dev_snrgrid", "split": o["split"], "seed": seed,
                   "seed_tag": "s3-dev", "norad": int(o["norad"]), "operations": [op]}
            txt = json.dumps(rec, indent=1, sort_keys=True) + "\n"
            if p.exists() and p.read_text(encoding="utf-8") == txt:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt, encoding="utf-8", newline="\n")
            n += 1
    return n


def recipes_of(obs_id: int) -> list[dict]:
    out = []
    for p in sorted((Path("data/recipes") / str(obs_id)).glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if r["group"] in GROUPS:
            out.append(r)
    for p in sorted((RECIPES / str(obs_id)).glob("*.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


# ------------------------------------------------------------------------------------------- interference v2 folds
_V2 = {}


def v2_data():
    if "df" not in _V2:
        from eval.interference_eval import load_split
        from eval.interference_v2 import stations
        df = pd.concat([load_split("train"), load_split("calib")], ignore_index=True)
        df["station"] = df["obs_id"].map(stations())
        _V2["df"] = df
    return _V2["df"]


def fold_of_station() -> dict[int, int]:
    p = OUT / "v2_folds.json"
    if p.exists():
        return {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    from sklearn.model_selection import GroupKFold
    df = v2_data()
    y = (df["role"] == "pos").to_numpy()
    out = {}
    for i, (_, b) in enumerate(GroupKFold(n_splits=N_FOLDS).split(df, y, df["station"])):
        for s in df.iloc[b]["station"].unique():
            out[int(s)] = i
    OUT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out


def fold_model(i: int, cfg: dict) -> dict:
    """Fold i of eval/interference_v2.py loso (v2 template + HGB), trained once and checked against the stored OOF."""
    if i in _V2:
        return _V2[i]
    p = OUT / f"v2_fold{i}.pkl"
    if p.exists():
        with open(p, "rb") as fh:
            _V2[i] = pickle.load(fh)
        return _V2[i]
    from eval.interference_eval import hgb, weights66
    from eval.interference_v2 import V2 as V2COLS, features, fit_template_v2
    t0 = time.perf_counter()
    with open("models/interference/model.pkl", "rb") as fh:
        params = pickle.load(fh)["params"]
    df = v2_data()
    fos = fold_of_station()
    held = df["station"].map(fos).to_numpy() == i
    tr = ~held
    use = df["role"].isin(["pos", "neg"]).to_numpy() & (df["win_idx"] % 2 == 0).to_numpy()
    y = (df["role"] == "pos").to_numpy()
    t = fit_template_v2(df[tr & (df["kind"] == "clean").to_numpy()])
    sel = df[tr & use]
    mdl = hgb(params, cfg["seed"]).fit(features(sel, t, V2COLS), y[tr & use], sample_weight=weights66(sel))
    s = mdl.predict_proba(features(df[held], t, V2COLS))[:, 1]
    chk = None
    if OOF.exists():
        ref = np.load(OOF)[1][held]
        chk = {"n": int(held.sum()), "max_abs_diff": float(np.max(np.abs(ref - s)))}
    b = {"fold": i, "template": t, "model": mdl, "features": V2COLS, "oof_check": chk,
         "seconds": round(time.perf_counter() - t0, 1)}
    with open(p, "wb") as fh:
        pickle.dump(b, fh)
    print(json.dumps({"v2_fold": i, "oof_check": chk, "seconds": b["seconds"]}), file=sys.stderr, flush=True)
    _V2[i] = b
    return b


def v2_scores(win: pd.DataFrame, norad: int, station: int, cfg: dict) -> np.ndarray:
    b = fold_model(fold_of_station()[station], cfg)
    return b["model"].predict_proba(derive(win, norad, b["template"])[b["features"]].to_numpy(np.float32))[:, 1]


# ------------------------------------------------------------------------------------------- stage: score
def roles(lab: pd.DataFrame, group: str) -> np.ndarray:
    """eval/interference_eval.load_split roles: 1 pos, 0 neg, -1 excluded."""
    at = lab["attack_type"].fillna("").to_numpy()
    ia = lab["is_attack"].to_numpy().astype(int)
    ov = lab["overlap_frac"].to_numpy().astype(float)
    pos = (at == "interference") & (ia == 1)
    neg = ((ia == 0) & (ov == 0)) | ((group == "replay") & (at == "replay") & (ia == 1))
    return np.select([pos, neg], [1, 0], -1)


def enrollment_of(obs_id: int, o: pd.Series, archive: list[dict], params: dict) -> list[dict]:
    return V3.select_enrollment(o["start_utc"], int(o["station_id"]), int(o["norad"]), archive, params["k_max"])


def phase1_tables(obs_id: int, vid: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = Path("data/features") / str(obs_id)
    w = pd.read_csv(d / "windows.csv")
    w = w[w["variant_id"] == vid].sort_values("win_idx").reset_index(drop=True)
    lab = pd.read_csv(d / "labels.csv")
    lab = lab[(lab["unit"] == "window") & (lab["variant_id"] == vid)].sort_values("index").reset_index(drop=True)
    return w, lab


def whole_pass(rec: dict, duration: float) -> bool:
    ops = [op for op in rec["operations"] if op["type"] == "interference"]
    return bool(ops) and all(len(op["segments"]) == 1 and op["segments"][0][0] <= 1e-6
                             and op["segments"][0][1] >= duration - 1e-6 for op in ops)


def score_recording(rec: dict, clean: dict, o: pd.Series, tmpl: dict | None, enr: list[dict], cfg: dict,
                    params: dict, fcfg: dict, verify: bool) -> dict:
    t0 = time.perf_counter()
    oid, vid, group = int(rec["obs_id"]), rec["variant_id"], rec["group"]
    sr, psd = clean["sr"], cfg["qc"]["psd"]
    res = apply_recipe(rec, clean, None)
    y = common_chain(res["audio"], sr, cfg["tables"]["chain"])
    key = SPEC / "rec" / f"{oid}__{vid}.npz"
    if key.exists():
        z = np.load(key)
        freqs, t, db = z["freqs"], z["t"], z["db"].astype(np.float32)
    else:
        freqs, t, db = V3.psd_db_full(y, sr, psd, cfg["tables"]["features_chunk_windows"])
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_name(key.stem + ".part.npz")
        np.savez(tmp, freqs=freqs, t=t, db=db.astype(np.float16))
        tmp.replace(key)
    pchunk = {**psd, "chunk_windows": cfg["tables"]["features_chunk_windows"]}
    check = None
    if group == "s3dev_snrgrid":
        w = window_features(y, sr, pchunk, clean["f_low"], clean["band_hi"])
        win = pd.DataFrame({"win_idx": np.arange(len(w["t_start_s"])), **{k: w[k] for k in ["t_start_s"] + FEATURES}})
        lab = pd.DataFrame(window_labels(w["t_start_s"], psd["win_s"], res["events"], cfg["features"]["window_min_overlap_s"]))
    else:
        win, lab = phase1_tables(oid, vid)
        if verify:
            w = window_features(y, sr, pchunk, clean["f_low"], clean["band_hi"])
            n = min(len(win), len(w["t_start_s"]))
            check = {"n_windows": [int(len(win)), int(len(w["t_start_s"]))],
                     "max_abs_diff": {f: float(np.max(np.abs(win[f].to_numpy()[:n] - np.asarray(w[f])[:n])))
                                      for f in FEATURES}}
    n = min(len(win), len(db), len(lab))
    win, lab, db = win.iloc[:n].reset_index(drop=True), lab.iloc[:n].reset_index(drop=True), db[:n]
    s2 = v2_scores(win, int(o["norad"]), int(o["station_id"]), cfg)
    T = None
    if tmpl is not None:
        T, _ = V3.score(db, freqs, tmpl, params)
    wp = whole_pass(rec, float(o["duration_s"]))
    ls = None
    if wp:
        fr, pxx = window_psds(res["audio"], sr, psd)
        f_edge, f_drop = LS.estimate_f_edge(fr, 10 * np.log10(pxx + 1e-30), psd)
        del pxx
        hi = f_drop - cfg["tables"]["feature_band_top_margin_hz"]
        lines = LS.screen_audio(y, sr, (float(psd["f_low_hz"]), hi), cfg["qc"],
                                fcfg["line_screen"]["downlink"].get(str(int(o["norad"])), []))
        allowed = [ln for ln in lines["lines"] if F.line_allowed(ln, f_edge, hi, fcfg["line_screen"])]
        ls = {"detected": bool(allowed), "n_lines": len(lines["lines"]), "n_allowed": len(allowed),
              "f_edge_hz": round(float(f_edge), 1)}
    ops = [op for op in rec["operations"] if op["type"] == "interference"]
    return {"obs_id": oid, "variant_id": vid, "group": group, "station": int(o["station_id"]), "norad": int(o["norad"]),
            "split": o["split"], "k": len(enr), "enrollment": [a["obs_id"] for a in enr],
            "template_band_hz": tmpl["band_hz"] if tmpl else None, "whole_pass": wp,
            "snr_db": float(ops[0]["snr_db"]) if ops else None, "shape": ops[0]["shape"] if ops else None,
            "n_windows": int(n), "role": roles(lab, group).tolist(),
            "is_attack": lab["is_attack"].astype(int).tolist(),
            "v2_score": np.round(s2, 7).tolist(), "T": None if T is None else np.round(T, 4).tolist(),
            "line_screen": ls, "feature_check": check, "seconds": round(time.perf_counter() - t0, 1),
            "created_utc": utc_now()}


def stage_score(cfg: dict, only: set[tuple[int, str]] | None, limit: int | None) -> dict:
    params = V3.load_params()
    fcfg = F.load(F.DEFAULT_FUSION)
    obs, flags = dev_obs(), split_flags()
    archive = {a["obs_id"]: a for a in V3.build_archive()}
    archive = list(archive.values())
    enr_json = json.loads(Path("data/raw_enroll/enrollment.json").read_text(encoding="utf-8"))
    expect = {r["obs_id"]: [e["obs_id"] for e in r["enroll"]] for g in enr_json["groups"] for r in g["passes"]}
    REC.mkdir(parents=True, exist_ok=True)
    done = mismatch = 0
    for i, o in obs.iterrows():
        recs = [r for r in recipes_of(int(i)) if only is None or (int(i), r["variant_id"]) in only]
        todo = [r for r in recs if not (REC / f"{i}__{r['variant_id']}.json").exists()]
        if not todo:
            continue
        enr = enrollment_of(int(i), o, archive, params)
        if [a["obs_id"] for a in enr] != expect.get(int(i)):
            mismatch += 1
            print(json.dumps({"enrollment_mismatch": int(i), "v3": [a["obs_id"] for a in enr],
                              "enrollment_json": expect.get(int(i))}), file=sys.stderr)
        tmpl = None
        if len(enr) >= params["k_min"]:
            tmpl = V3.fit_template([V3.pass_spectra(Path(a["audio"]), cfg, SPEC / "enroll") for a in enr], params,
                                   float(cfg["qc"]["psd"]["f_low_hz"]))
        clean = load_clean_p2(int(i), cfg, o, flags[int(i)])
        for r in todo:
            out = score_recording(r, clean, o, tmpl, enr, cfg, params, fcfg,
                                  verify=r["variant_id"] in ("clean", "interference_00"))
            p = REC / f"{i}__{r['variant_id']}.json"
            tmp = p.with_name(p.name + ".part")
            tmp.write_text(json.dumps(out) + "\n", encoding="utf-8")
            tmp.replace(p)
            done += 1
            chk = out["feature_check"]
            print(json.dumps({"rec": p.name, "k": out["k"], "seconds": out["seconds"], "whole_pass": out["whole_pass"],
                              "max_T": None if out["T"] is None else max(out["T"]),
                              "feature_check_max": None if chk is None else max(chk["max_abs_diff"].values())}),
                  file=sys.stderr, flush=True)
            if limit is not None and done >= limit:
                return {"scored": done, "enrollment_mismatch": mismatch}
        del clean
    return {"scored": done, "enrollment_mismatch": mismatch}


# ------------------------------------------------------------------------------------------- stage: report
def tau_for_fpr(neg: np.ndarray, groups: np.ndarray, alpha: float) -> float:
    """Same result as eval/metrics.threshold_for_fpr (smallest candidate with mean(neg >= tau) <= alpha pooled and
    per group, candidates = the negative scores and just above their max), in O(n log n)."""
    neg = np.asarray(neg, dtype=float)
    cands = np.unique(neg)
    x = -np.inf
    for g in [None] + list(np.unique(groups)):
        v = np.sort(neg if g is None else neg[groups == g])[::-1]
        allowed = int(np.floor(alpha * len(v) + 1e-9))
        if allowed < len(v):
            x = max(x, v[allowed])
    if not np.isfinite(x):
        return float(cands[0])
    j = np.searchsorted(cands, x, side="right")
    return float(cands[j]) if j < len(cands) else float(np.nextafter(cands[-1], np.inf))


def cp(x: int, n: int) -> list:
    if n == 0:
        return [None, None]
    lo = 0.0 if x == 0 else float(beta.ppf(0.025, x, n - x + 1))
    hi = 1.0 if x == n else float(beta.ppf(0.975, x + 1, n - x))
    return [round(lo, 4), round(hi, 4)]


def rate(flags: list[bool]) -> dict:
    x, n = int(sum(flags)), len(flags)
    return {"rate": round(x / n, 4) if n else None, "x": x, "n": n, "ci95": cp(x, n)}


def load_v3_records() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(REC.glob("*.json"))]


def stage_report(cfg: dict, recs: list[dict] | None = None, params: dict | None = None, out: Path | None = OUT,
                 threshold_path: Path | None = V3.THRESHOLD, lock: str = "models/gates_p2_s3/lock.json",
                 version: str = "v3") -> dict:
    """LOSO report of one template version (v3 by default; recs carrying their own T for v3.1 / ablations).
    out None / threshold_path None: that output is not written."""
    params = params or V3.load_params()
    v2th = json.loads(V2_TH.read_text(encoding="utf-8"))
    alpha = params["target_fpr"]
    recs = load_v3_records() if recs is None else recs
    for r in recs:
        r["role"], r["v2_score"] = np.asarray(r["role"]), np.asarray(r["v2_score"])
        r["T"] = None if r["T"] is None else np.asarray(r["T"])
        r["v2_alert"] = k_of_n(r["v2_score"] >= v2th["tau_smoothed"], v2th["k"], v2th["n"])
    scored = [r for r in recs if r["T"] is not None]
    thr_pool = [r for r in scored if r["group"] != "s3dev_snrgrid"]
    taus = {}
    for s in sorted({r["station"] for r in scored}):
        other = [r for r in thr_pool if r["station"] != s]
        neg = np.concatenate([r["T"][r["role"] == 0] for r in other])
        grp = np.concatenate([np.full(int((r["role"] == 0).sum()), r["norad"]) for r in other])
        taus[s] = tau_for_fpr(neg, grp, alpha)
    for r in recs:
        if r["T"] is None:
            r["v3_alert"], r["t_alert"] = V3.decide(r["v2_alert"], None, np.inf, params["k"], params["n"])
        else:
            r["v3_alert"], r["t_alert"] = V3.decide(r["v2_alert"], r["T"], taus[r["station"]], params["k"], params["n"])

    def fa(rs, key):
        per = {}
        for r in rs:
            if r["group"] in ("clean", "benign"):
                m = r["role"] == 0
                a = per.setdefault(r["station"], [0, 0])
                a[0] += int(r[key][m].sum())
                a[1] += int(m.sum())
        st = {str(k): round(v[0] / v[1], 5) for k, v in per.items() if v[1]}
        tot = [sum(v[0] for v in per.values()), sum(v[1] for v in per.values())]
        worst = max(st.items(), key=lambda kv: kv[1]) if st else (None, None)
        return {"worst_station": worst[0], "worst_fa": worst[1], "pooled_fa": round(tot[0] / tot[1], 5) if tot[1] else None,
                "n_windows": tot[1], "per_station": st}

    def det(rs, key):
        return rate([bool(r[key].any()) for r in rs])

    def block(rs):
        wp = [r for r in rs if r["group"] == "interference" and r["whole_pass"]]
        grid = {snr: [r for r in rs if r["group"] == "s3dev_snrgrid" and round(r["snr_db"]) == snr] for snr in SIR_GRID}
        weak = [r for snr in (20, 25, 30) for r in grid[snr]]
        rnd_weak = [r for r in rs if r["group"] == "interference" and 20 <= r["snr_db"] < 30]
        return {"n_recordings": len(rs), "n_passes": len({r["obs_id"] for r in rs}),
                "a_window_fa": {"v3": fa(rs, "v3_alert"), "v2": fa(rs, "v2_alert"), "template_only": fa(rs, "t_alert")},
                "b_whole_pass": {"v3": det(wp, "v3_alert"), "v2": det(wp, "v2_alert"), "template_only": det(wp, "t_alert"),
                                 "line_screen": rate([bool(r["line_screen"] and r["line_screen"]["detected"]) for r in wp])},
                "c_sir_20_30": {"v3": det(weak, "v3_alert"), "v2": det(weak, "v2_alert"),
                                "v3_alert_on_attacked_window": rate([bool((r["v3_alert"] & (np.asarray(r["is_attack"]) == 1)).any())
                                                                     for r in weak])},
                "curve_sir_grid": {str(snr): {"v3": det(g, "v3_alert"), "v2": det(g, "v2_alert")} for snr, g in grid.items()},
                "random_sir_20_30_interference": {"v3": det(rnd_weak, "v3_alert"), "v2": det(rnd_weak, "v2_alert")}}

    rep = {"created_utc": utc_now(), "version": version, "lock": lock, "n_recordings": len(recs),
           "tau_t_loso": {str(k): round(v, 4) for k, v in taus.items()}, "v2_threshold": v2th,
           "pooled_k_ge_1": block(scored),
           "by_k": {str(k): block([r for r in scored if r["k"] == k]) for k in (1, 2, 3)},
           "by_satellite": {str(n): block([r for r in scored if r["norad"] == n]) for n in sorted({r["norad"] for r in scored})},
           "fallback_k0_v2_only": block([r for r in recs if r["T"] is None])}
    pk = rep["pooled_k_ge_1"]
    rep["gate"] = {"a_worst_station_fa_le_0.02": pk["a_window_fa"]["v3"]["worst_fa"] is not None
                   and pk["a_window_fa"]["v3"]["worst_fa"] <= 0.02,
                   "b_v3_whole_pass_ge_0.55": pk["b_whole_pass"]["v3"]["rate"] is not None
                   and pk["b_whole_pass"]["v3"]["rate"] >= 0.55,
                   "c_v3_sir_20_30_gt_0.04": pk["c_sir_20_30"]["v3"]["rate"] is not None
                   and pk["c_sir_20_30"]["v3"]["rate"] > 0.04}
    rep["gate"]["pass"] = all(rep["gate"].values())
    checks = [r["feature_check"] for r in recs if r.get("feature_check")]
    rep["feature_reproduction"] = {"n_checked": len(checks),
                                   "max_abs_diff": max((max(c["max_abs_diff"].values()) for c in checks), default=None)}
    rep["v2_fold_oof_check"] = {}
    for p in sorted(OUT.glob("v2_fold*.pkl")):
        with open(p, "rb") as fh:
            rep["v2_fold_oof_check"][p.stem] = pickle.load(fh)["oof_check"]
    neg = np.concatenate([r["T"][r["role"] == 0] for r in [r for r in scored if r["group"] != "s3dev_snrgrid"]])
    grp = np.concatenate([np.full(int((r["role"] == 0).sum()), r["norad"]) for r in scored if r["group"] != "s3dev_snrgrid"])
    final = {"tau_t": tau_for_fpr(neg, grp, alpha), "target_fpr": alpha, "k": params["k"], "n": params["n"],
             "rule": "tau_T on all scored dev stations' not-attacked windows, <= target FPR pooled and per satellite",
             "source": "eval/s3_dev_eval.py report", "created_utc": rep["created_utc"]}
    rep["final_tau_t"] = final["tau_t"]
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
        (out / "report.md").write_text(markdown(rep), encoding="utf-8")
    if threshold_path is not None:
        if version != "v3":
            final["source"] = f"eval/s3_dev_eval.py report (version {version})"
        Path(threshold_path).write_text(json.dumps(final, indent=1) + "\n", encoding="utf-8")
    return rep


def markdown(rep: dict) -> str:
    def r3(d):
        return "-" if d["rate"] is None else f"{d['rate']:.3f} ({d['x']}/{d['n']}) [{d['ci95'][0]}, {d['ci95'][1]}]"
    L = [f"# S3 interference {rep.get('version', 'v3')}, dev LOSO ({rep['created_utc']})", "",
         f"Gate (signed, {rep['lock']}): {rep['gate']}", "",
         f"Columns 'v3' = the template version of this report ({rep.get('version', 'v3')}).", "",
         "| set | (a) worst-station window FA v3 / v2 | (b) whole pass v3 / v2 / line screen | (c) SIR 20-30 v3 / v2 |",
         "| --- | --- | --- | --- |"]
    for name, b in [("pooled k>=1", rep["pooled_k_ge_1"])] + [(f"k={k}", v) for k, v in rep["by_k"].items()] + \
                   [(f"sat {k}", v) for k, v in rep["by_satellite"].items()] + [("fallback k=0", rep["fallback_k0_v2_only"])]:
        a = b["a_window_fa"]
        L.append(f"| {name} (n={b['n_recordings']}) | {a['v3']['worst_fa']} ({a['v3']['worst_station']}) / "
                 f"{a['v2']['worst_fa']} ({a['v2']['worst_station']}) | {r3(b['b_whole_pass']['v3'])} / "
                 f"{r3(b['b_whole_pass']['v2'])} / {r3(b['b_whole_pass']['line_screen'])} | "
                 f"{r3(b['c_sir_20_30']['v3'])} / {r3(b['c_sir_20_30']['v2'])} |")
    L += ["", "Detection vs SIR (dev SIR grid, pooled k>=1): " + "; ".join(
        f"{s} dB v3 {r3(v['v3'])}, v2 {r3(v['v2'])}" for s, v in rep["pooled_k_ge_1"]["curve_sir_grid"].items()), ""]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------- v3.1 (cache only)
def stage_score_v31(cfg: dict, only: set[tuple[int, str]] | None, limit: int | None) -> dict:
    """Recompute T for v3.1 and its ablations from the cached spectra of every scored v3 record (no audio)."""
    psd = cfg["qc"]["psd"]
    archive = {a["obs_id"]: a for a in V3.build_archive()}
    p3 = V3.load_params()
    pv = {v: V31.load_params(variant=v) for v in V31.ABLATIONS}
    REC31.mkdir(parents=True, exist_ok=True)
    by_obs: dict[int, list[Path]] = {}
    for b in sorted(REC.glob("*.json")):
        oid, vid = b.stem.split("__", 1)
        if only is None or (int(oid), vid) in only:
            by_obs.setdefault(int(oid), []).append(b)
    done, worst_check = 0, 0.0
    for oid, paths in by_obs.items():
        todo = [b for b in paths if not (REC31 / b.name).exists()]
        if not todo:
            continue
        enr = json.loads(todo[0].read_text(encoding="utf-8"))["enrollment"]
        tm, tm3 = {}, None
        if enr:
            sp = [V3.pass_spectra(Path(archive[e]["audio"]), cfg, SPEC / "enroll") for e in enr]
            tm = {v: V31.fit_template(sp, p, psd) for v, p in pv.items()}
            tm3 = V3.fit_template(sp, p3, float(psd["f_low_hz"]))
            del sp
        for b in todo:
            t0 = time.perf_counter()
            r = json.loads(b.read_text(encoding="utf-8"))
            if r["enrollment"] != enr:
                raise SystemExit(f"enrollment differs between variants of {oid}: {b.name}")
            out = {"obs_id": r["obs_id"], "variant_id": r["variant_id"], "k": r["k"], "enrollment": enr,
                   "T": {v: None for v in pv}, "band_hz": {}, "n_excluded_bins": {}, "v3_recompute_max_abs_diff": None}
            if enr:
                z = np.load(SPEC / "rec" / f"{r['obs_id']}__{r['variant_id']}.npz")
                n = r["n_windows"]
                freqs, db = z["freqs"], z["db"][:n].astype(np.float32)
                for v, p in pv.items():
                    t, _ = V31.score(db, freqs, tm[v], p)
                    out["T"][v] = np.round(t, 4).tolist()
                    out["band_hz"][v] = tm[v]["band_hz"]
                    out["n_excluded_bins"][v] = int((~tm[v]["keep"]).sum())
                t3, _ = V3.score(db, freqs, tm3, p3)
                d = float(np.max(np.abs(t3 - np.asarray(r["T"])))) if len(t3) == len(r["T"]) else float("nan")
                out["v3_recompute_max_abs_diff"] = round(d, 4)
                if d == d:
                    worst_check = max(worst_check, d)
                del z, db
            out.update(seconds=round(time.perf_counter() - t0, 2), created_utc=utc_now())
            q = REC31 / b.name
            tmp = q.with_name(q.name + ".part")
            tmp.write_text(json.dumps(out) + "\n", encoding="utf-8")
            tmp.replace(q)
            done += 1
            print(json.dumps({"rec": q.name, "k": out["k"], "seconds": out["seconds"],
                              "max_T": {v: None if t is None else max(t) for v, t in out["T"].items()},
                              "band_hi": {v: round(x[1]) for v, x in out["band_hz"].items()},
                              "n_excluded_bins": out["n_excluded_bins"],
                              "v3_recompute_max_abs_diff": out["v3_recompute_max_abs_diff"]}), file=sys.stderr, flush=True)
            if limit is not None and done >= limit:
                return {"rescored": done, "v3_recompute_max_abs_diff": round(worst_check, 4)}
    return {"rescored": done, "v3_recompute_max_abs_diff": round(worst_check, 4)}


def stage_report_v31(cfg: dict) -> dict:
    base = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(REC.glob("*.json"))}
    new = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(REC31.glob("*.json"))}
    missing = sorted(set(base) - set(new))
    if missing:
        raise SystemExit(f"{len(missing)} v3 records not rescored yet (--version v3.1 --stage score): {missing[:3]}")
    reps = {}
    for v in V31.ABLATIONS:
        recs = [{**base[k], "T": new[k]["T"][v]} for k in sorted(base)]
        reps[v] = stage_report(cfg, recs, V31.load_params(variant=v), out=None,
                               threshold_path=V31.THRESHOLD if v == "v3.1" else None,
                               lock="models/gates_p2_s3_v31/lock.json", version=v)
    rep = reps["v3.1"]
    rep["ablations_not_gated"] = {v: {**{kk: reps[v][kk] for kk in ("tau_t_loso", "pooled_k_ge_1", "by_k", "final_tau_t")},
                                      "criteria_values_not_gated": reps[v]["gate"]} for v in ("fix1_only", "fix2_only")}
    v3p = OUT / "report.json"
    rep["v3_report_pooled_k_ge_1"] = json.loads(v3p.read_text(encoding="utf-8"))["pooled_k_ge_1"] if v3p.exists() else None
    chk = [x["v3_recompute_max_abs_diff"] for x in new.values() if x["v3_recompute_max_abs_diff"] is not None]
    rep["v3_recompute_from_float16_cache_max_abs_diff"] = max(chk) if chk else None
    OUT31.mkdir(parents=True, exist_ok=True)
    (OUT31 / "report.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    md = markdown(rep)
    rows = ([("v3 (P2-22)", rep["v3_report_pooled_k_ge_1"])] if rep["v3_report_pooled_k_ge_1"] else []) + \
        [(f"{v} (not gated)", rep["ablations_not_gated"][v]["pooled_k_ge_1"]) for v in ("fix1_only", "fix2_only")]
    for name, b in rows:
        a, bw, c = b["a_window_fa"]["v3"], b["b_whole_pass"]["v3"], b["c_sir_20_30"]["v3"]
        md += (f"\n{name}, pooled k>=1: (a) worst {a['worst_fa']} ({a['worst_station']}), pooled {a['pooled_fa']}; "
               f"(b) {bw['rate']} ({bw['x']}/{bw['n']}); (c) {c['rate']} ({c['x']}/{c['n']}); curve "
               + ", ".join(f"{s} dB {g['v3']['x']}/{g['v3']['n']}" for s, g in b["curve_sir_grid"].items()) + "\n")
    md += ("\nv3 recomputed from the float16 cache vs stored v3 T: max abs diff "
           f"{rep['v3_recompute_from_float16_cache_max_abs_diff']}\n")
    (OUT31 / "report.md").write_text(md, encoding="utf-8")
    return rep


# ------------------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["recipes", "score", "report", "all"], required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--only", default=None, help="comma-separated obs_id:variant_id (smoke tests)")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many new recordings")
    ap.add_argument("--version", choices=["v3", "v3.1"], default="v3", help="template version (v3.1: cache only)")
    a = ap.parse_args(argv)
    require_lock("gates_p2_s3")
    if a.version == "v3.1":
        require_lock("gates_p2_s3_v31")
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    only = None if not a.only else {(int(x.split(":")[0]), x.split(":")[1]) for x in a.only.split(",")}
    out = {}
    if a.version == "v3.1":
        if a.stage in ("score", "all"):
            out["score"] = stage_score_v31(cfg, only, a.limit)
        if a.stage in ("report", "all"):
            out["gate"] = stage_report_v31(cfg)["gate"]
        print(json.dumps(out, indent=1, default=str))
        return 0
    if a.stage in ("recipes", "all"):
        out["recipes_written"] = sir_recipes(cfg, dev_obs())
    if a.stage in ("score", "all"):
        out["score"] = stage_score(cfg, only, a.limit)
    if a.stage in ("report", "all"):
        rep = stage_report(cfg)
        out["gate"] = rep["gate"]
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
