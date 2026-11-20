"""Record IQ from a Pluto into a SigMF file (receive only, no transmission).

    python -m bench.rx_capture --device station --seconds 300 --out out/bench/rx_001

Writes 1 s buffers straight to disk (flat memory on a Pi), and reports dropped-buffer suspicion from the wall clock.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from bench import iqfile
from bench.pluto import RX_FULL_SCALE, configure_rx, describe, open_pluto
from bench.safety import DEFAULT_CONFIG, load_config


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default="station", choices=["station", "attacker", "satellite"])
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gain-db", type=float, default=None)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args()
    cfg = load_config(args.config)
    gain = cfg["rx"]["hardware_gain_db"] if args.gain_db is None else args.gain_db
    sdr = open_pluto(cfg["devices"][args.device])
    configure_rx(sdr, cfg, gain)
    n_buf = int(np.ceil(args.seconds * cfg["rf"]["sample_rate"] / cfg["rx"]["buffer_samples"]))
    data, _ = iqfile.paths(args.out)
    Path(data).parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):                                         # flush stale buffers
        sdr.rx()
    start = datetime.now(timezone.utc)
    t0 = time.monotonic()
    with open(data, "wb") as fh:
        for _ in range(n_buf):
            x = sdr.rx()
            out = np.empty(2 * len(x), dtype=np.int16)
            out[0::2] = np.round(x.real)
            out[1::2] = np.round(x.imag)
            out.tofile(fh)
    wall = time.monotonic() - t0
    expected = n_buf * cfg["rx"]["buffer_samples"] / cfg["rf"]["sample_rate"]
    info = {"device": args.device, "rx_gain_db": gain, "full_scale": RX_FULL_SCALE, "wall_s": wall,
            "expected_s": expected, "settings": {k: str(v) for k, v in describe(sdr).items()}}
    iqfile.write_meta(args.out, cfg["rf"]["sample_rate"], cfg["rf"]["center_hz"], start=start,
                      description=f"bench capture ({args.device})", hw="ADALM-Pluto", extra=info)
    flag = "" if wall < expected * 1.05 else "  WARNING: wall time > expected, buffers may have been dropped"
    print(f"{data}: {expected:.0f} s recorded in {wall:.1f} s{flag}")


if __name__ == "__main__":
    main()
