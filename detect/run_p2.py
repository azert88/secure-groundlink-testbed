"""Phase 2 entry point: detect/run.py with a replay version switch (S1). detect/run.py is locked and unchanged.

The replay version comes from config detectors.replay_version (default v1) or --replay-version. v1 is exactly
detect/run.run. v2 runs the same pipeline with detect/replay_det_v2.py in place of detect/replay_det.py (swapped in
run.py's namespace for the duration of the call), its frame archive, parameters and threshold from detectors.replay_v2.
Evidence names are v1's, so the locked naming and fusion apply unchanged.

Example:  python detect/run_p2.py --obs-dir data/raw/<obs_id> --replay-version v2 --out out.json
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import sys
import types
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import fusion as F  # noqa: E402
from detect import replay_det_v2 as V2  # noqa: E402
from detect import run as R  # noqa: E402
from detect.frame_store_v2 import FrameStore  # noqa: E402

VERSIONS = ("v1", "v2")


@contextlib.contextmanager
def replay_v2_in_run(cfg: dict):
    """cfg for run.run with replay v2 swapped in; run.replay_det restored on exit."""
    rc2 = cfg["detectors"]["replay_v2"]
    p2 = V2.load_params(cfg)
    store = FrameStore.load(Path(rc2["store"])) if Path(rc2["store"]).exists() else None
    shim = types.SimpleNamespace(
        detect=lambda frames, norad, qc, _p, _store, start, obs: V2.detect(frames, norad, qc, p2, store, start, obs),
        load_params=lambda _cfg: p2)
    c2 = copy.deepcopy(cfg)
    c2["detectors"]["replay"] = {**cfg["detectors"]["replay"], "store": rc2["store"], "threshold": rc2["threshold"]}
    saved = R.replay_det
    R.replay_det = shim
    try:
        yield c2
    finally:
        R.replay_det = saved


def run(obs_dir: Path, cfg: dict, version: str | None = None, **kw) -> dict:
    version = version or cfg["detectors"].get("replay_version", "v1")
    if version not in VERSIONS:
        raise ValueError(f"replay version {version!r} not in {VERSIONS}")
    if version == "v1":
        out = R.run(obs_dir, cfg, **kw)
    else:
        with replay_v2_in_run(cfg) as c2:
            out = R.run(obs_dir, c2, **kw)
    out["replay_version"] = version
    return out


def verify(version: str) -> tuple[bool, str]:
    ok, msg = R.verify_models()
    if not ok or version == "v1":
        return ok, msg
    lock = Path("models/replay_v2/lock.json")
    return R.verify_models(lock) if lock.exists() else (False, f"{lock.as_posix()} missing: v2 is not locked")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the detectors and fusion with a replay version switch (Phase 2)")
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--replay-version", choices=VERSIONS, default=None, help="default: detectors.replay_version")
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
        ok, msg = verify(version)
        if not ok:
            print(json.dumps({"error": msg}), file=sys.stderr)
            return 2
    res = run(Path(a.obs_dir), cfg, version, model=Path(a.model), fusion_path=Path(a.fusion), norad=a.norad,
              full=a.full)
    txt = json.dumps(res, indent=1, default=str)
    Path(a.out).write_text(txt + "\n", encoding="utf-8") if a.out else print(txt)
    if a.md:
        Path(a.md).write_text(F.markdown(res), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
