"""Power per frequency zone and per time block of a bench IQ capture (receive side only, nothing transmitted).

    python -m bench.zone_power --iq out/bench/rx_1 --out out/bench/rx_1_zone_power.csv [--block-s 0.05]

Zones (baseband Hz at 528 kS/s, bench/config.yaml frequency plan; docs/GATES_PHASE2_S4.md section A):
  inband  [98, 166] kHz    link carrier +132 kHz +- 34 kHz (worst carrier offset): signal + co-channel jammer.
                           The primary statistic of detect/jam_v1 (S4).
  pilot   [37, 83] kHz     pilot search zone (+60 kHz +- 23 kHz): diagnostic only, never read by jam_v1.
  floor   [-250, -180] kHz an empty zone far from DC leakage, pilot and link: receiver floor, diagnostic only.
Power of a zone in a block = sum over its FFT bins of |X_k|^2 / N^2 (Parseval: the whole band sums to mean |x|^2),
in linear units of full scale squared (complex amplitude 1 = 0 dBFS). The CSV gives dBFS and the block's RX gain
(manual gain recorded by bench.rx_capture in the SigMF meta, `bench:rx_gain_db`), so the power referred to the RX
input is dBFS - gain. Memory: the capture is memory-mapped and processed in chunks of whole blocks.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bench import iqfile  # noqa: E402

ZONES = {"inband": (98000.0, 166000.0), "pilot": (37000.0, 83000.0), "floor": (-250000.0, -180000.0)}
BLOCK_S = 0.05
RX_FULL_SCALE = 2 ** 11                     # bench/pluto.py RX scale, used if the meta does not record one


def _zone_bins(n: int, fs: float, zones: dict) -> dict:
    f = np.fft.fftfreq(n, 1.0 / fs)
    return {name: (f >= lo) & (f <= hi) for name, (lo, hi) in zones.items()}


def block_power(iq: np.ndarray, fs: float, zones: dict = ZONES, block_s: float = BLOCK_S, t0: float = 0.0,
                max_blocks: int = 64) -> dict:
    """Linear zone power per block. Returns {'t0', 't1', <zone>: array}; a last partial block is kept (own length)."""
    n = int(round(block_s * fs))
    nb_full = len(iq) // n
    out = {"t0": [], "t1": [], **{z: [] for z in zones}}
    bins = _zone_bins(n, fs, zones)
    for b0 in range(0, nb_full, max_blocks):
        b1 = min(nb_full, b0 + max_blocks)
        x = np.asarray(iq[b0 * n:b1 * n], np.complex128).reshape(b1 - b0, n)
        p = np.abs(np.fft.fft(x, axis=1)) ** 2 / float(n) ** 2
        for z, m in bins.items():
            out[z].append(p[:, m].sum(axis=1))
        out["t0"].append(t0 + np.arange(b0, b1) * n / fs)
        out["t1"].append(t0 + np.arange(b0 + 1, b1 + 1) * n / fs)
    rest = len(iq) - nb_full * n
    if rest > 0:
        x = np.asarray(iq[nb_full * n:], np.complex128)
        p = np.abs(np.fft.fft(x)) ** 2 / float(rest) ** 2
        for z, m in _zone_bins(rest, fs, zones).items():
            out[z].append(np.array([p[m].sum()]))
        out["t0"].append(np.array([t0 + nb_full * n / fs]))
        out["t1"].append(np.array([t0 + len(iq) / fs]))
    return {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in out.items()}


def db(p: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(np.asarray(p, np.float64) + 1e-30)


def capture_power(base: str | Path, zones: dict = ZONES, block_s: float = BLOCK_S, chunk_blocks: int = 200) -> dict:
    """block_power over a SigMF cs16 capture, chunked. Adds 'rx_gain_db' (constant per capture)."""
    meta = iqfile.read_meta(base)
    g = meta["global"]
    fs = float(g["core:sample_rate"])
    full_scale = float(g.get("bench:full_scale", RX_FULL_SCALE))
    gain = float(g.get("bench:rx_gain_db", float("nan")))
    raw = iqfile.open_cs16(base)
    n = int(round(block_s * fs))
    step = chunk_blocks * n
    parts = []
    n_samples = len(raw) // 2
    for i0 in range(0, n_samples, step):
        i1 = min(n_samples, i0 + step)
        seg = raw[2 * i0:2 * i1].astype(np.float64)
        iq = (seg[0::2] + 1j * seg[1::2]) / full_scale
        parts.append(block_power(iq, fs, zones, block_s, t0=i0 / fs))
    out = {k: np.concatenate([p[k] for p in parts]) if parts else np.zeros(0) for k in ["t0", "t1", *zones]}
    out["rx_gain_db"] = np.full(len(out["t0"]), gain)
    return out


def write_csv(path: str | Path, res: dict, zones: dict = ZONES) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t0_s", "t1_s", *[f"{z}_dbfs" for z in zones], "rx_gain_db"])
        dbs = {z: db(res[z]) for z in zones}
        for i in range(len(res["t0"])):
            w.writerow([f"{res['t0'][i]:.4f}", f"{res['t1'][i]:.4f}", *[f"{dbs[z][i]:.3f}" for z in zones],
                        f"{res['rx_gain_db'][i]:.1f}"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iq", required=True, help="SigMF base name (without .sigmf-data)")
    ap.add_argument("--out", required=True, help="output CSV")
    ap.add_argument("--block-s", type=float, default=BLOCK_S)
    a = ap.parse_args(argv)
    res = capture_power(a.iq, block_s=a.block_s)
    write_csv(a.out, res)
    print(f"{a.out}: {len(res['t0'])} blocks of {a.block_s} s, median inband {np.median(db(res['inband'])):.1f} dBFS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
