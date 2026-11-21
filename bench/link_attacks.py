"""Mode B attacks on the signed link, SIMULATION ONLY (no hardware, nothing transmitted).

Each scenario runs real FUNCUBE-1 frames through bench/link_signed (envelope + per-pass key) and the 2-FSK PHY
(bench/link_phy) with a simulated channel: the legitimate transmitter at +15 kHz carrier offset, the attacker's
transmitter at -18 kHz, in-band SNR 15 dB. Fresh random master key and epoch state per scenario.

  clean  live pass, no attack                                                  -> 0 rejections
  a      recorded earlier pass (epoch 0, accepted before) replayed after the live pass (epoch 1) -> rollback
         (replayed while the receiver is still in the earlier session, the same frames are rejected as replay)
  b      3 frames of the live pass replayed inside the pass                    -> replay
  c      one forged frame (frame bytes changed, original tag kept) injected     -> bad_tag
  d      2 frames of the live pass dropped (jammed / deleted)                   -> gap (strict: every later frame)
  d_resync  same with --gap-policy resync                                       -> ok_after_gap, 2 frames missing
  e      attacker key guess: 3 frames sealed with the live session id and a random key -> bad_tag

  python -m bench.link_attacks --all --sim
  python -m bench.link_attacks --attack b --sim --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bench import link_phy as PHY  # noqa: E402
from bench import link_signed as LS  # noqa: E402
from trust.envelope import HEADER, Sealer  # noqa: E402
from trust.session_keys import EpochState  # noqa: E402

LIVE_OBS, EARLIER_OBS = 14934136, 14932721       # two consecutive FUNCUBE-1 passes of station 2107 (Phase 1 dev)
CFO_SAT, CFO_ATK, SNR_DB = 15000.0, -18000.0, 15.0
EXPECTED = {"clean": {}, "a": {"rollback": 16}, "b": {"replay": 3}, "c": {"bad_tag": 1}, "d": {"gap": 7},
            "d_resync": {}, "e": {"bad_tag": 3}}
ATTACKS = tuple(EXPECTED)


def forge(env: bytes) -> bytes:
    """Change one byte of the frame body, keep header and tag."""
    i = HEADER.size + 10
    return env[:i] + bytes([env[i] ^ 0x5A]) + env[i + 1:]


def run(attack: str, seed: int = 0, snr_db: float = SNR_DB) -> dict:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        master = os.urandom(32)
        sender = LS.Sender(master, EpochState(d / "sender.json"))
        rx = LS.Receiver(master, EpochState(d / "receiver.json"), gap_policy="resync" if attack == "d_resync" else "strict")
        setup = []
        if attack == "a":
            earlier = sender.seal_pass(LS.load_frames(EARLIER_OBS))            # epoch 0, received normally earlier
            setup = LS.receive_iq(rx, PHY.demodulate(LS.bursts_iq([(e, CFO_SAT) for e in earlier], snr_db, seed + 100)))
        live = sender.seal_pass(LS.load_frames(LIVE_OBS))
        sat = [(e, CFO_SAT) for e in live]
        if attack == "a":
            bursts = sat + [(e, CFO_ATK) for e in earlier]           # replayed once the live session runs
        elif attack == "b":
            bursts = sat[:10] + [(live[i], CFO_ATK) for i in (4, 5, 6)] + sat[10:]
        elif attack == "c":
            bursts = sat[:8] + [(forge(live[8]), CFO_ATK)] + sat[8:]
        elif attack in ("d", "d_resync"):
            bursts = sat[:6] + sat[8:]
        elif attack == "e":
            sid = HEADER.unpack(live[0][:HEADER.size])[1]
            fake = Sealer(sid, os.urandom(32), counter=6, prev=HEADER.unpack(live[6][:HEADER.size])[4])
            t0 = HEADER.unpack(live[5][:HEADER.size])[3]
            bursts = sat[:6] + [(fake.seal(b"\x00" * 256, t0 + i), CFO_ATK) for i in range(3)] + sat[6:]
        else:
            bursts = sat
        rows = LS.receive_iq(rx, PHY.demodulate(LS.bursts_iq(bursts, snr_db, seed)))
    s = LS.summary(rows)
    return {"attack": attack, "sent": len(bursts), "setup": LS.summary(setup) if setup else None, "rows": rows,
            **s, "expected_rejections": EXPECTED[attack], "as_expected": s["rejected"] == EXPECTED[attack]
            and s["received"] == len(bursts)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--attack", choices=ATTACKS)
    ap.add_argument("--sim", action="store_true", required=True, help="simulation only (required: nothing is transmitted)")
    ap.add_argument("--verbose", action="store_true", help="print every receiver verdict")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    res = [run(x, a.seed) for x in (ATTACKS if a.all else [a.attack])]
    for r in res:
        print(f"\n[{r['attack']}]" + (f"  setup (earlier pass): {r['setup']}" if r["setup"] else ""))
        for row in r["rows"]:
            if a.verbose or row["verdict"] != "ok":
                print(LS.fmt_row(row))
    print("\n| attack | frames sent | received | accepted | rejected by reason | as expected |")
    print("| --- | --- | --- | --- | --- | --- |")
    for r in res:
        rej = ", ".join(f"{k} {v}" for k, v in r["rejected"].items()) or "-"
        print(f"| {r['attack']} | {r['sent']} | {r['received']} | {r['accepted']} | {rej} | {r['as_expected']} |")
    return 0 if all(r["as_expected"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
