"""Jury demo of the signed radio link (mode B), SIMULATION ONLY: nothing is transmitted, no Pluto is opened.

    python -m bench.demo_link --sim

Runs a clean pass, then each attack of bench/link_attacks.py (real FUNCUBE-1 frames, per-frame HMAC envelope,
per-pass session key, 2-FSK PHY with simulated carrier offsets and noise). One line per frame:
  ACCEPT (green)   authentic, in order
  REJECT (red)     with the reason: replay / rollback / bad_tag / gap ...
  MISSING (yellow) counter skipped on the radio link (lost or jammed): reported, never silent (resync policy)
then a summary table. --no-color for plain text.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bench import link_attacks as A  # noqa: E402

TITLES = {
    "clean": "Clean pass: the satellite's frames, no attack",
    "a": "(a) Replay of a recorded EARLIER pass, after the live pass",
    "b": "(b) Replay of 3 frames INSIDE the live pass",
    "c": "(c) Forged frame: bytes changed, original tag kept",
    "d": "(d) 2 frames dropped (jammed) - radio policy resync: missing frames reported",
    "d_strict": "(d') same drop under the strict Phase 1 file rule: the rest of the pass is lost (denial of service)",
    "e": "(e) Attacker guesses the key: 3 frames sealed with a random key",
}
WHY = {"replay": "counter already used", "rollback": "session of an older pass", "bad_tag": "HMAC tag does not verify",
       "gap": "counter jump (strict rule)", "chain": "hash chain broken", "malformed": "not an envelope",
       "timestamp": "time goes backwards", "wrong_session": "other session", "phy_crc": "radio CRC error"}


class C:
    def __init__(self, on: bool):
        self.g, self.r, self.y, self.b, self.d, self.x = (("\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m")
                                                          if on else ("",) * 6)


def t_utc(ms) -> str:
    return "--:--:--" if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M:%S")


def show(res: dict, c: C) -> None:
    print(f"\n{c.b}{TITLES[res['attack']]}{c.x}")
    if res["setup"]:
        print(f"{c.d}  (earlier pass received normally before: {res['setup']['accepted']} frames accepted){c.x}")
    src = res["sources"] if len(res["sources"]) == len(res["rows"]) else ["?"] * len(res["rows"])
    for r, s in zip(res["rows"], src):
        for k in range(r.get("missing", 0)):
            print(f"  {c.y}MISSING{c.x}  counter {r['counter'] - r['missing'] + k:>3}  lost or jammed on the link (reported)")
        who = f"from {s:<9}"
        head = f"counter {'-' if r['counter'] is None else r['counter']:>3}  frame {t_utc(r['t_ms'])}  {who}"
        if r["accepted"]:
            print(f"  {c.g}ACCEPT{c.x}   {head}")
        else:
            print(f"  {c.r}REJECT{c.x}   {head}  {c.r}{r['verdict']}{c.x} ({WHY.get(r['verdict'], '')})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sim", action="store_true", required=True, help="simulation only (required)")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)
    if os.name == "nt":
        os.system("")                                  # enables ANSI colours in the Windows console
    c = C(not a.no_color)
    t0 = time.perf_counter()
    res = []
    for name in A.ATTACKS:
        r = A.run(name)
        show(r, c)
        res.append(r)
    print(f"\n{c.b}Summary (simulated channel: satellite +15 kHz, attacker -18 kHz, SNR 15 dB){c.x}")
    print(f"  {'scenario':<10} {'sent':>4} {'accepted':>8} {'missing':>7}  {'rejected (reason)':<26} result")
    for r in res:
        rej = ", ".join(f"{k} {v}" for k, v in r["rejected"].items()) or "-"
        ok = f"{c.g}as expected{c.x}" if r["as_expected"] else f"{c.r}UNEXPECTED{c.x}"
        print(f"  {r['attack']:<10} {r['sent']:>4} {r['accepted']:>8} {r['missing']:>7}  {rej:<26} {ok}")
    print(f"{c.d}  {time.perf_counter() - t0:.1f} s, nothing transmitted{c.x}")
    return 0 if all(r["as_expected"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
