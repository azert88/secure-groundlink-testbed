"""Transmit an IQ file from a Pluto, CONDUCTED ONLY. The operator runs this; never an assistant or a scheduler.

    python -m bench.tx_play --device satellite --iq out/bench/tx_14932721
    python -m bench.tx_play --device attacker  --iq out/bench/attack_tone --cyclic --seconds 60

Refuses to start unless bench.safety accepts the TX gain and the declared attenuation (bench/config.yaml).
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from bench import iqfile
from bench.pluto import TX_FULL_SCALE, configure_tx, open_pluto
from bench.safety import add_tx_arguments, guard_from_args


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", required=True, choices=["satellite", "attacker"])
    ap.add_argument("--iq", required=True, help="IQ base path (no extension)")
    ap.add_argument("--cyclic", action="store_true", help="repeat the first buffer (short signals: tone, noise)")
    ap.add_argument("--seconds", type=float, default=None, help="with --cyclic: how long to transmit")
    add_tx_arguments(ap)
    args = ap.parse_args()
    cfg, gain = guard_from_args(args)                          # raises SafetyError on an unsafe request
    meta = iqfile.read_meta(args.iq)
    if float(meta["global"]["core:sample_rate"]) != float(cfg["rf"]["sample_rate"]):
        raise SystemExit("IQ sample rate differs from bench/config.yaml")
    file_scale = float(meta["global"].get("bench:full_scale", cfg["rf"]["pluto_full_scale"]))
    raw = iqfile.open_cs16(args.iq)
    sdr = open_pluto(cfg["devices"][args.device])
    configure_tx(sdr, cfg, gain)
    block = int(cfg["rf"]["sample_rate"]) * 2                  # 1 s of interleaved int16
    print(f"TX start {time.strftime('%H:%M:%S')} ({len(raw) // 2 / cfg['rf']['sample_rate']:.0f} s in file)")
    try:
        if args.cyclic:
            sdr.tx_cyclic_buffer = True
            x = raw[:block].astype(np.float64)
            sdr.tx((x[0::2] + 1j * x[1::2]) * (TX_FULL_SCALE / file_scale))
            time.sleep(args.seconds or 10)
        else:
            sdr.tx_cyclic_buffer = False
            for i in range(0, len(raw), block):
                x = raw[i:i + block].astype(np.float64)
                sdr.tx((x[0::2] + 1j * x[1::2]) * (TX_FULL_SCALE / file_scale))
    finally:
        sdr.tx_destroy_buffer()
        print(f"TX stop {time.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
