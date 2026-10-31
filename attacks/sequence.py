"""One sequence anomaly per variant (DATASET_SPEC.md 6.3). Frame level only; audio untouched.

Targets are original filenames (stable when a replay was inserted before). Subtypes:
  deletion      remove k consecutive frames; the frame after the gap is labelled
  swap          exchange the payloads of two adjacent frames (timestamps stay); both labelled
  duplicate     repeat one frame at t_new (between it and the next frame); the copy is labelled
  forged        copy of a source frame with 1-4 bytes changed outside the first 16, at t_new
                (local rhythm); labelled
  timing        shift one frame's timestamp by shift_s (+-U[3, 8] s); its name follows the new time
  counter_jump  add `jump` to one frame's counter field (bytes rewritten); labelled
"""

from __future__ import annotations

from attacks.replay import frame_name


def _label(subtype: str) -> dict:
    return {"is_attack": 1, "attack_type": "sequence", "subtype": subtype, "evasion": 0}


def apply_sequence(frames: list[dict], op: dict, target: dict, encode_counter=None, decode_counter=None) -> list[dict]:
    """Returns the new frame list (dicts copied where changed)."""
    st = op["subtype"]
    by_name = {f["filename"]: i for i, f in enumerate(frames)}
    out = [dict(f) for f in frames]
    if st == "deletion":
        out[by_name[op["label_filename"]]].update(_label(st))
        drop = set(op["filenames"])
        return [f for f in out if f["filename"] not in drop]
    if st == "swap":
        i, j = (by_name[n] for n in op["filenames"])
        out[i]["hex"], out[j]["hex"] = frames[j]["hex"], frames[i]["hex"]
        out[i].update(_label(st))
        out[j].update(_label(st))
        return out
    if st in ("duplicate", "forged"):
        src = frames[by_name[op["source_filename"]]]
        data = bytearray.fromhex(src["hex"])
        for pos, val in op.get("byte_changes", []):
            data[pos] = val
        new = {**src, "t": op["t_new"], "hex": data.hex(), **_label(st),
               "filename": frame_name(target["obs_id"], target["start"], op["t_new"], src["filename"]),
               "decoder_branches": 1, "n_copies": 0, "copy_delays_s": []}
        names = {f["filename"] for f in out}
        while new["filename"] in names:
            new["filename"] += "_"
        return out + [new]
    if st == "timing":
        f = out[by_name[op["filename"]]]
        f["t"] = f["t"] + op["shift_s"]
        names = {g["filename"] for g in out if g is not f}
        f["filename"] = frame_name(target["obs_id"], target["start"], f["t"], f["filename"])
        while f["filename"] in names:
            f["filename"] += "_"
        f.update(_label(st))
        return out
    if st == "counter_jump":
        f = out[by_name[op["filename"]]]
        data = bytes.fromhex(f["hex"])
        f["hex"] = encode_counter(data, decode_counter(data) + op["jump"]).hex()
        f.update(_label(st))
        return out
    raise ValueError(f"unknown sequence subtype {st}")
