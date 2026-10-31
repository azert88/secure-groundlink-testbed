"""Per-satellite downlink region from TRAIN observations only (decision 55).

For every TRAIN observation with audio (data/splits.json), detect the observation's signal bands
with qc.signal_bands (runs wider than line_max_width_hz above the in-band median). A frequency bin
belongs to the satellite's downlink region if it lies in such a band in at least
downlink_min_obs_frac of the TRAIN observations. The region is fixed per satellite, so an
interferer added to one observation can never make itself "downlink".
Output: qc.downlink_file (JSON) with the region, the TRAIN obs used and their observed bands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qc import runs, signal_bands, window_psds  # noqa: E402


def region_from_bands(freqs: np.ndarray, per_obs: list[list[list[float]]], min_frac: float) -> list[list[float]]:
    """Runs of bins covered by an observed band in >= min_frac of the observations."""
    if not per_obs:
        return []
    count = np.zeros(len(freqs))
    for bands in per_obs:
        cov = np.zeros(len(freqs), dtype=bool)
        for lo, hi in bands:
            cov |= (freqs >= lo) & (freqs <= hi)
        count += cov
    df = float(freqs[1] - freqs[0])
    return [[round(float(freqs[a]), 1), round(float(freqs[b - 1] + df), 1)]
            for a, b in runs(count / len(per_obs) >= min_frac)]


def observed_bands(path: Path, f_edge: float, qc: dict):
    x, sr = sf.read(str(path), dtype="float32")
    freqs, psd = window_psds(x, sr, qc["psd"])
    db = 10 * np.log10(psd + 1e-20)
    p = qc["psd"]
    band = (freqs >= p["f_low_hz"]) & (freqs <= f_edge - p["edge_margin_hz"])
    return freqs, signal_bands(db[:, band], freqs[band], float(freqs[1] - freqs[0]), qc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    qc = cfg["qc"]
    splits = json.loads(Path(cfg["splits"]["out_json"]).read_text(encoding="utf-8"))
    qrows = {o["obs_id"]: o for o in json.loads(Path(qc["report_json"]).read_text(encoding="utf-8"))["observations"]}
    out = {"source": "TRAIN observations of data/splits.json", "min_obs_frac": qc["downlink_min_obs_frac"],
           "satellites": {}}
    for norad, sat in splits["satellites"].items():
        train = [i for i in sat["splits"]["train"] if sat["obs"][str(i)]["audio"]]
        per_obs, freqs = {}, None
        for i in train:
            freqs, bands = observed_bands(Path(cfg["download"]["raw_dir"]) / str(i) / "audio.ogg",
                                          qrows[i]["f_edge_hz"], qc)
            per_obs[str(i)] = bands
            print(f"[downlink] {norad} obs {i}: {bands}", file=sys.stderr)
        region = region_from_bands(freqs, list(per_obs.values()), qc["downlink_min_obs_frac"]) if freqs is not None else []
        out["satellites"][norad] = {"name": sat["name"], "downlink": region, "n_train_obs": len(train),
                                    "n_with_band": sum(bool(b) for b in per_obs.values()), "observed": per_obs}
    Path(qc["downlink_file"]).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: {"downlink": v["downlink"], "n_train_obs": v["n_train_obs"], "n_with_band": v["n_with_band"]}
                      for k, v in out["satellites"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
