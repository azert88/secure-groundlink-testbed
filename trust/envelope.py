"""Phase M: per-frame trust envelope (prevention layer of the emulated ground-station trust anchor).

The station wraps every frame it receives in an envelope before handing it on (archive, network, mission control):

  header  magic "TSE1" (4) | session id (16) | counter u64 (8) | timestamp ms u64 (8) | prev hash (32) | length u16 (2)
  body    the frame bytes
  tag     HMAC-SHA256(session key, header || body) (32)

  counter    monotonic session counter, +1 per envelope (starts at 0)
  prev hash  SHA-256 of the previous complete envelope (32 zero bytes for the first): a hash chain
  overhead   102 bytes per frame (70 header + 32 tag)

The verifier is strict and in order: an envelope is accepted only if its tag verifies, its session id is the session's,
its counter is exactly last + 1, its prev hash is the hash of the last accepted envelope and its timestamp does not
go backwards. A rejected envelope does not change the verifier state, so every rejection is deterministic:
  modification / forgery -> bad_tag      replay or old copy -> replay (counter <= last)
  deletion               -> gap          reorder            -> gap (early frame), then the late frame is accepted
  other session          -> wrong_session  chain splice     -> chain (valid tag and counter, wrong prev hash)
Frame losses between satellite and station happen BEFORE the envelope and are the detectors' job; after the
envelope the transport must be lossless, so a gap is an attack (or a transport fault) and is rejected.

CLI:
  python trust/envelope.py bench --n 20000 --out eval/out/trust/overhead.json
  python trust/envelope.py seal --demod-dir <dir> --key keys/private/session.key --out stream.bin
  python trust/envelope.py verify --stream stream.bin --key keys/private/session.key
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"TSE1"
HEADER = struct.Struct(">4s16sQQ32sH")          # 70 bytes
TAG_LEN = 32
OVERHEAD = HEADER.size + TAG_LEN                 # 102 bytes
ZERO = bytes(32)


def new_session() -> tuple[bytes, bytes]:
    """(session id, session key), 16 and 32 random bytes."""
    return os.urandom(16), os.urandom(32)


def _tag(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


@dataclass
class Sealer:
    session_id: bytes
    key: bytes
    counter: int = 0
    prev: bytes = ZERO

    def seal(self, frame: bytes, t_ms: int | None = None) -> bytes:
        if len(frame) > 0xFFFF:
            raise ValueError("frame longer than 65535 bytes")
        t_ms = int(time.time() * 1000) if t_ms is None else int(t_ms)
        head = HEADER.pack(MAGIC, self.session_id, self.counter, t_ms, self.prev, len(frame))
        env = head + frame + _tag(self.key, head + frame)
        self.counter += 1
        self.prev = hashlib.sha256(env).digest()
        return env


@dataclass
class Verifier:
    session_id: bytes
    key: bytes
    last: int = -1
    prev: bytes = ZERO
    last_t: int = -1
    log: list = field(default_factory=list)

    def verify(self, env: bytes) -> tuple[bool, str, bytes | None]:
        """(accepted, reason, frame). Rejected envelopes leave the state unchanged."""
        if len(env) < OVERHEAD:
            return self._out(False, "malformed", None)
        magic, sid, ctr, t_ms, prev, n = HEADER.unpack(env[:HEADER.size])
        if magic != MAGIC or len(env) != OVERHEAD + n:
            return self._out(False, "malformed", None)
        body, tag = env[HEADER.size:HEADER.size + n], env[HEADER.size + n:]
        if not hmac.compare_digest(tag, _tag(self.key, env[:HEADER.size + n])):
            return self._out(False, "bad_tag", None)
        if not hmac.compare_digest(sid, self.session_id):
            return self._out(False, "wrong_session", None)
        if ctr <= self.last:
            return self._out(False, "replay", None)
        if ctr != self.last + 1:
            return self._out(False, "gap", None)
        if not hmac.compare_digest(prev, self.prev):
            return self._out(False, "chain", None)
        if t_ms < self.last_t:
            return self._out(False, "timestamp", None)
        self.last, self.prev, self.last_t = ctr, hashlib.sha256(env).digest(), t_ms
        return self._out(True, "ok", body)

    def _out(self, ok: bool, reason: str, body):
        self.log.append(reason)
        return ok, reason, body


# ------------------------------------------------------------------------------------------- stream file
def write_stream(path: Path, envs: list[bytes]) -> None:
    with open(path, "wb") as fh:
        for e in envs:
            fh.write(struct.pack(">I", len(e)) + e)


def read_stream(path: Path) -> list[bytes]:
    data, out, i = Path(path).read_bytes(), [], 0
    while i < len(data):
        (n,) = struct.unpack(">I", data[i:i + 4])
        out.append(data[i + 4:i + 4 + n])
        i += 4 + n
    return out


# ------------------------------------------------------------------------------------------- overhead
def bench(n: int = 20000, sizes: tuple[int, ...] = (44, 256), seed: int = 0) -> dict:
    """Microseconds per seal / verify and bytes per frame (frame sizes: TECHSAT 1B beacon 44 B, FUNCUBE-1 256 B)."""
    import random
    rnd = random.Random(seed)
    out = {"overhead_bytes_per_frame": OVERHEAD, "header_bytes": HEADER.size, "tag_bytes": TAG_LEN, "n": n,
           "python": sys.version.split()[0], "per_frame_size": {}}
    for size in sizes:
        frames = [bytes(rnd.getrandbits(8) for _ in range(size)) for _ in range(min(n, 256))]
        sid, key = new_session()
        s = Sealer(sid, key)
        t = time.perf_counter_ns()
        envs = [s.seal(frames[i % len(frames)], i) for i in range(n)]
        t_seal = (time.perf_counter_ns() - t) / n / 1000
        v = Verifier(sid, key)
        t = time.perf_counter_ns()
        ok = all(v.verify(e)[0] for e in envs)
        t_ver = (time.perf_counter_ns() - t) / n / 1000
        out["per_frame_size"][str(size)] = {"seal_us": round(t_seal, 2), "verify_us": round(t_ver, 2), "all_ok": ok,
                                            "overhead_pct": round(100 * OVERHEAD / size, 1)}
    return out


def bench_manifest(n: int = 200) -> dict:
    """Ed25519 sign / verify of a model manifest (trust/manifest.py), microseconds per operation."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    msg = json.dumps({"files": {f"f{i}": "0" * 64 for i in range(8)}}, sort_keys=True).encode()
    t = time.perf_counter_ns()
    sigs = [k.sign(msg) for _ in range(n)]
    t_s = (time.perf_counter_ns() - t) / n / 1000
    pub = k.public_key()
    t = time.perf_counter_ns()
    for s in sigs:
        pub.verify(s, msg)
    t_v = (time.perf_counter_ns() - t) / n / 1000
    return {"ed25519_sign_us": round(t_s, 1), "ed25519_verify_us": round(t_v, 1), "signature_bytes": 64}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-frame HMAC trust envelope")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench")
    b.add_argument("--n", type=int, default=20000)
    b.add_argument("--out", default=None)
    s = sub.add_parser("seal")
    s.add_argument("--demod-dir", required=True)
    s.add_argument("--key", required=True, help="session file (48 bytes: id + key); created if missing")
    s.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--stream", required=True)
    v.add_argument("--key", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "bench":
        res = {**bench(a.n), **bench_manifest()}
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(res, indent=1))
        return 0
    kp = Path(a.key)
    if a.cmd == "seal":
        if not kp.exists():
            kp.parent.mkdir(parents=True, exist_ok=True)
            sid, key = new_session()
            kp.write_bytes(sid + key)
        raw = kp.read_bytes()
        sealer = Sealer(raw[:16], raw[16:])
        files = sorted(p for p in Path(a.demod_dir).iterdir() if p.is_file())
        envs = [sealer.seal(p.read_bytes()) for p in files]
        write_stream(Path(a.out), envs)
        print(json.dumps({"frames": len(envs), "bytes": sum(map(len, envs)), "overhead_bytes": OVERHEAD * len(envs)}))
        return 0
    raw = kp.read_bytes()
    ver = Verifier(raw[:16], raw[16:])
    res = [ver.verify(e)[1] for e in read_stream(Path(a.stream))]
    print(json.dumps({"n": len(res), "accepted": res.count("ok"),
                      "rejected": {r: res.count(r) for r in sorted(set(res)) if r != "ok"}}))
    return 0 if all(r == "ok" for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
