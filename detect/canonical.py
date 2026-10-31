"""Canonical frame stream: the same pre-processing for QC, table building and the detectors.

Applied to any input recording before replay detection (decision 45):
  1. Decoder branches: files with the same timestamp and the same bytes (e.g. _g0 / _g1)
     are one frame; decoder_branches counts them.
  2. Rewrites: byte-identical frames whose timestamp is at most max_rewrite_delay_s after the
     first copy are one frame (some stations write a second copy a few seconds later);
     n_copies counts the later copies and copy_delays_s holds their delays.
The first copy keeps its timestamp and filename. Frames with different bytes are never merged.
"""

from __future__ import annotations

from datetime import datetime


def _delay_s(t0, t1) -> float:
    """Seconds from t0 to t1 (datetimes or plain seconds)."""
    if isinstance(t0, datetime):
        return (t1 - t0).total_seconds()
    return float(t1 - t0)


def canonical_frames(files: list[dict], max_rewrite_delay_s: float = 10.0) -> list[dict]:
    """Merge decoder branches and rewrites.

    files: dicts with at least t (datetime or seconds), sha256 and filename; other keys are kept
    from the first copy. Returns new dicts sorted by (t, filename) with decoder_branches,
    n_copies and copy_delays_s added.
    """
    out: list[dict] = []
    last_by_sha: dict[str, dict] = {}       # most recent canonical frame for each content
    for f in sorted(files, key=lambda f: (f["t"], f["filename"])):
        prev = last_by_sha.get(f["sha256"])
        if prev is not None:
            d = _delay_s(prev["t"], f["t"])
            if d == 0:
                prev["decoder_branches"] += 1
                continue
            if d <= max_rewrite_delay_s:
                prev["n_copies"] += 1
                prev["copy_delays_s"].append(d)
                continue
        fr = {**f, "decoder_branches": 1, "n_copies": 0, "copy_delays_s": []}
        out.append(fr)
        last_by_sha[f["sha256"]] = fr
    return out
