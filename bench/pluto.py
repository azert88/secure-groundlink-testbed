"""Thin pyadi-iio wrapper (the only module that opens a Pluto). Requires requirements-bench.txt.

Scaling [UNVERIFIED for the installed firmware, check on the first loop-back]: pyadi-iio TX takes complex samples
up to 2^14; RX returns 12-bit values sign-extended (about +-2^11). The values are recorded in each IQ file's meta.
"""

from __future__ import annotations

import numpy as np

TX_FULL_SCALE = 2 ** 14
RX_FULL_SCALE = 2 ** 11


def open_pluto(uri: str):
    if str(uri).startswith("fake:"):                # rehearsal stand-in, no hardware (bench/fake_pluto.py)
        from bench.fake_pluto import FakePluto
        return FakePluto(uri)
    import adi                                     # pyadi-iio; imported here so the rest of bench/ runs without it
    return adi.Pluto(uri)


def configure_rx(sdr, cfg: dict, gain_db: float) -> None:
    rf = cfg["rf"]
    sdr.sample_rate = int(rf["sample_rate"])
    sdr.rx_lo = int(rf["center_hz"])
    sdr.rx_rf_bandwidth = int(rf["rf_bandwidth"])
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = float(gain_db)
    sdr.rx_buffer_size = int(cfg["rx"]["buffer_samples"])


def configure_tx(sdr, cfg: dict, gain_db: float) -> None:
    """Call only after bench.safety.check_tx accepted gain_db."""
    rf = cfg["rf"]
    sdr.sample_rate = int(rf["sample_rate"])
    sdr.tx_lo = int(rf["center_hz"])
    sdr.tx_rf_bandwidth = int(rf["rf_bandwidth"])
    sdr.tx_hardwaregain_chan0 = float(gain_db)


def describe(sdr) -> dict:
    out = {}
    for k in ("sample_rate", "rx_lo", "tx_lo", "rx_hardwaregain_chan0", "tx_hardwaregain_chan0"):
        try:
            out[k] = getattr(sdr, k)
        except Exception as e:                     # attribute names differ across pyadi-iio versions
            out[k] = f"n/a ({type(e).__name__})"
    return out


def complex_from_int16(raw: np.ndarray, full_scale: float) -> np.ndarray:
    return (raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)) / full_scale
