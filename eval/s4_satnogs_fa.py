"""S4 gate (b): jam_v1 false alarms on SatNOGS FUNCUBE-1 train + calib passes (docs/GATES_PHASE2_S4.md A, C, E(b)).

Refuses to run unless the signed gate addendum verifies (models/gates_p2_s4/lock.json). Train + calib only: the
test split and data/holdout_p2/ are never read.
  power   per recording (clean + Phase 1 benign variants of every FUNCUBE-1 train / calib pass): in-band audio power
          per Phase 1 window PSD (1 s windows, 0.5 s hop) on [f_low, f_edge], from the S3 spectra cache
          (data/cache/s3_spectra/rec, float16, reduced precision) when present, otherwise regenerated from the recipe
          with the S3 pipeline (apply_recipe -> common chain -> window PSD) into data/cache/s4_spectra (float32).
          Expected frames: FUNCUBE-1 counters of the clean frames (benign variants keep the frames; the chain does
          not re-decode), first to last decoded counter. One JSON per recording in out/s4_satnogs/rec/.
  report  LOSO by station: P0 of the held-out station = fit_p0 on the lossy windows (L >= L0) of every other
          station's recordings; FA per condition (clean, benign) pooled, CP one-sided 95 % upper bound, per-pass
          FA, worst station, benign broken down by operation type -> out/s4_satnogs/report.{json,md}.
The noise_rise FA is OPTIMISTIC: frames are not re-decoded, so added noise never adds loss here.
  python eval/s4_satnogs_fa.py --stage all            # power (resumable) then report
  python eval/s4_satnogs_fa.py --smoke 3              # 3 passes, power only, timing + cache precision check
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import jam_v1  # noqa: E402

NORAD = 39444
S3_CACHE = Path("data/cache/s3_spectra/rec")
S4_CACHE = Path("data/cache/s4_spectra/rec")
OUT = Path("out/s4_satnogs")
WIN_S = 1.0                                      # config.yaml qc.psd.win_s (Phase 1 window PSD)
F_LOW = 100.0                                    # config.yaml qc.psd.f_low_hz
REF_BLOCK_S = 10.0


def require_gate() -> None:
    sys.path.insert(0, str(ROOT / "eval"))
    from lock import require_lock
    require_lock("gates_p2_s4")


# ------------------------------------------------------------------------------------------- inputs
def dev_passes() -> pd.DataFrame:
    o = pd.read_csv("data/tables/observations.csv")
    o = o[(o["norad"] == NORAD) & o["split"].isin(["train", "calib"])]
    return o.set_index("obs_id").sort_index()


def recordings(obs_id: int) -> list[dict]:
    """clean + benign recipes (data/recipes, Phase 1, frozen; read only)."""
    out = []
    for p in sorted((Path("data/recipes") / str(obs_id)).glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if r["variant_id"] == "clean" or r["group"] == "benign":
            out.append(r)
    return out


def expected_frames(fr: pd.DataFrame) -> tuple[list[dict] | None, str]:
    """Expected frames from the decoded counters (gate A): spans from the interpolated counter-time line."""
    d = fr[(fr["valid"] == 1) & fr["counter"].notna()]
    if d.empty:
        return None, "no decoded counter"
    g = d.groupby("counter")["t_rel_s"].min().sort_index()
    cs, ts = g.index.to_numpy(np.int64), g.to_numpy(float)
    if len(cs) < 2:
        return None, "< 2 decoded counters"
    if np.any(np.diff(ts) < 0):                 # counters not increasing in time; ties allowed (1 s timestamps)
        return None, "non-increasing counter-time line"
    allc = np.arange(cs[0], cs[-1] + 1)
    t = np.interp(allc, cs, ts)
    mid = (t[1:] + t[:-1]) / 2
    t0 = np.concatenate([[t[0] - (t[1] - t[0]) / 2], mid])
    t1 = np.concatenate([mid, [t[-1] + (t[-1] - t[-2]) / 2]])
    have = set(cs.tolist())
    return [{"counter": int(c), "t": float(tc), "t0": float(a), "t1": float(b), "lost": int(c) not in have}
            for c, tc, a, b in zip(allc, t, t0, t1)], "ok"


def spectra(obs_id: int, rec: dict, o: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    key = f"{obs_id}__{rec['variant_id']}.npz"
    for d, src in ((S3_CACHE, "s3_cache_float16"), (S4_CACHE, "regenerated_float32")):
        if (d / key).exists():
            z = np.load(d / key)
            return z["freqs"], z["t"], z["db"].astype(np.float64), src
    return (*regenerate(obs_id, rec, S4_CACHE / key), "regenerated_float32")


def regenerate(obs_id: int, rec: dict, key: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """S3 pipeline (eval/s3_dev_eval.score_recording): recipe on the clean pass, common chain, window PSD."""
    import yaml
    sys.path.insert(0, str(ROOT / "data"))
    import eval.s3_dev_eval as S3
    from attacks.apply import apply_recipe
    from detect.audio_front import common_chain
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    obs = S3.dev_obs()
    if obs_id not in obs.index:
        raise FileNotFoundError(f"{obs_id}: not a selected-audio dev pass")
    clean = S3.load_clean_p2(obs_id, cfg, obs.loc[obs_id], S3.split_flags()[obs_id])
    res = apply_recipe(rec, clean, None)
    y = common_chain(res["audio"], clean["sr"], cfg["tables"]["chain"])
    freqs, t, db = S3.V3.psd_db_full(y, clean["sr"], cfg["qc"]["psd"], cfg["tables"]["features_chunk_windows"])
    key.parent.mkdir(parents=True, exist_ok=True)
    tmp = key.with_name(key.stem + ".part.npz")
    np.savez(tmp, freqs=freqs, t=t, db=db.astype(np.float32))
    tmp.replace(key)
    return freqs, t, db.astype(np.float64)


def band_power(freqs: np.ndarray, db: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Linear band power per window: sum of the PSD over [lo, hi] times the bin width (detect/features.py rule)."""
    m = (freqs >= lo) & (freqs <= hi)
    return np.sum(10.0 ** (db[:, m] / 10.0), axis=1) * float(freqs[1] - freqs[0])


def ref_power(t: np.ndarray, p: np.ndarray) -> float:
    """Median over 10 s blocks of the mean in-band power (gate A, SatNOGS reference)."""
    b = np.floor(t / REF_BLOCK_S).astype(int)
    return float(np.median([p[b == k].mean() for k in np.unique(b)]))


# ------------------------------------------------------------------------------------------- power stage
def power_record(obs_id: int, rec: dict, o: pd.Series, frames: list[dict], check: bool = False) -> dict:
    t_start = time.perf_counter()
    freqs, t, db, src = spectra(obs_id, rec, o)
    p = band_power(freqs, db, F_LOW, float(o["f_edge_hz"]))
    ref = ref_power(t, p)
    out = {"obs_id": obs_id, "variant_id": rec["variant_id"], "station_id": int(o["station_id"]), "split": o["split"],
           "condition": "clean" if rec["variant_id"] == "clean" else "benign",
           "ops": sorted({op["type"] for op in rec.get("operations", [])}), "source": src,
           "band_hz": [F_LOW, float(o["f_edge_hz"])], "ref_db": 10 * math.log10(ref),
           "win_t": t.tolist(), "win_p_rel": (p / ref).tolist(), "frames": frames}
    if check:                                    # cache precision: feature-band power vs the Phase 1 power_db
        w = pd.read_csv(Path("data/features") / str(obs_id) / "windows.csv")
        w = w[w["variant_id"] == rec["variant_id"]].sort_values("win_idx")
        pf = 10 * np.log10(band_power(freqs, db, float(o["feature_band_lo_hz"]), float(o["feature_band_hi_hz"])) + 1e-30)
        n = min(len(w), len(pf))
        out["check_power_db_max_abs_diff"] = float(np.max(np.abs(pf[:n] - w["power_db"].to_numpy()[:n]))) if n else None
    out["seconds"] = time.perf_counter() - t_start
    return out


def stage_power(out: Path, limit: int | None = None, check: bool = False) -> dict:
    obs = dev_passes()
    fr = pd.read_csv("data/tables/frames.csv", usecols=["obs_id", "variant_id", "counter", "valid", "t_rel_s"])
    fr = fr[fr["variant_id"] == "clean"]
    (out / "rec").mkdir(parents=True, exist_ok=True)
    log = {"excluded": {}, "failed": {}, "sources": {}}
    for n, (i, o) in enumerate(obs.iterrows()):
        if limit is not None and n >= limit:
            break
        frames, why = expected_frames(fr[fr["obs_id"] == i])
        if frames is None:
            log["excluded"][int(i)] = why
            continue
        for rec in recordings(int(i)):
            p = out / "rec" / f"{i}__{rec['variant_id']}.json"
            if p.exists() and not check:
                r = json.loads(p.read_text(encoding="utf-8"))
            else:
                try:
                    r = power_record(int(i), rec, o, frames, check)
                except FileNotFoundError as e:
                    log["failed"][f"{i}__{rec['variant_id']}"] = str(e)
                    continue
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(r), encoding="utf-8")
                tmp.replace(p)
                print(f"{i} {rec['variant_id']}: {r['source']}, {len(frames)} expected frames "
                      f"({sum(f['lost'] for f in frames)} lost), {r['seconds']:.1f} s"
                      + (f", check max |dP| {r['check_power_db_max_abs_diff']:.3f} dB" if check else ""), flush=True)
            log["sources"][r["source"]] = log["sources"].get(r["source"], 0) + 1
    (out / "power_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


# ------------------------------------------------------------------------------------------- report stage
def rows_of(r: dict, params: dict) -> list[dict]:
    t = np.asarray(r["win_t"], float)
    blocks = {"t0": t, "t1": t + WIN_S, "inband": np.asarray(r["win_p_rel"], float), "gain_db": np.zeros(len(t))}
    return jam_v1.window_rows(r["frames"], blocks, params)


def cp_upper(k: int, n: int, conf: float = 0.95) -> float:
    from scipy import stats
    return 1.0 if k >= n else float(stats.beta.ppf(conf, k + 1, n - k))


def tally(items: list[tuple[dict, list[dict]]]) -> dict:
    k = sum(sum(x["jamming_suspected"] for x in rows) for _, rows in items)
    n = sum(len(rows) for _, rows in items)
    kp = sum(jam_v1.pass_alert(rows) for _, rows in items)
    return {"windows": n, "alert_windows": k, "rate": k / n if n else float("nan"), "cp_upper_95": cp_upper(k, n) if n else None,
            "recordings": len(items), "alert_recordings": kp, "pass_rate": kp / len(items) if items else float("nan")}


def stage_report(out: Path) -> dict:
    params = jam_v1.load_params()
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "rec").glob("*.json"))]
    rows = {(r["obs_id"], r["variant_id"]): rows_of(r, params) for r in recs}
    stations = sorted({r["station_id"] for r in recs})
    l0 = params["loss_threshold"]
    p0, scored = {}, []
    for s in stations:
        vals = [x["P"] for r in recs if r["station_id"] != s for x in rows[(r["obs_id"], r["variant_id"])] if x["L"] >= l0]
        p0[s] = jam_v1.fit_p0(vals, params)
        for r in recs:
            if r["station_id"] == s:
                scored.append((r, jam_v1.apply(rows[(r["obs_id"], r["variant_id"])], p0[s], params)))
    log = json.loads((out / "power_log.json").read_text(encoding="utf-8")) if (out / "power_log.json").exists() else {}
    rep = {"gate": "docs/GATES_PHASE2_S4.md E(b)", "p0_db_by_held_out_station": {str(k): v for k, v in p0.items()},
           "sources": log.get("sources"), "excluded_passes": log.get("excluded"), "failed": log.get("failed"),
           "fa": {c: tally([x for x in scored if x[0]["condition"] == c]) for c in ("clean", "benign")},
           "by_station": {str(s): tally([x for x in scored if x[0]["station_id"] == s]) for s in stations},
           "benign_by_op": {op: tally([x for x in scored if x[0]["condition"] == "benign" and op in x[0]["ops"]])
                            for op in ("gain", "noise_rise", "fading", "edge_attenuation")},
           "note": "noise_rise FA is optimistic: frames are not re-decoded, added noise never adds loss"}
    worst = max(rep["fa"], key=lambda c: rep["fa"][c]["rate"])
    rep["gate_b"] = {"worst_condition": worst, "worst_fa": rep["fa"][worst]["rate"], "pass": rep["fa"][worst]["rate"] <= 0.01}
    ws = max(rep["by_station"], key=lambda s: rep["by_station"][s]["rate"])
    rep["worst_station"] = {"station": ws, **rep["by_station"][ws]}
    (out / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    md = ["# S4 gate (b): jam_v1 FA on SatNOGS FUNCUBE-1 train + calib (LOSO)", "",
          f"Sources: {rep['sources']}. Excluded passes: {rep['excluded_passes']}. Failed: {rep['failed']}", "",
          "| condition | windows | alerts | FA | CP 95 % upper | recordings alerted |", "|---|---|---|---|---|---|"]
    for c, s in rep["fa"].items():
        md.append(f"| {c} | {s['windows']} | {s['alert_windows']} | {s['rate']:.4f} | {s['cp_upper_95']} | "
                  f"{s['alert_recordings']}/{s['recordings']} |")
    g = rep["gate_b"]
    md += ["", f"Gate (b): worst {g['worst_condition']} FA {g['worst_fa']:.4f} -> {'PASS' if g['pass'] else 'FAIL'}",
           f"Worst station (not gated): {rep['worst_station']}", "", rep["note"], "",
           "```", json.dumps({"by_station": rep["by_station"], "benign_by_op": rep["benign_by_op"],
                              "p0": rep["p0_db_by_held_out_station"]}, indent=1), "```"]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["power", "report", "all"])
    ap.add_argument("--smoke", type=int, metavar="N", help="power stage on the first N passes, with cache check")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    require_gate()
    out = Path(a.out)
    if a.smoke:
        print(json.dumps(stage_power(out.with_name(out.name + "_smoke"), limit=a.smoke, check=True)))
    elif a.stage in ("power", "all"):
        print(json.dumps(stage_power(out)))
        if a.stage == "all":
            print(json.dumps(stage_report(out)["gate_b"]))
    elif a.stage == "report":
        print(json.dumps(stage_report(out)["gate_b"]))
    else:
        ap.error("--stage or --smoke required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
