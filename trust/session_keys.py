"""Session key management for the per-frame envelope (Phase 2, task S8; weakness W14).

Phase 1 used one random session key shared out of band. Phase 2 derives a fresh key per pass:

  master key  K_m   32 random bytes, provisioned once into the satellite emulator and the station
                    (file keys/private/master.key, never committed; production: HSM / secure element)
  session id  sid   16 bytes = 8-byte big-endian epoch (pass number, strictly increasing) || 8 random bytes
  session key K_s = HKDF-SHA256(ikm=K_m, salt=sid, info="tsyp14-groundlink/v1/hmac" || NORAD (4 B))   (RFC 5869)

Both sides derive K_s from sid; only sid travels on the link (in every envelope header). Rotation is per pass,
so a key leak exposes one pass, and a captured pass cannot be replayed into a later one (the station refuses
any sid whose epoch is not above the last accepted epoch: anti-rollback state on disk, written atomically).
Compared with CCSDS SDLS (CCSDS 355.0-B, Space Data Link Security), which is the reference design for space links
[mapping UNVERIFIED against the standard text]: SDLS carries a Security Parameter Index and an anti-replay sequence
number per Security Association, with AES-GCM / CMAC and keys managed by an SDLS key-management service; our
sid ~ SPI + epoch, our envelope counter ~ anti-replay sequence number, HMAC-SHA256 instead of AES-GCM/CMAC.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO = b"tsyp14-groundlink/v1/hmac"


class RollbackError(RuntimeError):
    pass


def new_master_key() -> bytes:
    return secrets.token_bytes(32)


def make_session_id(epoch: int) -> bytes:
    if not 0 <= epoch < 2 ** 64:
        raise ValueError("epoch out of range")
    return epoch.to_bytes(8, "big") + secrets.token_bytes(8)


def epoch_of(sid: bytes) -> int:
    if len(sid) != 16:
        raise ValueError("session id must be 16 bytes")
    return int.from_bytes(sid[:8], "big")


def derive_session_key(master: bytes, sid: bytes, norad: int) -> bytes:
    if len(master) != 32:
        raise ValueError("master key must be 32 bytes")
    epoch_of(sid)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=sid,
                info=INFO + int(norad).to_bytes(4, "big")).derive(master)


class EpochState:
    """Persistent anti-rollback state of one side (last epoch used / accepted), atomic writes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def last(self) -> int:
        if not self.path.exists():
            return -1
        return int(json.loads(self.path.read_text(encoding="utf-8"))["last_epoch"])

    def _store(self, epoch: int) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_epoch": epoch}), encoding="utf-8")
        os.replace(tmp, self.path)

    def next_session(self, master: bytes, norad: int) -> tuple[bytes, bytes]:
        """Sender side: new (sid, key) with epoch = last + 1, recorded before use."""
        epoch = self.last() + 1
        self._store(epoch)
        sid = make_session_id(epoch)
        return sid, derive_session_key(master, sid, norad)

    def accept(self, master: bytes, sid: bytes, norad: int) -> bytes:
        """Receiver side: key for sid if its epoch is new; raises RollbackError for an old or repeated epoch.

        Call only after the first envelope of the session verified (the epoch is then authenticated).
        """
        e = epoch_of(sid)
        if e <= self.last():
            raise RollbackError(f"session epoch {e} <= last accepted {self.last()}: replayed or rolled-back session")
        key = derive_session_key(master, sid, norad)
        return key

    def commit(self, sid: bytes) -> None:
        self._store(epoch_of(sid))
