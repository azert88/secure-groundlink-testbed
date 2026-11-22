"""IQ recordings in a minimal SigMF layout: <base>.sigmf-data (ci16_le, interleaved) + <base>.sigmf-meta (JSON).

Only the fields we use are written (SigMF core: datatype, sample_rate, frequency, datetime, description, hw);
extra bench fields go under the "bench:" namespace. Reading is chunked (np.memmap) to keep memory flat on a Pi.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def paths(base: str | Path) -> tuple[Path, Path]:
    base = Path(base)
    return base.with_suffix(".sigmf-data"), base.with_suffix(".sigmf-meta")


def write_meta(base: str | Path, sample_rate: float, center_hz: float, start: datetime | None = None,
               description: str = "", hw: str = "", extra: dict | None = None) -> Path:
    data, meta = paths(base)
    start = start or datetime.now(timezone.utc)
    doc = {
        "global": {"core:datatype": "ci16_le", "core:sample_rate": float(sample_rate), "core:version": "1.0.0",
                   "core:description": description, "core:hw": hw},
        "captures": [{"core:sample_start": 0, "core:frequency": float(center_hz),
                      "core:datetime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}],
        "annotations": [],
    }
    for k, v in (extra or {}).items():
        doc["global"][f"bench:{k}"] = v
    if data.exists():
        h = hashlib.sha512()                       # chunked: a 320 s capture is ~0.7 GB, too much RAM on a Pi
        with open(data, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        doc["global"]["core:sha512"] = h.hexdigest()
    meta.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return meta


def read_meta(base: str | Path) -> dict:
    return json.loads(paths(base)[1].read_text(encoding="utf-8"))


def start_time(meta: dict) -> datetime:
    s = meta["captures"][0]["core:datetime"].rstrip("Z")
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def open_cs16(base: str | Path) -> np.ndarray:
    """Interleaved int16 view of the data file (memory-mapped, read-only)."""
    return np.memmap(paths(base)[0], dtype="<i2", mode="r")
