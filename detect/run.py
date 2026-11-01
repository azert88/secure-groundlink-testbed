"""Prototype entry point: run the detectors on ONE raw recording folder, without labels.

Folder layout (as a SatNOGS observation): audio.ogg, demoddata/ (optional for the interference
detector), metadata.json (optional: satellite NORAD id). Output: one JSON report with every alert and
its reason. Phase L: interference detector; later phases add replay, sequence and fusion here.

Example:  python detect/run.py --obs-dir data/raw/<obs_id> --out out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.audio_front import windows_from_audio  # noqa: E402
from detect.frames_io import read_metadata  # noqa: E402
from detect.interference_det import DEFAULT_MODEL, Detector  # noqa: E402


def run(obs_dir: Path, cfg: dict, model: Path = DEFAULT_MODEL, norad: int | None = None, full: bool = False) -> dict:
    meta = read_metadata(obs_dir)
    norad = norad if norad is not None else meta.get("norad")
    out = {"obs_dir": obs_dir.name, "norad": norad, "detectors": {}}
    audio = obs_dir / "audio.ogg"
    if audio.exists():
        t0 = time.perf_counter()
        tracemalloc.start()
        win, info = windows_from_audio(audio, cfg)
        res = Detector.load(model).run(win, norad)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        if not full:
            res.pop("windows")
        res.update(audio=info, runtime_s=round(time.perf_counter() - t0, 2), peak_traced_mb=round(peak / 2 ** 20, 1))
        out["detectors"]["interference"] = res
    else:
        out["detectors"]["interference"] = {"skipped": "no audio.ogg"}
    out["alert"] = any(d.get("events") for d in out["detectors"].values())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the detectors on one raw recording folder (no labels)")
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--norad", type=int, default=None, help="satellite NORAD id if metadata.json is missing")
    ap.add_argument("--out", default=None, help="JSON output (default: stdout)")
    ap.add_argument("--full", action="store_true", help="include per-window scores")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    res = run(Path(a.obs_dir), cfg, Path(a.model), a.norad, a.full)
    txt = json.dumps(res, indent=1)
    Path(a.out).write_text(txt + "\n", encoding="utf-8") if a.out else print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
