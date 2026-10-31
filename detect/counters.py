"""Per-frame counters (decisions 31 and 39), shared by QC, tables, attacks and detectors.

FUNCUBE-1: counter = seqnumber * cycle + frame type; seqnumber = 24-bit big-endian at bytes
  seq_byte..seq_byte+2, frame type = low 6 bits of byte 0, satellite id = top 2 bits of byte 0.
TECHSAT 1B: uint32 little-endian seconds counter at bytes counter_byte..+3 of a frame_len-byte
  beacon that contains the ASCII marker.
"""

from __future__ import annotations


def funcube_counter(frame: bytes, fc: dict) -> int | None:
    """Per-frame counter seqnumber * cycle + frame type, or None if the frame does not fit."""
    if len(frame) != fc["frame_len"] or frame[0] >> 6 != fc["satid"]:
        return None
    ftype = frame[0] & 0x3F
    if ftype >= fc["cycle"]:
        return None
    seq = int.from_bytes(frame[fc["seq_byte"]:fc["seq_byte"] + 3], "big")
    return seq * fc["cycle"] + ftype


def techsat_counter(frame: bytes, tc: dict) -> int | None:
    """Seconds counter of a TECHSAT 1B beacon (frame_len bytes + ASCII marker), or None if not a beacon."""
    if len(frame) != tc["frame_len"] or tc["marker"].encode() not in frame:
        return None
    return int.from_bytes(frame[tc["counter_byte"]:tc["counter_byte"] + 4], "little")


def funcube_set_counter(frame: bytes, value: int, fc: dict) -> bytes:
    """Frame with its counter fields rewritten so that funcube_counter(...) == value."""
    seq, ftype = divmod(value, fc["cycle"])
    b = bytearray(frame)
    b[0] = (b[0] & 0xC0) | ftype
    b[fc["seq_byte"]:fc["seq_byte"] + 3] = (seq % (1 << 24)).to_bytes(3, "big")
    return bytes(b)


def techsat_set_counter(frame: bytes, value: int, tc: dict) -> bytes:
    b = bytearray(frame)
    b[tc["counter_byte"]:tc["counter_byte"] + 4] = (value % (1 << 32)).to_bytes(4, "little")
    return bytes(b)


def counter_codec(norad: int, qc: dict):
    """(decode(bytes) -> int | None, encode(bytes, int) -> bytes) for a satellite, or (None-decoder, None)."""
    if norad == qc["funcube"]["norad"]:
        fc = qc["funcube"]
        return (lambda b: funcube_counter(b, fc)), (lambda b, v: funcube_set_counter(b, v, fc))
    if norad == qc["techsat"]["norad"]:
        tc = qc["techsat"]
        return (lambda b: techsat_counter(b, tc)), (lambda b, v: techsat_set_counter(b, v, tc))
    return (lambda b: None), None
