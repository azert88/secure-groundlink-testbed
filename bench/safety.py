"""RF safety guard for the conducted bench. Every TX script calls `check_tx` before opening a Pluto.

Rules (docs/BENCH_LOG.md, bench/README.md):
  * conducted only, no antenna;
  * at least `min_fixed_attenuation_db` of fixed attenuation in every TX -> RX path, declared in config;
  * TX hardware gain <= `tx_gain_max_db` unless the operator passes --i-have-30db-attenuation;
  * worst-case power at any RX input (TX at its maximum, declared attenuation) stays below the RX absolute maximum.
The guard cannot see the cables: it checks what the operator declares, and refuses when the declaration is unsafe.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "bench" / "config.yaml"


class SafetyError(RuntimeError):
    pass


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def worst_case_rx_dbm(cfg: dict, tx_gain_db: float | None = None) -> float:
    """Power at the RX input if the TX ran at `tx_gain_db` (default: 0 dB = maximum) through the declared attenuation."""
    s = cfg["safety"]
    gain = 0.0 if tx_gain_db is None else float(tx_gain_db)
    return float(s["tx_max_dbm"]) + gain - float(s["declared_attenuation_db"])


def check_tx(cfg: dict, tx_gain_db: float, confirmed_attenuation: bool = False) -> list[str]:
    """Raise SafetyError if the TX request is unsafe; return the lines of the safety banner otherwise."""
    s = cfg["safety"]
    att = float(s["declared_attenuation_db"])
    if att < float(s["min_fixed_attenuation_db"]):
        raise SafetyError(
            f"declared fixed attenuation {att:g} dB < required {s['min_fixed_attenuation_db']} dB: "
            "wire the attenuators, then set safety.declared_attenuation_db in bench/config.yaml")
    if tx_gain_db > 0:
        raise SafetyError(f"tx gain {tx_gain_db:g} dB > 0 dB is not a valid Pluto TX hardware gain")
    if tx_gain_db > float(s["tx_gain_max_db"]) and not confirmed_attenuation:
        raise SafetyError(
            f"tx gain {tx_gain_db:g} dB above the bench limit {s['tx_gain_max_db']} dB; "
            "pass --i-have-30db-attenuation only after checking every TX -> RX path")
    worst = worst_case_rx_dbm(cfg)            # even with the gain at its maximum by mistake
    if worst >= float(s["rx_abs_max_dbm"]):
        raise SafetyError(f"worst case at RX {worst:.1f} dBm >= RX absolute maximum {s['rx_abs_max_dbm']} dBm")
    expected = worst_case_rx_dbm(cfg, tx_gain_db)
    return [
        "=" * 72,
        "CONDUCTED BENCH - NO ANTENNA. Every TX -> RX path must hold the declared fixed attenuation.",
        f"TX gain {tx_gain_db:g} dB; declared attenuation {att:g} dB; "
        f"expected RX input <= {expected:.1f} dBm (worst case {worst:.1f} dBm, RX abs. max {s['rx_abs_max_dbm']} dBm).",
        f"Centre frequency {cfg['rf']['center_hz'] / 1e6:.3f} MHz, sample rate {cfg['rf']['sample_rate']} S/s.",
        "Log this session in docs/BENCH_LOG.md.",
        "=" * 72,
    ]


def add_tx_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--tx-gain-db", type=float, default=None, help="Pluto TX hardware gain (default: config)")
    ap.add_argument("--i-have-30db-attenuation", action="store_true",
                    help="allow a TX gain above safety.tx_gain_max_db (attenuators checked by hand)")


def guard_from_args(args: argparse.Namespace) -> tuple[dict, float]:
    """Load config, apply the guard, print the banner; return (config, tx gain)."""
    cfg = load_config(args.config)
    gain = cfg["safety"]["tx_gain_db"] if args.tx_gain_db is None else args.tx_gain_db
    for line in check_tx(cfg, float(gain), args.i_have_30db_attenuation):
        print(line)
    return cfg, float(gain)
