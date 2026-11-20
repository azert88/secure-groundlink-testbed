"""Reference outputs of replay v1 and v2 on the two sample recordings (and a near-copy variant of each), used by
tests/test_replay_det_v21.py to show that the v2.1 code leaves v1 and v2 unchanged. Generated BEFORE the v2.1 change
into tests/data/replay_golden_v1_v2.json."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import yaml

from detect import replay_det as v1
from detect import replay_det_v2 as v2
from detect.frame_store_v2 import FrameStore
from detect.frames_io import read_metadata, stream_from_dir
from detect.hash_store import HashStore, qc_view

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ("14932721", "15080917")
KEEP = ("filename", "score", "evidence", "reason")


def near_variant(frames, qc, norad, shift_s=200.0):
    """Copies of frames 2..6 with one byte changed outside the protected fields, shifted by shift_s."""
    out = list(frames)
    for f in frames[2:7]:
        b = bytearray(f["data"])
        m = v2.protected_mask(norad, qc, bytes(b))
        pos = next(i for i in range(len(b) - 1, -1, -1) if not m[i])
        b[pos] ^= 0x5A
        d = bytes(b)
        out.append({**f, "data": d, "sha256": hashlib.sha256(d).hexdigest(), "filename": "nc_" + f["filename"],
                    "t_utc": f["t_utc"] + timedelta(seconds=shift_s), "n_copies": 0, "copy_delays_s": []})
    return sorted(out, key=lambda f: (f["t_utc"], f["filename"]))


def compute() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    qc = qc_view(cfg)
    p1 = v1.load_params(cfg)
    p2 = {**p1, **{k: cfg["detectors"]["replay_v2"][k] for k in ("e1_same_counter", "e2_near_dup_d", "robust_line")}}
    hs = HashStore.load(ROOT / cfg["detectors"]["replay"]["store"])
    fs = FrameStore.load(ROOT / cfg["detectors"]["replay_v2"]["store"])
    out = {}
    for s in SAMPLES:
        d = ROOT / "data/sample" / s
        meta = read_metadata(d)
        frames, norad = stream_from_dir(d / "demoddata", qc, meta.get("norad"))
        for name, fr in (("clean", frames), ("near", near_variant(frames, qc, norad))):
            start, oid = meta.get("start_utc"), meta.get("obs_id")
            r1 = v1.detect(fr, norad, qc, p1, hs, start, oid)
            r2 = v2.detect(fr, norad, qc, p2, fs, start, oid)
            out[f"{s}/{name}"] = {"v1": [{k: r[k] for k in KEEP} for r in r1["frames"]],
                                  "v2": [{k: r[k] for k in KEEP} for r in r2["frames"]]}
    return out
