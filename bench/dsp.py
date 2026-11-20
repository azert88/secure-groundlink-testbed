"""Mode A signal chain: SatNOGS audio (48 kHz, real) <-> complex baseband IQ for the Pluto (USB / upper sideband).

TX:  audio -> analytic signal (Hilbert) -> x11 polyphase up-sampling -> shift to +if_hz -> + CW pilot at +pilot_hz.
RX:  IQ -> per-block pilot search (carrier offset, CFO) -> shift down by if_hz + CFO -> complex low-pass 24 kHz
     -> /11 -> real part = audio.  The per-block CFO, pilot SNR and power are kept as radio evidence (bench_features.csv).
All stages are streaming (filter state carried between chunks) so a full pass never has to sit in memory at 528 kS/s.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

AUDIO_SR = 48000
AUDIO_BW = 24000.0


def ratio(cfg: dict) -> int:
    fs = int(cfg["rf"]["sample_rate"])
    if fs % AUDIO_SR:
        raise ValueError(f"sample_rate {fs} must be a multiple of {AUDIO_SR}")
    return fs // AUDIO_SR


def lowpass_taps(fs: float, cutoff: float, numtaps: int = 255) -> np.ndarray:
    return signal.firwin(numtaps, cutoff, fs=fs).astype(np.float64)


def analytic(audio: np.ndarray) -> np.ndarray:
    """Analytic signal of a real audio track (positive frequencies only)."""
    return signal.hilbert(np.asarray(audio, dtype=np.float64)).astype(np.complex128)


def scaled_analytic(audio: np.ndarray, peak: float) -> tuple[np.ndarray, float]:
    """Analytic signal scaled so the 99.99th percentile of its magnitude equals `peak` (rare spikes limited)."""
    z = analytic(audio)
    ref = float(np.percentile(np.abs(z), 99.99)) or 1.0
    g = peak / ref
    z, _ = limit(z * g, min(0.85, 1.7 * peak))
    return z, g


class Upconverter:
    """Streaming analytic audio (48 kHz) -> IQ at fs = L x 48 kHz, shifted to +if_hz, plus a CW pilot."""

    def __init__(self, cfg: dict):
        self.L = ratio(cfg)
        self.fs = float(cfg["rf"]["sample_rate"])
        self.taps = lowpass_taps(self.fs, AUDIO_BW * 0.98) * self.L
        self.zi = np.zeros(len(self.taps) - 1, dtype=np.complex128)
        self.f_if = float(cfg["rf"]["if_hz"])
        self.f_p = float(cfg["rf"]["pilot_hz"])
        self.a_p = float(cfg["rf"]["pilot_amplitude"])
        self.n = 0                                           # absolute sample index at fs

    def process(self, z48: np.ndarray) -> np.ndarray:
        up = np.zeros(len(z48) * self.L, dtype=np.complex128)
        up[::self.L] = z48
        y, self.zi = signal.lfilter(self.taps, 1.0, up, zi=self.zi)
        t = (self.n + np.arange(len(y))) / self.fs
        self.n += len(y)
        return y * np.exp(2j * np.pi * self.f_if * t) + self.a_p * np.exp(2j * np.pi * self.f_p * t)


def pilot_estimate(iq: np.ndarray, fs: float, f_expected: float, search_hz: float = 23000.0) -> dict:
    """Frequency of the strongest line within +-search_hz of f_expected (parabolic interpolation on a Hann FFT).

    Returns cfo_hz (measured - expected), pilot_snr_db (peak bin vs median of the search band), power_dbfs.
    """
    n = len(iq)
    spec = np.abs(np.fft.fft(iq * np.hanning(n))) ** 2
    freqs = np.fft.fftfreq(n, 1.0 / fs)
    band = np.where(np.abs(freqs - f_expected) <= search_hz)[0]
    k = band[np.argmax(spec[band])]
    a, b, c = (np.log(spec[(k + d) % n] + 1e-30) for d in (-1, 0, 1))
    den = a - 2 * b + c
    delta = 0.5 * (a - c) / den if den != 0 else 0.0
    f_peak = freqs[k] + delta * fs / n
    return {"cfo_hz": float(f_peak - f_expected),
            "pilot_snr_db": float(10 * np.log10(spec[k] / (np.median(spec[band]) + 1e-30))),
            "power_dbfs": float(10 * np.log10(np.mean(np.abs(iq) ** 2) + 1e-30))}


class Downconverter:
    """Streaming IQ at fs -> USB audio at 48 kHz, with per-block CFO tracking on the pilot.

    process() takes blocks whose length is a multiple of L (one block = one CFO estimate, e.g. 1 s).
    """

    def __init__(self, cfg: dict, smooth: float = 0.5):
        self.L = ratio(cfg)
        self.fs = float(cfg["rf"]["sample_rate"])
        self.taps = lowpass_taps(self.fs, AUDIO_BW * 0.98)
        self.zi = np.zeros(len(self.taps) - 1, dtype=np.complex128)
        self.f_if = float(cfg["rf"]["if_hz"])
        self.f_p = float(cfg["rf"]["pilot_hz"])
        self.search = float(cfg["rf"].get("pilot_search_hz", 23000.0))
        self.phase = 0.0
        self.cfo = None
        self.smooth = smooth                                  # weight of the previous CFO in the tracked value
        self.features: list[dict] = []
        self.n = 0

    def process(self, iq: np.ndarray) -> np.ndarray:
        if len(iq) % self.L:
            raise ValueError(f"block length {len(iq)} is not a multiple of {self.L}")
        est = pilot_estimate(iq, self.fs, self.f_p, self.search)
        cfo = est["cfo_hz"] if self.cfo is None else self.smooth * self.cfo + (1 - self.smooth) * est["cfo_hz"]
        self.cfo = cfo
        self.features.append({"t_s": self.n / self.fs, **est, "cfo_tracked_hz": cfo})
        f = self.f_if + cfo
        ph = self.phase + 2 * np.pi * f * np.arange(len(iq)) / self.fs
        self.phase = float((self.phase + 2 * np.pi * f * len(iq) / self.fs) % (2 * np.pi))
        self.n += len(iq)
        base = iq * np.exp(-1j * ph)
        y, self.zi = signal.lfilter(self.taps, 1.0, base, zi=self.zi)
        return np.real(y[::self.L])                 # real part of the analytic band = the audio


def chunks(x: np.ndarray, size: int):
    for i in range(0, len(x), size):
        yield x[i:i + size]


def limit(iq: np.ndarray, max_mag: float = 0.95) -> tuple[np.ndarray, int]:
    """Clip the magnitude (phase kept); returns the clipped signal and the number of clipped samples."""
    mag = np.abs(iq)
    over = mag > max_mag
    if over.any():
        iq = iq.copy()
        iq[over] *= max_mag / mag[over]
    return iq, int(over.sum())


def to_cs16(iq: np.ndarray, full_scale: float) -> np.ndarray:
    """Complex float -> interleaved int16 at the Pluto scale (magnitude limited to 0.95 first)."""
    iq, _ = limit(iq)
    out = np.empty(2 * len(iq), dtype=np.int16)
    out[0::2] = np.round(iq.real * full_scale * 0.999)
    out[1::2] = np.round(iq.imag * full_scale * 0.999)
    return out


def from_cs16(raw: np.ndarray, full_scale: float) -> np.ndarray:
    raw = np.asarray(raw, dtype=np.float64)
    return (raw[0::2] + 1j * raw[1::2]) / full_scale
