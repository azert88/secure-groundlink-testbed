"""Phase 2 entry point with an interference version switch (S3). detect/run_p2.py (in the replay_v21 lock) and
detect/run.py (in the fusion lock) are unchanged; this wraps run_p2.run as run_p2 wraps run.run.

--interference-version v2 (default) is exactly detect/run_p2.run. v3 (detect/interference_v3.py) or v3.1
(detect/interference_v31.py, v3 with the re-encoded band edge and empty bins ignored) runs interference v2
as before and ORs a per-station template alert into its windows; template-only runs become extra interference events
(fusion reads them as interference-model events with v2's probability, so they stay in the flag tier unless v2 is
confident). The station comes from metadata.json ground_station or --station.

Example:  python detect/run_p2_s3.py --obs-dir data/raw/<obs_id> --interference-version v3 --no-verify --out out.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import fusion as F  # noqa: E402
from detect import interference_v3 as IV3  # noqa: E402
from detect import interference_v31 as IV31  # noqa: E402
from detect import run as R  # noqa: E402
from detect import run_p2 as P2  # noqa: E402

INTERFERENCE_VERSIONS = ("v2", "v3", "v3.1")
INTERFERENCE_MODULES = {"v3": IV3, "v3.1": IV31}
INTERFERENCE_LOCKS = {"v3": "models/interference_v3/lock.json", "v3.1": "models/interference_v31/lock.json"}


@contextlib.contextmanager
def interference_v3_in_run(cfg: dict, obs_dir: Path, station: int | None = None, module=IV3):
    """run.audio_windows wrapped to keep the chained audio, run.Detector wrapped to add the template alert; both
    restored afterwards."""
    mp = Path(obs_dir) / "metadata.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    station = station if station is not None else meta.get("ground_station")
    start = meta.get("start")
    cap: dict = {}
    saved_aw, saved_det = R.audio_windows, R.Detector

    def audio_windows(path, c):
        win, y, sr, info = saved_aw(path, c)
        cap["y"], cap["sr"] = y, sr
        return win, y, sr, info

    class DetectorV3:
        def __init__(self, inner):
            self.inner = inner

        @classmethod
        def load(cls, path):
            return cls(saved_det.load(path))

        def run(self, win, norad):
            res = self.inner.run(win, norad)
            v3 = module.run_on_recording(res, cap["y"], cap["sr"], station, norad, start, cfg)
            merged = module.merge_into_v2(res, v3)
            cap["v3"] = merged["v3"]
            cap.pop("y", None)
            return merged

    R.audio_windows, R.Detector = audio_windows, DetectorV3
    try:
        yield cap
    finally:
        R.audio_windows, R.Detector = saved_aw, saved_det


def run(obs_dir: Path, cfg: dict, version: str | None = None, interference_version: str = "v2",
        station: int | None = None, **kw) -> dict:
    if interference_version not in INTERFERENCE_VERSIONS:
        raise ValueError(f"interference version {interference_version!r} not in {INTERFERENCE_VERSIONS}")
    if interference_version == "v2":
        out = P2.run(obs_dir, cfg, version, **kw)
    else:
        with interference_v3_in_run(cfg, obs_dir, station, INTERFERENCE_MODULES[interference_version]) as cap:
            out = P2.run(obs_dir, cfg, version, **kw)
        out["interference_v3"] = cap.get("v3")
    out["interference_version"] = interference_version
    return out


def verify(version: str, interference_version: str) -> tuple[bool, str]:
    ok, msg = P2.verify(version)
    if not ok or interference_version not in INTERFERENCE_LOCKS:
        return ok, msg
    lock = Path(INTERFERENCE_LOCKS[interference_version])
    return R.verify_models(lock) if lock.exists() else (False, f"{lock.as_posix()} missing: {interference_version} is not locked")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the detectors and fusion with replay and interference version switches")
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--replay-version", choices=P2.VERSIONS, default=None, help="default: detectors.replay_version")
    ap.add_argument("--interference-version", choices=INTERFERENCE_VERSIONS, default="v2")
    ap.add_argument("--station", type=int, default=None, help="station id for interference v3 (default: metadata.json)")
    ap.add_argument("--model", default=str(R.DEFAULT_MODEL))
    ap.add_argument("--fusion", default=str(F.DEFAULT_FUSION))
    ap.add_argument("--norad", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="skip the signed-model checks (development only)")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    version = a.replay_version or cfg["detectors"].get("replay_version", "v1")
    if not a.no_verify:
        ok, msg = verify(version, a.interference_version)
        if not ok:
            print(json.dumps({"error": msg}), file=sys.stderr)
            return 2
    res = run(Path(a.obs_dir), cfg, version, a.interference_version, a.station, model=Path(a.model),
              fusion_path=Path(a.fusion), norad=a.norad, full=a.full)
    txt = json.dumps(res, indent=1, default=str)
    Path(a.out).write_text(txt + "\n", encoding="utf-8") if a.out else print(txt)
    if a.md:
        Path(a.md).write_text(F.markdown(res), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
