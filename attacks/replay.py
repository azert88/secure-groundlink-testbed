"""Replay of a valid 5-20-frame subsequence (docs/DATASET_SPEC.md 6.2).

Frame level: copies of the source frames are inserted with every timestamp shifted by delta_s
(the generator chooses a later inter-burst gap), so the intra-burst spacing is preserved. Copies
get a filename of the target observation at their new time (same suffix as the source file), so
the name gives no cue. Labels: is_attack 1, attack_type replay, subtype intra_pass / cross_pass.
Audio level (when op["audio"]): the source audio around every copied frame, [t - airtime - margin,
t + airtime + margin] merged into contiguous spans, OVERWRITES the target audio at the same times +
delta_s, with a 10 ms raised-cosine crossfade at both edges of each span (file length unchanged).
For back-to-back frames (FUNCUBE-1) this is one span from the first to the last copied frame; for
sparse beacons (TECHSAT 1B, every 30 s) the copies interleave with the target's own beacons and
only the audio around each copy is replaced (decision 60). airtime_s covers a frame's on-air time
whether the timestamp marks its start or its end (decision 59).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from attacks.common import paste

NAME_RE = re.compile(r"^data_\d+_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(.*)$")


def frame_name(obs_id: int, start: datetime, t: float, template: str) -> str:
    """SatNOGS-style demoddata name of obs_id at start + t, keeping the template's suffix."""
    m = NAME_RE.match(template)
    suffix = m.group(1) if m else ""
    return f"data_{obs_id}_{(start + timedelta(seconds=t)).strftime('%Y-%m-%dT%H-%M-%S')}{suffix}"


def frame_spans(times, half: float) -> list[tuple[float, float]]:
    """Union of [t - half, t + half] over the given times, as sorted disjoint spans."""
    out = []
    for t in sorted(times):
        a, b = t - half, t + half
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def protected_bytes(norad: int, qc: dict, frame: bytes) -> set[int]:
    """Byte positions a near-copy must keep: the counter field (and, for FUNCUBE-1, byte 0 with the frame type;
    for TECHSAT 1B, the ASCII marker), so the modified frame still decodes as a valid frame with the same counter."""
    if norad == qc["funcube"]["norad"]:
        s = qc["funcube"]["seq_byte"]
        return {0, s, s + 1, s + 2}
    if norad == qc["techsat"]["norad"]:
        tc = qc["techsat"]
        c = tc["counter_byte"]
        m = frame.find(tc["marker"].encode())
        return set(range(c, c + 4)) | (set(range(m, m + len(tc["marker"]))) if m >= 0 else set())
    return set()


def near_copy(hex_str: str, norad: int, qc: dict, seed: int) -> tuple[str, int]:
    """Near-copy replay evasion (decision 79): change ONE byte outside the protected fields so the frame hash no
    longer matches its original. Returns (new hex, changed position). Deterministic for a given seed."""
    import numpy as np
    b = bytearray(bytes.fromhex(hex_str))
    keep = protected_bytes(norad, qc, bytes(b))
    pos = [i for i in range(len(b)) if i not in keep]
    rng = np.random.default_rng(seed)
    i = int(pos[rng.integers(len(pos))])
    b[i] ^= int(rng.integers(1, 256))
    return b.hex(), i


def apply_replay(frames: list[dict], audio, sr: int, op: dict, target: dict, source: dict) -> tuple[list, object, dict | None]:
    """Returns (new frame list, new audio, audio event or None).

    target / source: clean observation dicts (obs_id, start, frames, audio); source is target for intra_pass.
    """
    by_name = {f["filename"]: f for f in source["frames"]}
    src = [by_name[n] for n in op["source_filenames"]]
    lab = {"is_attack": 1, "attack_type": "replay", "subtype": op["subtype"], "evasion": int(op.get("evasion", 0))}
    names = {f["filename"] for f in frames}
    copies = []
    for f in src:
        t = f["t"] + op["delta_s"]
        name = frame_name(target["obs_id"], target["start"], t, f["filename"])
        while name in names:                     # never collides in practice (gap chosen); keep names unique
            name += "_"
        names.add(name)
        copies.append({**f, "t": t, "filename": name, **lab})
    event = None
    if op.get("audio") and audio is not None and source.get("audio") is not None:
        segs = []
        shift = int(round(op["delta_s"] * sr))
        for a, b in frame_spans([f["t"] for f in src], op["airtime_s"] + op["margin_s"]):
            i0, i1 = max(0, int(round(a * sr))), min(len(source["audio"]), int(round(b * sr)))
            d0 = i0 + shift
            if d0 < 0 or d0 + (i1 - i0) > len(audio):
                raise ValueError("replay destination outside the target audio")
            audio = paste(audio, source["audio"][i0:i1], d0, int(op["fade_ms"] / 1000 * sr))
            segs.append([d0 / sr, (d0 + i1 - i0) / sr])
        event = {"attack_type": "replay", "subtype": op["subtype"], "is_attack": 1, "evasion": lab["evasion"],
                 "segments": segs}
    return frames + copies, audio, event
