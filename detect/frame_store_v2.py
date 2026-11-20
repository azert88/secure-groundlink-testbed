"""Frame archive for replay v2 (S1): the station's clean frames of earlier passes, WITH their bytes.

Same content and same build as the v1 hash store (detect/hash_store.py: clean canonical frames of the raw observation
folders, no labels), plus the frame bytes, so v2 can match a frame of the pass under test against the archive by
  exact bytes (v1), same counter with different bytes (E1), masked Hamming distance <= d (E2, FUNCUBE-1 only).
A query only sees passes that START BEFORE the recording under test and never the recording itself.

Entry per frame: [obs_id, pass_start_utc, frame_utc, hex], per satellite.
CLI: python detect/frame_store_v2.py [--obs-table data/tables/observations.csv] [--out models/replay_v2/frame_store.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect.frames_io import read_metadata, stream_from_dir  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ts(dt: datetime) -> str:
    return dt.strftime(FMT)


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


class FrameStore:
    def __init__(self, data: dict | None = None):
        self.data = data or {}          # {norad(str): [[obs_id, pass_start, frame_utc, hex], ...]}
        self._idx: dict = {}

    def add_pass(self, norad: int, obs_id: int, pass_start: datetime, frames: list[dict]) -> None:
        tab = self.data.setdefault(str(norad), [])
        for f in frames:
            tab.append([int(obs_id), _ts(pass_start), _ts(f["t_utc"]), f["data"].hex()])
        self._idx.pop(str(norad), None)

    def index(self, norad: int | None) -> dict:
        """Parsed entries of one satellite: obs, pass start, frame time, bytes, grouped arrays by length."""
        k = str(norad)
        if k not in self._idx:
            rows = self.data.get(k, [])
            ent = [{"obs": int(o), "ps": _dt(ps), "fu": _dt(fu), "data": bytes.fromhex(h)} for o, ps, fu, h in rows]
            by_len: dict[int, list[int]] = {}
            for i, e in enumerate(ent):
                by_len.setdefault(len(e["data"]), []).append(i)
            arr = {L: np.array([np.frombuffer(ent[i]["data"], np.uint8) for i in ix]) for L, ix in by_len.items()}
            self._idx[k] = {"entries": ent, "by_len": by_len, "arr": arr}
        return self._idx[k]

    def n_frames(self) -> dict:
        return {k: len(v) for k, v in self.data.items()}

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "FrameStore":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))


def build_from_raw(raw_dir: Path, obs_ids: list[int], qc: dict) -> FrameStore:
    """Archive of the clean canonical frames of the given raw observation folders (as hash_store.build_from_raw)."""
    st = FrameStore()
    for oid in obs_ids:
        d = Path(raw_dir) / str(oid)
        meta = read_metadata(d)
        if not (d / "demoddata").is_dir() or "start_utc" not in meta:
            continue
        frames, norad = stream_from_dir(d / "demoddata", qc, meta.get("norad"))
        st.add_pass(norad, oid, meta["start_utc"], frames)
    return st


def build_from_table(frames_csv: Path, obs_csv: Path, qc: dict) -> FrameStore:
    """Archive from the canonical frame table (clean variant of every observation, already merged), pass start and
    satellite from the observation table. Same frames as the raw build when the raw folders are complete."""
    import pandas as pd
    from detect.frames_io import stream_from_rows
    obs = pd.read_csv(obs_csv).set_index("obs_id")
    cols = ["obs_id", "variant_id", "filename", "hex", "decoder_branches", "n_copies", "copy_delays_s"]
    fr = pd.read_csv(frames_csv, usecols=cols, dtype={"copy_delays_s": str})
    fr = fr[fr["variant_id"] == "clean"]
    st = FrameStore()
    for oid, g in fr.groupby("obs_id", sort=True):
        if oid not in obs.index:
            continue
        norad = int(obs.loc[oid, "norad"])
        frames, _ = stream_from_rows(g.to_dict("records"), qc, norad)
        st.add_pass(norad, int(oid), _dt(obs.loc[oid, "start_utc"]), frames)
    return st


def main(argv: list[str] | None = None) -> int:
    import yaml
    from detect.hash_store import qc_view
    ap = argparse.ArgumentParser(description="Build the replay v2 frame archive from the canonical frame table")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--frames-table", default="data/tables/frames.csv")
    ap.add_argument("--obs-table", default="data/tables/observations.csv")
    ap.add_argument("--out", default=None, help="default: detectors.replay_v2.store")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    st = build_from_table(Path(a.frames_table), Path(a.obs_table), qc_view(cfg))
    ids = sorted({int(e[0]) for v in st.data.values() for e in v})
    out = Path(a.out or cfg["detectors"]["replay_v2"]["store"])
    st.save(out)
    print(json.dumps({"out": out.as_posix(), "observations": len(ids), "frames_per_norad": st.n_frames()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
