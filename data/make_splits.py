"""Phase E: train / calib / test splits by observation (DATASET_SPEC.md section 5).

Universe per satellite: observations kept for audio (Phase C `selected`: qc_status ok or
flagged_reviewed_ok, TECHSAT 1B keep-first-40) or passing frame QC (decision 37).
Test is station-disjoint from train; calib may share stations with train.
  FUNCUBE-1: test = all observations of splits.test_only_stations stations (decision 23), chosen
    among stations not in excluded_test_stations with 3..6 universe observations (decision 30);
    if more stations qualify than needed, the choice is a seeded draw.
  TECHSAT 1B: the frame-QC observations are an extra cross-satellite test set only (decision 38),
    so their stations are test stations; further stations (seeded order) are added while that
    brings the test share closer to the target fraction. Those observations also count for audio
    tasks when they pass audio QC (decision 57: keep-first-40 only limited volume).
Remaining observations: seeded shuffle, calib = round(calib fraction x universe), rest train.
Writes data/splits.json, fills the split column of the Phase D observations.csv if it exists,
and records the SHA-256 of splits.json in data/DATA.md. Never re-split after features exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

NAMES = {39444: "FUNCUBE-1", 25397: "TECHSAT 1B"}


def split_satellite(rows: list[dict], norad: int, cfg: dict, seed: int) -> dict:
    sp = cfg["splits"]
    rng = np.random.default_rng(seed + norad)
    by_station: dict[int, list[int]] = {}
    for r in rows:
        by_station.setdefault(r["station_id"], []).append(r["obs_id"])
    n = len(rows)
    frame_extra = sorted(r["obs_id"] for r in rows if r["frame_qc"]) if norad == 25397 else []
    if norad in sp["test_only_stations"]:
        lo, hi = sp["test_station_obs_range"]
        eligible = sorted(s for s, ids in by_station.items()
                          if s not in sp["excluded_test_stations"].get(norad, []) and lo <= len(ids) <= hi)
        k = sp["test_only_stations"][norad]
        if len(eligible) < k:
            raise SystemExit(f"{norad}: only {len(eligible)} eligible test stations: {eligible}")
        test_st = sorted(eligible) if len(eligible) == k else sorted(rng.choice(eligible, k, replace=False).tolist())
    else:
        test_st = sorted({r["station_id"] for r in rows if r["obs_id"] in frame_extra})
        target = sp["fractions"]["test"] * n
        for s in rng.permutation(sorted(set(by_station) - set(test_st))).tolist():
            cur = sum(len(by_station[t]) for t in test_st)
            if abs(cur + len(by_station[s]) - target) < abs(cur - target):
                test_st.append(s)
        test_st = sorted(test_st)
    test = sorted(i for s in test_st for i in by_station[s])
    rest = sorted(r["obs_id"] for r in rows if r["station_id"] not in test_st)
    rest = rng.permutation(rest).tolist()
    n_cal = int(round(sp["fractions"]["calib"] * n))
    splits = {"train": sorted(rest[n_cal:]), "calib": sorted(rest[:n_cal]), "test": test}
    st = {r["obs_id"]: r["station_id"] for r in rows}
    train_st = {st[i] for i in splits["train"]}
    assert not train_st & set(test_st), "test station in train"
    assert set(frame_extra) <= set(test), "frame extra set outside test"
    return {"name": NAMES[norad], "test_stations": test_st, "splits": splits, "frame_test_extra": frame_extra,
            # audio use: Phase C selection, plus audio-QC passes of the test-only frame set (decision 57)
            "obs": {str(r["obs_id"]): {"station_id": r["station_id"],
                                       "audio": bool(r["selected"] or (r["obs_id"] in frame_extra and r.get("audio_qc"))),
                                       "frames": bool(r["frame_qc"]), "timing_reliable": bool(r["timing_reliable"])}
                    for r in rows}}


def counts_table(result: dict) -> list[dict]:
    out = []
    for norad, sat in result["satellites"].items():
        for name, ids in sat["splits"].items():
            c = Counter(sat["obs"][str(i)]["station_id"] for i in ids)
            for station, k in sorted(c.items()):
                ids_st = [i for i in ids if sat["obs"][str(i)]["station_id"] == station]
                out.append({"satellite": sat["name"], "split": name, "station_id": station, "n_obs": k,
                            "n_audio": sum(sat["obs"][str(i)]["audio"] for i in ids_st),
                            "n_frames": sum(sat["obs"][str(i)]["frames"] for i in ids_st)})
    return out


def record_sha(data_md: Path, path: Path, sha: str) -> None:
    text = data_md.read_text(encoding="utf-8") if data_md.exists() else ""
    head = "## Splits"
    block = f"{head}\n\n`{path.as_posix()}` SHA-256: `{sha}` (data/make_splits.py, Phase E).\n"
    if head in text:
        start = text.index(head)
        end = text.find("\n## ", start + len(head))
        text = text[:start] + block + (text[end:] if end != -1 else "")
    else:
        text = text.rstrip("\n") + "\n\n" + block
    data_md.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    obs = json.loads(Path(cfg["qc"]["report_json"]).read_text(encoding="utf-8"))["observations"]
    universe = [o for o in obs if o.get("selected") or o.get("frame_qc")]
    result = {"seed": cfg["seed"], "source": cfg["qc"]["report_json"],
              "rules": "by observation; test station-disjoint from train; see data/make_splits.py",
              "satellites": {str(n): split_satellite([o for o in universe if o["norad"] == n], n, cfg, cfg["seed"])
                             for n in sorted({o["norad"] for o in universe})}}
    out = Path(cfg["splits"]["out_json"])
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    out.write_text(text, encoding="utf-8", newline="\n")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    record_sha(Path(cfg["download"]["data_md"]), out, sha)

    obs_csv = Path(cfg["tables"]["out_dir"]) / "observations.csv"
    if obs_csv.exists():
        df = pd.read_csv(obs_csv, dtype=str, keep_default_na=False)      # keep every other cell byte-identical
        lookup = {i: name for sat in result["satellites"].values() for name, ids in sat["splits"].items() for i in ids}
        df["split"] = df["obs_id"].map(lambda i: lookup.get(int(i), ""))
        df.to_csv(obs_csv, index=False, lineterminator="\n")
    table = counts_table(result)
    summary = {sat["name"]: {name: len(ids) for name, ids in sat["splits"].items()} |
               {"test_stations": sat["test_stations"], "frame_test_extra": len(sat["frame_test_extra"])}
               for sat in result["satellites"].values()}
    print(json.dumps({"sha256": sha, "summary": summary, "per_station": table}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
