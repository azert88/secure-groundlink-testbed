"""Cross-pass hash store: the ground station's archive of clean frames from earlier passes.

Entry per frame: sha256 -> [[obs_id, pass_start_utc, frame_utc], ...] per satellite. Built from raw
demoddata folders with the same canonical stream as the detectors (detect.frames_io). Holds no labels.
A query for the recording under test only sees passes that START BEFORE it and never the recording
itself (exclude_obs), as a real station would (DETECTOR_SPEC Phase J).

CLI: python detect/hash_store.py --obs-table data/tables/observations.csv --out models/replay/hash_store.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.frames_io import read_metadata, stream_from_dir  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ts(dt: datetime) -> str:
    return dt.strftime(FMT)


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def qc_view(cfg: dict) -> dict:
    """qc + canonical sections, the parameter view used by counters and the canonical stream."""
    return {**cfg["qc"], **cfg["canonical"]}


class HashStore:
    def __init__(self, data: dict | None = None):
        # {norad(str): {sha256: [[obs_id, pass_start, frame_utc], ...]}}
        self.data = data or {}

    def add_pass(self, norad: int, obs_id: int, pass_start: datetime, frames: list[dict]) -> None:
        tab = self.data.setdefault(str(norad), {})
        for f in frames:
            tab.setdefault(f["sha256"], []).append([int(obs_id), _ts(pass_start), _ts(f["t_utc"])])

    def lookup(self, norad: int | None, sha256: str, before_utc: datetime | None,
               exclude_obs: int | None = None) -> list[tuple[int, datetime, datetime]]:
        """Archived copies of a frame from passes that start strictly before before_utc."""
        out = []
        for obs_id, ps, fu in self.data.get(str(norad), {}).get(sha256, []):
            if exclude_obs is not None and obs_id == exclude_obs:
                continue
            ps = _dt(ps)
            if before_utc is not None and ps >= before_utc:
                continue
            out.append((obs_id, ps, _dt(fu)))
        return out

    def n_frames(self) -> dict:
        return {k: sum(len(v) for v in tab.values()) for k, tab in self.data.items()}

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "HashStore":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))


def build_from_raw(raw_dir: Path, obs_ids: list[int], qc: dict) -> HashStore:
    """Store of the clean canonical frames of the given raw observation folders."""
    st = HashStore()
    for oid in obs_ids:
        d = Path(raw_dir) / str(oid)
        meta = read_metadata(d)
        if not (d / "demoddata").is_dir() or "start_utc" not in meta:
            continue
        frames, norad = stream_from_dir(d / "demoddata", qc, meta.get("norad"))
        st.add_pass(norad, oid, meta["start_utc"], frames)
    return st


def main(argv: list[str] | None = None) -> int:
    import pandas as pd
    import yaml
    ap = argparse.ArgumentParser(description="Build the cross-pass hash store from raw demoddata folders")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--obs-table", default="data/tables/observations.csv", help="observations to archive (obs_id column)")
    ap.add_argument("--out", default=None, help="default: detectors.replay.store")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    ids = sorted(int(i) for i in pd.read_csv(a.obs_table)["obs_id"])
    st = build_from_raw(Path(cfg["download"]["raw_dir"]), ids, qc_view(cfg))
    out = Path(a.out or cfg["detectors"]["replay"]["store"])
    st.save(out)
    print(json.dumps({"out": out.as_posix(), "observations": len(ids), "frames_per_norad": st.n_frames()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
