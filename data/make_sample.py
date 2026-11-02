"""Build data/sample/: small raw observations for the offline demo, with SANITIZED metadata (decision 29).

Copies audio.ogg and demoddata/ unchanged from data/raw/<obs_id>/ and writes a metadata.json that keeps only an
allowlist of fields (satellite, pass times, transmitter, numeric station id, TLE). Removed: observer and station names,
station coordinates and altitude, client metadata, user fields and every download URL. The removed key names are listed
in the file ("removed_fields"), and the SatNOGS source page and licence are recorded.

Example:  python data/make_sample.py --obs 14932721 15080917
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

KEEP = ["id", "norad_cat_id", "start", "end", "status", "ground_station", "observation_frequency", "max_altitude",
        "transmitter_mode", "transmitter_baud", "transmitter_description", "transmitter_downlink_low",
        "transmitter_downlink_high", "tle0", "tle1", "tle2"]


def sanitize(meta: dict) -> dict:
    out = {k: meta[k] for k in KEEP if k in meta}
    out["removed_fields"] = sorted(k for k in meta if k not in KEEP)
    out["source"] = f"https://network.satnogs.org/observations/{meta['id']}/"
    out["licence"] = "CC BY-SA 4.0 (SatNOGS Network)"
    return out


def make(obs_id: int, raw_dir: Path, out_dir: Path) -> dict:
    src, dst = raw_dir / str(obs_id), out_dir / str(obs_id)
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "demoddata").mkdir(parents=True)
    shutil.copy2(src / "audio.ogg", dst / "audio.ogg")
    for f in sorted((src / "demoddata").iterdir()):
        shutil.copy2(f, dst / "demoddata" / f.name)
    meta = sanitize(json.loads((src / "metadata.json").read_text(encoding="utf-8")))
    (dst / "metadata.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return {"obs_id": obs_id, "norad": meta["norad_cat_id"], "n_frame_files": len(list((dst / "demoddata").iterdir())),
            "audio_bytes": (dst / "audio.ogg").stat().st_size}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy raw observations to data/sample/ with sanitized metadata")
    ap.add_argument("--obs", type=int, nargs="+", required=True)
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--out-dir", default="data/sample")
    a = ap.parse_args(argv)
    print(json.dumps([make(o, Path(a.raw_dir), Path(a.out_dir)) for o in a.obs], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
