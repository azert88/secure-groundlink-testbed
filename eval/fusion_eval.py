"""Phase N, development evaluation of the fusion on TRAIN + CALIB (labels are read here only, never in detect/).

Stages
  lines  [--split train|calib] [--workers N] [--resume]
         line screen (detect/line_screen.py) on every audio variant of the frozen recipes: recipe applied in memory,
         common chain, screen over the feature band; every line found is cached with its frequency, so the upper
         frequency cap can be compared afterwards -> eval/out/fusion/lines/<split>/<obs_id>.json
  dev    recording-level fusion on train + calib from the frozen detectors' cached scores (replay, sequence: per-frame
         caches; interference v2: station-out-of-fold window scores, decision 92) + the line screen; chooses the line
         screen's frequency cap (false alarms first), fits the confidence maps, writes models/fusion/fusion.json and
         eval/out/fusion/report_dev.{json,md}. Test is never read here.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

OUT = Path("eval/out/fusion")
DEV_SPLITS = ("train", "calib")


def _flags() -> dict:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return {int(i): f for s in sp["satellites"].values() for i, f in s["obs"].items()}


def split_obs(split: str) -> list[int]:
    sp = json.loads(Path("data/splits.json").read_text(encoding="utf-8"))
    return sorted(int(o) for s in sp["satellites"].values() for o in s["splits"][split])


def downlink_train() -> dict:
    """Per-satellite downlink region learned from TRAIN observations only (data/downlink_bands.json, decision 55)."""
    d = json.loads(Path("data/downlink_bands.json").read_text(encoding="utf-8"))["satellites"]
    return {k: v["downlink"] for k, v in d.items()}


# ------------------------------------------------------------------------------------------------ lines
def lines_obs(args: tuple) -> dict:
    """Worker: line screen on every audio variant of one observation."""
    oid, split, config = args
    from attacks.apply import apply_recipe
    from build_features import load_clean
    from build_tables import common_chain
    from detect.line_screen import screen_audio
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    obs = pd.read_csv("data/tables/observations.csv").set_index("obs_id")
    t0 = time.perf_counter()
    clean = load_clean(oid, cfg, obs.loc[oid], _flags()[oid])
    dl = downlink_train().get(str(clean["norad"]), [])
    out = {"obs_id": oid, "norad": clean["norad"], "split": split, "f_edge_hz": float(clean["f_edge"]),
           "band_hi_hz": float(clean["band_hi"]), "variants": {}}
    if clean["audio"] is not None:
        for p in sorted((Path(cfg["attacks"]["recipes_dir"]) / str(oid)).glob("*.json")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            res = apply_recipe(rec, clean)
            if res["audio"] is None:
                continue
            y = common_chain(res["audio"], clean["sr"], cfg["tables"]["chain"])
            s = screen_audio(y, clean["sr"], (clean["f_low"], clean["band_hi"]), cfg["qc"], dl)
            s.pop("t_start_s", None)
            out["variants"][rec["variant_id"]] = s
    out["seconds"] = round(time.perf_counter() - t0, 1)
    d = OUT / "lines" / split
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{oid}.tmp"
    tmp.write_text(json.dumps(out) + "\n", encoding="utf-8")
    tmp.replace(d / f"{oid}.json")
    return {"obs_id": oid, "n_variants": len(out["variants"]), "seconds": out["seconds"]}


def stage_lines(split: str, workers: int, resume: bool, config: str) -> None:
    if split not in DEV_SPLITS:
        raise SystemExit("lines: train or calib only (test goes through eval/run_standard_test.py)")
    flags = _flags()
    ids = [o for o in split_obs(split) if flags[o]["audio"]]
    todo = [o for o in ids if not (resume and (OUT / "lines" / split / f"{o}.json").exists())]
    print(f"[N lines {split}] {len(todo)} of {len(ids)} observations", file=sys.stderr, flush=True)
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=1) as pool:
        for i, r in enumerate(pool.imap_unordered(lines_obs, [(o, split, config) for o in todo])):
            print(f"[N lines {split}] {i + 1}/{len(todo)} {r}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["lines", "dev"])
    ap.add_argument("--split", choices=list(DEV_SPLITS), default="train")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    if a.stage == "lines":
        stage_lines(a.split, a.workers, a.resume, a.config)
    else:
        from eval.fusion_dev import run_dev
        rep = run_dev(yaml.safe_load(Path(a.config).read_text(encoding="utf-8")))
        print(json.dumps(rep["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
