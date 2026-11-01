"""Frame input for the detectors: one recording at a time, no labels (DETECTOR_SPEC rule 4).

Two entry points that give the same canonical stream:
  stream_from_dir(demoddata dir)  raw SatNOGS demoddata files (the jury's recording); applies
                                  detect.canonical (decoder branches + rewrite merge).
  stream_from_rows(rows)          rows of a canonical frames table (data/features/<obs>/frames.csv);
                                  only columns that are functions of the raw files are read:
                                  filename, hex, decoder_branches, n_copies, copy_delays_s.
Every frame dict: filename, t_utc (datetime, 1 s resolution, from the filename), sha256, data (bytes),
counter (int or None), decoder_branches, n_copies, copy_delays_s. Sorted by (t_utc, filename).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from detect.canonical import canonical_frames
from detect.counters import counter_codec

# not anchored at the end: attack copies may carry an extra '_' after the suffix
TIME_RE = re.compile(r"_(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})")


def filename_time(name: str) -> datetime | None:
    """UTC time encoded in a demoddata filename (data_<obs>_YYYY-MM-DDTHH-MM-SS[_suffix])."""
    m = TIME_RE.search(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)


def infer_norad(datas: list[bytes], qc: dict) -> int | None:
    """Satellite from frame formats when no metadata is given: majority of decodable frames."""
    votes = {}
    for norad in (qc["funcube"]["norad"], qc["techsat"]["norad"]):
        dec, _ = counter_codec(norad, qc)
        votes[norad] = sum(dec(d) is not None for d in datas)
    best = max(votes, key=votes.get)
    return best if votes[best] > 0 else None


def _finish(files: list[dict], norad: int | None, qc: dict, merge: bool) -> list[dict]:
    dec, _ = counter_codec(norad, qc) if norad is not None else ((lambda b: None), None)
    if merge:
        delay = qc["max_rewrite_delay_s"]
        delay = delay.get(norad, 0.0) if isinstance(delay, dict) else delay
        files = canonical_frames(files, delay)
    out = []
    for f in files:
        out.append({"filename": f["filename"], "t_utc": f["t"], "sha256": f["sha256"], "data": f["data"],
                    "counter": dec(f["data"]), "decoder_branches": int(f.get("decoder_branches", 1)),
                    "n_copies": int(f.get("n_copies", 0)), "copy_delays_s": list(f.get("copy_delays_s", []))})
    out.sort(key=lambda f: (f["t_utc"], f["filename"]))
    return out


def stream_from_dir(demod_dir: Path, qc: dict, norad: int | None = None) -> tuple[list[dict], int | None]:
    """Canonical stream of a raw demoddata folder; norad inferred from the frames if not given."""
    files = []
    for p in sorted(Path(demod_dir).iterdir()):
        t = filename_time(p.name) if p.is_file() else None
        if t is None:
            continue
        data = p.read_bytes()
        files.append({"filename": p.name, "t": t, "sha256": hashlib.sha256(data).hexdigest(), "data": data})
    if norad is None:
        norad = infer_norad([f["data"] for f in files], qc)
    return _finish(files, norad, qc, merge=True), norad


def _delays(v) -> list[float]:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v == "":
        return []
    return [float(x) for x in str(v).split(";") if x != ""]


def stream_from_rows(rows: list[dict], qc: dict, norad: int | None = None) -> tuple[list[dict], int | None]:
    """Canonical stream from canonical-table rows (already merged: n_copies is taken as given)."""
    files = []
    for r in rows:
        data = bytes.fromhex(r["hex"]) if isinstance(r["hex"], str) else b""
        files.append({"filename": r["filename"], "t": filename_time(r["filename"]), "data": data,
                      "sha256": hashlib.sha256(data).hexdigest(), "decoder_branches": r.get("decoder_branches", 1),
                      "n_copies": r.get("n_copies", 0), "copy_delays_s": _delays(r.get("copy_delays_s"))})
    if norad is None:
        norad = infer_norad([f["data"] for f in files], qc)
    return _finish(files, norad, qc, merge=False), norad


def read_metadata(obs_dir: Path) -> dict:
    """Only the fields the detectors may use: satellite and pass start (no station or operator data)."""
    p = Path(obs_dir) / "metadata.json"
    if not p.exists():
        return {}
    m = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    if m.get("norad_cat_id") is not None:
        out["norad"] = int(m["norad_cat_id"])
    if m.get("start"):
        out["start_utc"] = datetime.strptime(m["start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if m.get("id") is not None:
        out["obs_id"] = int(m["id"])
    return out
