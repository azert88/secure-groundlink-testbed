"""Stand-in for adi.Pluto, for rehearsing the bench scripts with NO hardware (nothing is ever transmitted).

Selected only when a device URI in the bench config starts with "fake:" (bench.pluto.open_pluto); the shipped
bench/config.yaml uses "usb:", so a real session never reaches this module.

All fake devices of one Python process share one in-memory CABLE (a FIFO of complex64 samples at the RX scale):
  tx(x)   x in pyadi-iio TX units (|x| up to TX_FULL_SCALE = 2^14). The cable applies the TX gain and a fixed path
          loss (dB) and a fixed carrier offset (continuous phase), and stores the level at the RX input; rx() adds the
          receiver gain and a calibration offset (dB). Cyclic TX keeps the buffer and appends it for the wall time it was "on" at tx_destroy_buffer().
  rx()    returns rx_buffer_size complex samples, like the real driver: integer-valued, 12-bit range (|I|, |Q| <=
          2047), with complex Gaussian noise; empty cable = noise only (idle air).
The cable starts with `lead_s` seconds of idle air (default 4 s): it plays the role of "start the station capture,
then transmit" (bench/README.md mode A steps 2-3), so rx_capture's 3 flushed buffers do not eat the signal.
The levels are a rehearsal model, not a measurement of a Pluto [UNVERIFIED as hardware behaviour].
Because the cable lives in memory, TX and RX must run in the same process (tests/test_bench_dry_run.py).
"""

from __future__ import annotations

import time

import numpy as np

TX_FULL_SCALE = 2 ** 14
RX_FULL_SCALE = 2 ** 11


class Cable:
    def __init__(self, fs: float = 528000.0, cfo_hz: float = 7000.0, path_loss_db: float = 30.0,
                 cal_db: float = 20.0, noise_dbfs: float = -60.0, lead_s: float = 4.0, seed: int = 0):
        self.fs, self.cfo_hz, self.path_loss_db, self.cal_db = fs, cfo_hz, path_loss_db, cal_db
        self.noise_dbfs, self.lead_s = noise_dbfs, lead_s
        self.rng = np.random.default_rng(seed)
        self.chunks: list[np.ndarray] = []
        self.offset = 0                           # read position inside chunks[0]
        self.n_tx = 0                             # samples written so far (carrier-offset phase reference)
        self.pending_lead = True

    def _lead(self) -> None:
        if self.pending_lead:
            self.pending_lead = False
            self.chunks.append(np.zeros(int(self.lead_s * self.fs), np.complex64))

    def write(self, x: np.ndarray, tx_gain_db: float) -> None:
        self._lead()
        x = np.asarray(x, np.complex128) / TX_FULL_SCALE
        n = np.arange(self.n_tx, self.n_tx + len(x))
        self.n_tx += len(x)
        g = 10 ** ((tx_gain_db - self.path_loss_db) / 20)                  # level at the RX input
        self.chunks.append((x * g * np.exp(2j * np.pi * self.cfo_hz * n / self.fs)).astype(np.complex64))

    def read(self, n: int, rx_gain_db: float) -> np.ndarray:
        self._lead()
        out = np.zeros(n, np.complex128)
        k = 0
        while k < n and self.chunks:
            c = self.chunks[0]
            take = min(n - k, len(c) - self.offset)
            out[k:k + take] = c[self.offset:self.offset + take]
            k += take
            self.offset += take
            if self.offset == len(c):
                self.chunks.pop(0)
                self.offset = 0
        out *= 10 ** ((rx_gain_db + self.cal_db) / 20)
        sigma = 10 ** (self.noise_dbfs / 20) / np.sqrt(2)
        out += sigma * (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n))
        out = np.clip(np.round(out * RX_FULL_SCALE), -(RX_FULL_SCALE - 1), RX_FULL_SCALE - 1)
        return out                                 # pyadi-iio returns integer-valued complex samples

    def pending_samples(self) -> int:
        return sum(len(c) for c in self.chunks) - self.offset


CABLE = Cable()


def reset_cable(**kw) -> Cable:
    """New empty cable (fresh idle lead); keyword arguments override the Cable defaults."""
    global CABLE
    CABLE = Cable(**kw)
    return CABLE


class FakePluto:
    """Attribute-compatible subset of adi.Pluto used by bench/pluto.py, bench/rx_capture.py and bench/tx_play.py."""

    def __init__(self, uri: str = "fake:"):
        if not str(uri).startswith("fake:"):
            raise ValueError(f"FakePluto only accepts 'fake:' URIs, got {uri!r}")
        self.uri = uri
        self.sample_rate = 528000
        self.rx_lo = self.tx_lo = 2400000000
        self.rx_rf_bandwidth = self.tx_rf_bandwidth = 18000000
        self.gain_control_mode_chan0 = "slow_attack"
        self.rx_hardwaregain_chan0 = 71.0
        self.tx_hardwaregain_chan0 = -10.0
        self.rx_buffer_size = 1024
        self.tx_cyclic_buffer = False
        self._cyclic: tuple[np.ndarray, float] | None = None

    def rx(self) -> np.ndarray:
        gain = float(self.rx_hardwaregain_chan0) if self.gain_control_mode_chan0 == "manual" else 30.0
        return CABLE.read(int(self.rx_buffer_size), gain)

    def tx(self, data) -> None:
        if self.tx_hardwaregain_chan0 > 0:
            raise ValueError("TX hardware gain must be <= 0 dB")
        x = np.asarray(data)
        if not np.iscomplexobj(x):
            raise TypeError("tx() takes complex samples")
        if np.max(np.abs(x.real), initial=0) > TX_FULL_SCALE or np.max(np.abs(x.imag), initial=0) > TX_FULL_SCALE:
            raise ValueError("tx() samples exceed the 2^14 TX scale")
        if self.tx_cyclic_buffer:
            if self._cyclic is not None:
                raise RuntimeError("cyclic buffer already pushed; call tx_destroy_buffer() first")
            self._cyclic = (x.copy(), time.monotonic())
        else:
            CABLE.write(x, float(self.tx_hardwaregain_chan0))

    def tx_destroy_buffer(self) -> None:
        if self._cyclic is not None:
            x, t0 = self._cyclic
            n = int((time.monotonic() - t0) * float(self.sample_rate))
            reps = int(np.ceil(n / len(x))) if len(x) else 0
            for _ in range(reps):
                CABLE.write(x, float(self.tx_hardwaregain_chan0))
            self._cyclic = None
