"""Phase G: features for all variants (docs/DATASET_SPEC.md section 7).

For every recipe of an observation: load the clean raw data once, apply the recipe in memory
(attacks.apply), run the common chain (data.build_tables.common_chain), and compute the frames,
windows and labels rows with the same code as Phase D (the clean variant reproduces data/tables).
One worker process per observation (multiprocessing, spawn); each reports its runtime and peak
memory (peak working set on Windows, ru_maxrss on Linux).
Outputs: features.out_dir/<obs_id>/{frames,windows,labels}.csv, then <obs_id>/done.json (written last,
with a SHA-256 digest of that observation's recipes), and features.runtime_json (updated after every
observation, so it is also the progress file). --resume skips observations whose done.json matches the
current recipes, so an interrupted run can be restarted with the same command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
from build_tables import FMT, FRAME_COLS, LABEL_COLS, WINDOW_COLS, common_chain, frame_rows, load_clean_frames, write_csv  # noqa: E402
from attacks.apply import apply_recipe  # noqa: E402
from attacks.common import burst_intervals, window_labels  # noqa: E402
from attacks.interference import reference_powers  # noqa: E402
from detect.counters import counter_codec  # noqa: E402
from detect.features import FEATURES, burst_active, window_features  # noqa: E402


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MB."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE            # 64-bit pseudo-handle, not a C int
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise OSError(ctypes.get_last_error(), "K32GetProcessMemoryInfo failed")
        return c.PeakWorkingSetSize / 2 ** 20
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def load_clean(obs_id: int, cfg: dict, obs: pd.Series, flags: dict) -> dict:
    """Clean observation dict for attacks.apply (raw audio decoded, canonical frames, reference powers)."""
    qc, tb = {**cfg["qc"], **cfg["canonical"]}, cfg["tables"]
    obs_dir = Path(cfg["download"]["raw_dir"]) / str(obs_id)
    t0 = datetime.strptime(obs["start_utc"], FMT).replace(tzinfo=timezone.utc)
    norad = int(obs["norad"])
    dec, enc = counter_codec(norad, qc)
    frames = load_clean_frames(obs_dir, norad, qc, t0)
    reliable = bool(flags["timing_reliable"])
    c = {"obs_id": obs_id, "norad": norad, "start": t0, "frames": frames, "timing_reliable": reliable,
         "order_by_counter": norad == qc["funcube"]["norad"], "f_low": qc["psd"]["f_low_hz"],
         "f_edge": obs["f_edge_hz"], "f_drop": obs["f_drop_hz"], "band_hi": obs["feature_band_hi_hz"],
         "encode_counter": enc, "decode_counter": dec, "audio": None, "sr": None, "ref": None}
    if flags["audio"]:
        x, sr = sf.read(str(obs_dir / "audio.ogg"), dtype="float32")
        rows = frame_rows(frames, t0, reliable, c["order_by_counter"], tb["burst_gap_s"], dec)
        p = {**qc["psd"], "chunk_windows": tb["features_chunk_windows"]}
        intervals = burst_intervals(rows, tb["burst_margin_s"]) if reliable else None
        c.update(audio=x, sr=sr, ref=reference_powers(x, sr, p, c["f_low"], c["f_edge"], c["band_hi"], intervals))
    return c


def variant_tables(recipe: dict, clean: dict, cfg: dict, load_source) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return tables_from_result(apply_recipe(recipe, clean, load_source), recipe, clean, cfg)


def tables_from_result(res: dict, recipe: dict, clean: dict, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """frames, windows, labels of an applied recipe (res = attacks.apply.apply_recipe output)."""
    tb, qc = cfg["tables"], cfg["qc"]
    vid, oid = recipe["variant_id"], recipe["obs_id"]
    rows = frame_rows(res["frames"], clean["start"], clean["timing_reliable"], clean["order_by_counter"],
                      tb["burst_gap_s"], clean["decode_counter"])
    fdf = pd.DataFrame([{"obs_id": oid, "variant_id": vid, **r} for r in rows], columns=FRAME_COLS)
    labels = [{"obs_id": oid, "variant_id": vid, "unit": "frame", "index": r["idx"], "is_attack": r["is_attack"],
               "attack_type": r["attack_type"], "subtype": r["subtype"], "snr_db": np.nan, "inr_db": np.nan,
               "overlap_frac": float(r["is_attack"]), "evasion": r["evasion"]} for r in rows]
    wdf = pd.DataFrame(columns=WINDOW_COLS)
    if res["audio"] is not None and clean["sr"]:
        y = common_chain(res["audio"], clean["sr"], tb["chain"])
        p = {**qc["psd"], "chunk_windows": tb["features_chunk_windows"]}
        win = window_features(y, clean["sr"], p, clean["f_low"], clean["band_hi"])
        del y
        if clean["timing_reliable"]:
            win["burst_active"] = burst_active(win["t_start_s"], qc["psd"]["win_s"], burst_intervals(rows, tb["burst_margin_s"]))
        else:
            win["burst_active"] = np.full(len(win["t_start_s"]), np.nan)
        n = len(win["t_start_s"])
        wdf = pd.DataFrame({"obs_id": oid, "variant_id": vid, "win_idx": np.arange(n),
                            **{k: win[k] for k in ["t_start_s", "burst_active"] + FEATURES}})[WINDOW_COLS]
        wl = window_labels(win["t_start_s"], qc["psd"]["win_s"], res["events"], cfg["features"]["window_min_overlap_s"])
        labels += [{"obs_id": oid, "variant_id": vid, "unit": "window", "index": i, **lab} for i, lab in enumerate(wl)]
    return fdf.astype({"counter": "Int64"}), wdf, pd.DataFrame(labels, columns=LABEL_COLS)


def recipes_digest(recipe_dir: Path) -> str:
    """SHA-256 over the sorted recipe files of one observation (names and bytes)."""
    h = hashlib.sha256()
    for p in sorted(recipe_dir.glob("*.json")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def is_done(obs_id: int, cfg: dict) -> bool:
    """True if done.json exists and matches the observation's current recipes."""
    marker = Path(cfg["features"]["out_dir"]) / str(obs_id) / "done.json"
    if not marker.exists():
        return False
    digest = json.loads(marker.read_text(encoding="utf-8")).get("recipes_sha256")
    return digest == recipes_digest(Path(cfg["attacks"]["recipes_dir"]) / str(obs_id))


def process_observation(args: tuple) -> dict:
    """Worker: every recipe of one observation. Returns runtime and memory stats."""
    obs_id, config_path = args
    t_start = time.perf_counter()
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    obs_tab = pd.read_csv(Path(cfg["tables"]["out_dir"]) / "observations.csv").set_index("obs_id")
    splits = json.loads(Path(cfg["splits"]["out_json"]).read_text(encoding="utf-8"))
    flags = {int(i): f for s in splits["satellites"].values() for i, f in s["obs"].items()}
    clean = load_clean(obs_id, cfg, obs_tab.loc[obs_id], flags[obs_id])
    t_load = time.perf_counter() - t_start
    cache = {}

    def load_source(i):
        if i not in cache:
            cache[i] = load_clean(i, cfg, obs_tab.loc[i], flags[i])
        return cache[i]

    rdir = Path(cfg["attacks"]["recipes_dir"]) / str(obs_id)
    digest = recipes_digest(rdir)
    recipes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(rdir.glob("*.json"))]
    fparts, wparts, lparts, per = [], [], [], []
    for rec in recipes:
        t = time.perf_counter()
        f, w, lab = variant_tables(rec, clean, cfg, load_source)
        fparts.append(f), wparts.append(w), lparts.append(lab)
        per.append({"variant_id": rec["variant_id"], "seconds": round(time.perf_counter() - t, 3)})
    out = Path(cfg["features"]["out_dir"]) / str(obs_id)
    (out / "done.json").unlink(missing_ok=True)
    write_csv(pd.concat(fparts, ignore_index=True), out / "frames.csv")
    write_csv(pd.concat(wparts, ignore_index=True) if wparts else pd.DataFrame(columns=WINDOW_COLS), out / "windows.csv")
    write_csv(pd.concat(lparts, ignore_index=True), out / "labels.csv")
    dur = float(len(clean["audio"]) / clean["sr"]) if clean["audio"] is not None else 0.0
    (out / "done.json").write_text(json.dumps({"obs_id": obs_id, "recipes_sha256": digest}) + "\n", encoding="utf-8")
    return {"obs_id": obs_id, "norad": clean["norad"], "audio_s": round(dur, 1), "n_variants": len(recipes),
            "load_s": round(t_load, 2), "seconds": round(time.perf_counter() - t_start, 2),
            "per_variant_mean_s": round(float(np.mean([p["seconds"] for p in per])), 3) if per else None,
            "peak_rss_mb": round(peak_rss_mb(), 1), "variants": per}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--obs", type=int, nargs="*", help="only these observations (default: every recipe folder)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--resume", action="store_true", help="skip observations already done with the current recipes")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    ids = args.obs or sorted(int(p.name) for p in Path(cfg["attacks"]["recipes_dir"]).iterdir() if p.is_dir())
    todo = [i for i in ids if not (args.resume and is_done(i, cfg))]
    path = Path(cfg["features"]["runtime_json"])
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[G] {len(todo)} of {len(ids)} observations to do", file=sys.stderr, flush=True)
    ctx = mp.get_context("spawn")
    results = []
    with ctx.Pool(args.workers, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(process_observation, [(i, args.config) for i in todo]):
            results.append(r)
            # progress file: rewritten after every observation, so an interruption loses at most one
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            old[str(r["obs_id"])] = r
            done = sum(is_done(i, cfg) for i in ids)
            old["_progress"] = {"done": done, "total": len(ids),
                                "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            path.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[G] {done}/{len(ids)} obs {r['obs_id']}: {r['n_variants']} variants, {r['seconds']} s, "
                  f"peak {r['peak_rss_mb']} MB", file=sys.stderr, flush=True)
    print(json.dumps([{k: v for k, v in r.items() if k != "variants"} for r in results], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
