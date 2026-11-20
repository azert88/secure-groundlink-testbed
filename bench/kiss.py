"""KISS file reader/writer for gr-satellites --kiss_out output.

gr-satellites stores a reception timestamp before each frame as a KISS control frame holding the UNIX time in
milliseconds (gr-satellites command-line docs). We assume command byte 0x09 followed by an 8-byte big-endian
integer [UNVERIFIED: check on the first real file with `python -m bench.kiss <file>`]; data frames use command 0x00.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
CMD_DATA, CMD_TIMESTAMP = 0x00, 0x09


def _unescape(b: bytes) -> bytes:
    out, esc = bytearray(), False
    for x in b:
        if esc:
            out.append(FEND if x == TFEND else FESC if x == TFESC else x)
            esc = False
        elif x == FESC:
            esc = True
        else:
            out.append(x)
    return bytes(out)


def _escape(b: bytes) -> bytes:
    return b.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))


def read_frames(raw: bytes) -> list[dict]:
    """[{'t_ms': int | None, 'data': bytes}] in file order; a timestamp applies to the next data frame."""
    frames, pending_t = [], None
    for chunk in raw.split(bytes([FEND])):
        if not chunk:
            continue
        body = _unescape(chunk)
        cmd, payload = body[0], body[1:]
        if cmd == CMD_TIMESTAMP and len(payload) == 8:
            pending_t = int.from_bytes(payload, "big")
        elif cmd & 0x0F == CMD_DATA:
            frames.append({"t_ms": pending_t, "data": payload})
            pending_t = None
    return frames


def write_frames(frames: list[dict]) -> bytes:
    """Inverse of read_frames (used by tests and by the simulated decoder)."""
    out = bytearray()
    for f in frames:
        if f.get("t_ms") is not None:
            out += bytes([FEND, CMD_TIMESTAMP]) + _escape(int(f["t_ms"]).to_bytes(8, "big")) + bytes([FEND])
        out += bytes([FEND, CMD_DATA]) + _escape(f["data"]) + bytes([FEND])
    return bytes(out)


if __name__ == "__main__":                 # quick inspection of a real gr-satellites KISS file
    fr = read_frames(open(sys.argv[1], "rb").read())
    print(f"{len(fr)} frames")
    for f in fr[:10]:
        t = datetime.fromtimestamp(f["t_ms"] / 1000, timezone.utc).isoformat() if f["t_ms"] else "no timestamp"
        print(t, len(f["data"]), f["data"][:16].hex())
