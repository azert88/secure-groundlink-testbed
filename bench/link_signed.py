"""Mode B of the bench: a signed link. Real FUNCUBE-1 frames, each sealed in the per-frame HMAC envelope
(trust/envelope.py) with a per-pass session key (trust/session_keys.py), carried by the 2-FSK PHY (bench/link_phy.py).

Sender   per pass: (sid, K_s) = EpochState.next_session(master, NORAD); Sealer(sid, K_s).seal(frame, t_ms) per frame.
Receiver per burst: PHY CRC -> envelope header -> session:
  - envelope of the current session: Verifier.verify (ok / replay / gap / bad_tag / chain / timestamp / malformed);
  - envelope of another session id: K = HKDF(master, sid, NORAD); tag checked (bad_tag); an authentic envelope of an
    epoch <= the last accepted one is a replayed / rolled-back session (rollback, EpochState.accept); a new epoch
    must start at counter 0 (else gap); it then becomes the current session (EpochState.commit).
  - PHY CRC failure: phy_crc (frame lost on the link, before the envelope).
  gap policy on the radio link: "resync" (DEFAULT, decision P2-28): an authentic envelope with a forward counter jump
  is accepted as ok_after_gap, the skipped counters are reported as missing (never silently), the chain restarts from
  its header; replay / rollback / bad_tag rules are unchanged. "strict" (--gap-policy strict) = the Phase 1 envelope
  rule, still the default for files (trust/envelope.py unchanged): after a missing envelope every later one is
  rejected as a gap, so one frame lost or jammed on the radio would reject the rest of the pass (denial of service).
Master key: keys/private/master.key (32 random bytes, created if missing, git-ignored, never printed).
Frames: --obs (default 14932721, a FUNCUBE-1 pass) from data/tables/frames.csv (clean variant, hex, SHA-256 checked;
data/sample/14932721 has no demoddata) or --demod-dir (one file per frame, sorted by name, timestamps = file order).

  python -m bench.link_signed --sim [--cfo-hz 15000 --snr-db 15]                 # no hardware
  python -m bench.link_signed --write-tx-iq out/bench/link_tx                    # IQ file for bench.tx_play (operator)
  python -m bench.link_signed --read-rx-iq out/bench/link_rx                     # capture from bench.rx_capture
This module never opens a Pluto.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bench import link_phy as PHY  # noqa: E402
from trust.envelope import HEADER, MAGIC, OVERHEAD, Sealer, Verifier  # noqa: E402
from trust.session_keys import EpochState, RollbackError, derive_session_key, epoch_of, new_master_key  # noqa: E402

NORAD = 39444
DEFAULT_OBS = 14932721
MASTER = Path("keys/private/master.key")
FRAMES_CSV = Path("data/tables/frames.csv")
VERDICTS = ("ok", "ok_after_gap", "replay", "gap", "bad_tag", "wrong_session", "chain", "timestamp", "malformed",
            "rollback", "phy_crc")


# ------------------------------------------------------------------------------------------- inputs
def load_frames(obs_id: int = DEFAULT_OBS, demod_dir: Path | None = None, table: Path = FRAMES_CSV) -> list[tuple[int, bytes]]:
    """[(t_ms, frame bytes)] of one real pass, in reception order."""
    if demod_dir is not None:
        files = sorted(p for p in Path(demod_dir).iterdir() if p.is_file())
        return [(i, p.read_bytes()) for i, p in enumerate(files)]
    out = []
    with open(table, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["obs_id"] == str(obs_id) and r["variant_id"] == "clean":
                b = bytes.fromhex(r["hex"])
                if hashlib.sha256(b).hexdigest() != r["sha256"]:
                    raise ValueError(f"frame {r['idx']} of {obs_id}: SHA-256 mismatch")
                t = datetime.strptime(r["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                out.append((int(r["idx"]), int(t.timestamp() * 1000), b))
    if not out:
        raise ValueError(f"no clean frames of obs {obs_id} in {table}")
    return [(t, b) for _, t, b in sorted(out)]


def load_or_create_master(path: Path = MASTER) -> bytes:
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new_master_key())
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError(f"{path}: master key must be 32 bytes")
    return key


# ------------------------------------------------------------------------------------------- sender / receiver
class Sender:
    def __init__(self, master: bytes, state: EpochState, norad: int = NORAD):
        self.master, self.state, self.norad = master, state, norad

    def seal_pass(self, frames: list[tuple[int, bytes]]) -> list[bytes]:
        sid, key = self.state.next_session(self.master, self.norad)
        s = Sealer(sid, key)
        return [s.seal(f, t) for t, f in frames]


def parse(env: bytes):
    if len(env) < OVERHEAD:
        return None
    h = HEADER.unpack(env[:HEADER.size])
    return h if h[0] == MAGIC and len(env) == OVERHEAD + h[5] else None


class Receiver:
    def __init__(self, master: bytes, state: EpochState, norad: int = NORAD, gap_policy: str = "resync"):
        if gap_policy not in ("strict", "resync"):
            raise ValueError("gap_policy is strict or resync")
        self.master, self.state, self.norad, self.gap_policy = master, state, norad, gap_policy
        self.sid = self.key = self.ver = None

    def receive(self, env: bytes) -> dict:
        h = parse(env)
        if h is None:
            return {"verdict": "malformed", "accepted": False, "counter": None, "t_ms": None, "epoch": None}
        _, sid, ctr, t_ms, prev, _ = h
        info = {"counter": ctr, "t_ms": t_ms, "epoch": epoch_of(sid)}
        if self.ver is not None and sid == self.sid:
            last = self.ver.last
            ok, reason, _ = self.ver.verify(env)
            if reason == "gap" and self.gap_policy == "resync":
                v = Verifier(sid, self.key, last=ctr - 1, prev=prev, last_t=self.ver.last_t)
                if v.verify(env)[0]:
                    self.ver = v
                    return {"verdict": "ok_after_gap", "accepted": True, "missing": ctr - last - 1, **info}
            return {"verdict": reason, "accepted": ok, **info}
        key = derive_session_key(self.master, sid, self.norad)
        v = Verifier(sid, key, last=ctr - 1, prev=prev)            # checks the tag (and format) only
        ok, reason, _ = v.verify(env)
        if not ok:
            return {"verdict": reason, "accepted": False, **info}
        try:
            self.state.accept(self.master, sid, self.norad)
        except RollbackError:
            return {"verdict": "rollback", "accepted": False, **info}
        if ctr != 0 and self.gap_policy == "strict":
            return {"verdict": "gap", "accepted": False, **info}
        self.state.commit(sid)
        self.sid, self.key, self.ver = sid, key, v
        if ctr == 0:
            return {"verdict": "ok", "accepted": True, **info}
        return {"verdict": "ok_after_gap", "accepted": True, "missing": ctr, **info}


# ------------------------------------------------------------------------------------------- link
def bursts_iq(bursts: list[tuple[bytes, float]], snr_db: float | None, seed: int = 0, fs: int = PHY.FS) -> np.ndarray:
    """Simulated air: each burst modulated with its own transmitter's carrier offset, then AWGN at the in-band SNR."""
    parts = []
    for payload, cfo in bursts:
        x = PHY.modulate([payload], fs, gap_s=0.01, lead_s=0.01)
        parts.append(x * np.exp(1j * 2 * np.pi * cfo * np.arange(len(x)) / fs).astype(np.complex64))
    return PHY.channel(np.concatenate(parts), fs, 0.0, snr_db, seed)


def receive_iq(rx: Receiver, detections: list[tuple], fs: int = PHY.FS) -> list[dict]:
    rows = []
    for idx, payload, crc_ok in detections:
        r = rx.receive(payload) if crc_ok else {"verdict": "phy_crc", "accepted": False, "counter": None, "t_ms": None,
                                                "epoch": None}
        rows.append({"rx_s": round(idx / fs, 4), **r})
    return rows


def fmt_row(r: dict) -> str:
    t = "-" if r.get("t_ms") is None else datetime.fromtimestamp(r["t_ms"] / 1000, timezone.utc).strftime("%H:%M:%S")
    extra = f" (missing {r['missing']})" if "missing" in r else ""
    return (f"  rx {r['rx_s']:8.3f} s  epoch {'-' if r['epoch'] is None else r['epoch']:>3}  counter "
            f"{'-' if r['counter'] is None else r['counter']:>4}  frame time {t}  {r['verdict']}{extra}")


def summary(rows: list[dict]) -> dict:
    c = Counter(r["verdict"] for r in rows)
    acc = sum(r["accepted"] for r in rows)
    return {"received": len(rows), "accepted": acc, "missing": sum(r.get("missing", 0) for r in rows),
            "rejected": {k: c[k] for k in VERDICTS if c[k] and k not in ("ok", "ok_after_gap")}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mode B signed link (envelope + per-pass keys over a 2-FSK PHY)")
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--sim", action="store_true", help="no hardware: simulated channel (carrier offset + noise)")
    m.add_argument("--write-tx-iq", default=None, help="IQ base path to write (SigMF cs16) for bench.tx_play")
    m.add_argument("--read-rx-iq", default=None, help="IQ base path of a bench.rx_capture recording")
    ap.add_argument("--obs", type=int, default=DEFAULT_OBS)
    ap.add_argument("--demod-dir", default=None)
    ap.add_argument("--norad", type=int, default=NORAD)
    ap.add_argument("--master-key", default=str(MASTER))
    ap.add_argument("--state-dir", default=None, help="epoch state files (default: temp dir for --sim, "
                                                      "out/bench_link/state otherwise)")
    ap.add_argument("--gap-policy", choices=["strict", "resync"], default="resync",
                    help="radio link default resync (missing frames reported); strict = Phase 1 file rule")
    ap.add_argument("--cfo-hz", type=float, default=15000.0)
    ap.add_argument("--snr-db", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--config", default=None, help="bench config (default bench/config.yaml)")
    a = ap.parse_args(argv)
    from bench.safety import DEFAULT_CONFIG, load_config
    cfg = load_config(a.config or DEFAULT_CONFIG)
    fs = int(cfg["rf"]["sample_rate"])
    master = load_or_create_master(Path(a.master_key))
    tmp = tempfile.TemporaryDirectory() if (a.sim and a.state_dir is None) else None
    sdir = Path(tmp.name if tmp else (a.state_dir or "out/bench_link/state"))
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        if a.sim or a.write_tx_iq:
            frames = load_frames(a.obs, Path(a.demod_dir) if a.demod_dir else None)
            envs = Sender(master, EpochState(sdir / "sender_epoch.json"), a.norad).seal_pass(frames)
            sizes = [len(f) for _, f in frames]
            stats = {"frames": len(envs), "frame_bytes": sum(sizes), "envelope_overhead_bytes": OVERHEAD * len(envs),
                     "envelope_overhead_pct": round(100 * OVERHEAD * len(envs) / sum(sizes), 1),
                     "phy_overhead_bytes_per_burst": PHY.PHY_OVERHEAD,
                     "airtime_s": round(sum(8 * (len(e) + PHY.PHY_OVERHEAD) for e in envs) / PHY.BIT_RATE, 3)}
        if a.write_tx_iq:
            from bench import dsp, iqfile
            iq = PHY.modulate(envs, fs)
            full = float(cfg["rf"]["pluto_full_scale"])
            data, _ = iqfile.paths(a.write_tx_iq)
            data.parent.mkdir(parents=True, exist_ok=True)
            dsp.to_cs16(iq, full).tofile(data)
            iqfile.write_meta(a.write_tx_iq, fs, cfg["rf"]["center_hz"], description="mode B signed link TX (2-FSK)",
                              extra={"full_scale": full, "link_if_hz": PHY.IF_HZ, "obs_id": a.obs,
                                     "session_epoch": epoch_of(parse(envs[0])[1]), **stats})
            print(json.dumps({"written": str(data), "seconds": round(len(iq) / fs, 2), **stats}, indent=1))
            return 0
        rx = Receiver(master, EpochState(sdir / "receiver_epoch.json"), a.norad, a.gap_policy)
        if a.sim:
            iq = bursts_iq([(e, a.cfo_hz) for e in envs], a.snr_db, a.seed, fs)
            rows = receive_iq(rx, PHY.demodulate(iq, fs), fs)
        else:
            from bench import dsp, iqfile
            meta = iqfile.read_meta(a.read_rx_iq)
            if float(meta["global"]["core:sample_rate"]) != fs:
                raise SystemExit("IQ sample rate differs from bench/config.yaml")
            full = float(meta["global"].get("bench:full_scale", cfg["rf"]["pluto_full_scale"]))
            raw = iqfile.open_cs16(a.read_rx_iq)
            det = PHY.demodulate_blocks(lambda i0, i1: dsp.from_cs16(raw[2 * i0:2 * i1], full).astype(np.complex64),
                                        len(raw) // 2, fs)
            rows = receive_iq(rx, det, fs)
        for r in rows:
            print(fmt_row(r))
        out = summary(rows)
        if a.sim:
            out = {**out, "sent": len(envs), **stats, "cfo_hz": a.cfo_hz, "snr_db_inband": a.snr_db}
        print(json.dumps(out, indent=1))
        return 0
    finally:
        if tmp:
            tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
