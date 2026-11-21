"""Mode B packet PHY (signed link): binary FSK bursts in numpy at the bench sample rate (528 kS/s, bench/config.yaml).

Modulation: 2-FSK, continuous phase (CPFSK), 9600 bit/s, deviation +-4800 Hz (modulation index h = 1), rectangular
pulses (no Gaussian filter), demodulated NON-COHERENTLY with two tone correlators per bit. Why not GFSK:
- with h = 1 the two tones are orthogonal over one bit, so the non-coherent correlator is the optimum detector and
  needs no carrier phase: only a frequency estimate (from the preamble) and a bit timing. BER = 0.5 exp(-Eb/2N0).
- Gaussian shaping narrows the spectrum but breaks that orthogonality (inter-symbol interference), and on a conducted
  bench with 30 dB pads the occupied bandwidth (~2 (dev + Rb) = 28.8 kHz) is not a constraint: it fits in the
  audio zone of the bench frequency plan with the worst carrier offset.
Carrier: +132 kHz in baseband (`IF_HZ`, centre of the bench audio zone 120-144 kHz), so with a +-22 kHz offset the
signal stays in ~98-166 kHz, away from the LO leakage at DC and from the pilot search zone (37-83 kHz).

Burst framing (bits MSB first):
  preamble 12 x 0x55 (96 bits, alternating) | sync 0x1ACFFC1D (32, CCSDS ASM) | length u16 | payload | CRC-32 | 0x55 tail
  CRC-32 = zlib.crc32 over length || payload. PHY overhead 23 bytes per burst. Bursts are separated by >= 20 ms idle.

Receiver: mix down by IF_HZ, complex FIR low-pass (34 kHz), decimate by 5 (105.6 kS/s, 11 samples per bit), burst
detection on the smoothed power (8-bit moving average > 2 x its 5th percentile = noise floor; needs idle gaps), carrier
offset per burst on the preamble (lag-2 autocorrelation, unambiguous to +-26.4 kHz, then lag-22 = 2 bits refinement),
bit timing = the sample phase that maximises the preamble decision energy, sync search (<= 3 bit errors), length, CRC.
In-band SNR (as bench/sim_loopback): signal power over noise power in a 24 kHz band (`REF_BW`), so
Eb/N0 = SNR + 10 log10(24000 / 9600) = SNR + 4.0 dB.

Not modelled (limits): sample-clock offset between Plutos (+-25 ppm each -> <= 0.15 bit drift over a 3000-bit burst,
no timing tracking after the preamble), multipath, AGC transients, overlapping bursts (collisions are lost).
"""

from __future__ import annotations

import zlib

import numpy as np
from scipy import signal

FS = 528000
BIT_RATE = 9600
DEV_HZ = 4800.0
IF_HZ = 132000.0
REF_BW = 24000.0
DECIM = 5
PREAMBLE = b"\x55" * 12
SYNC = bytes.fromhex("1ACFFC1D")
TAIL = b"\x55"
PHY_OVERHEAD = len(PREAMBLE) + len(SYNC) + 2 + 4 + len(TAIL)       # 23 bytes
MAX_LEN = 4096
_SYNC_BITS = np.unpackbits(np.frombuffer(SYNC, np.uint8)).astype(np.int8)


def sps(fs: int = FS) -> int:
    if fs % BIT_RATE:
        raise ValueError(f"sample rate {fs} is not a multiple of {BIT_RATE} bit/s")
    return fs // BIT_RATE


def frame_bytes(payload: bytes) -> bytes:
    if len(payload) > MAX_LEN:
        raise ValueError(f"payload longer than {MAX_LEN} bytes")
    head = len(payload).to_bytes(2, "big")
    return PREAMBLE + SYNC + head + payload + zlib.crc32(head + payload).to_bytes(4, "big") + TAIL


def _burst(bits: np.ndarray, fs: int, if_hz: float, amplitude: float) -> np.ndarray:
    n = sps(fs)
    f = if_hz + DEV_HZ * (2.0 * np.repeat(bits.astype(np.float64), n) - 1.0)
    x = amplitude * np.exp(1j * 2 * np.pi * np.cumsum(f) / fs)
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)                # one-bit raised-cosine ramps
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x.astype(np.complex64)


def modulate(frames: list[bytes], fs: int = FS, if_hz: float = IF_HZ, amplitude: float = 0.5, gap_s: float = 0.02,
             lead_s: float = 0.02) -> np.ndarray:
    """Complex baseband IQ (complex64, |x| <= amplitude) of the payloads, one burst each, idle gaps in between."""
    lead, gap = np.zeros(int(lead_s * fs), np.complex64), np.zeros(int(gap_s * fs), np.complex64)
    parts = [lead]
    for p in frames:
        parts += [_burst(np.unpackbits(np.frombuffer(frame_bytes(bytes(p)), np.uint8)), fs, if_hz, amplitude), gap]
    parts.append(lead)
    return np.concatenate(parts)


def channel(iq: np.ndarray, fs: int = FS, cfo_hz: float = 0.0, snr_db: float | None = 30.0, seed: int = 0,
            ref_bw: float = REF_BW) -> np.ndarray:
    """Simulated conducted channel: carrier offset + AWGN at an in-band SNR (signal power measured on the bursts,
    noise power counted in ref_bw), same SNR convention as bench/sim_loopback.channel."""
    rng = np.random.default_rng(seed)
    y = iq * np.exp(1j * 2 * np.pi * cfo_hz * np.arange(len(iq)) / fs)
    if snr_db is not None:
        active = np.abs(iq) > 1e-6
        p_sig = float(np.mean(np.abs(iq[active]) ** 2)) if active.any() else 1.0
        sigma2 = p_sig / 10 ** (snr_db / 10) * fs / ref_bw
        y = y + np.sqrt(sigma2 / 2) * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    return y.astype(np.complex64)


# ------------------------------------------------------------------------------------------- receiver
_TAPS = None


def _taps(fs: int) -> np.ndarray:
    global _TAPS
    if _TAPS is None or _TAPS[0] != fs:
        _TAPS = (fs, signal.firwin(127, 34000.0, fs=fs))
    return _TAPS[1]


def _baseband(iq: np.ndarray, fs: int, if_hz: float, chunk: int = 1 << 20) -> np.ndarray:
    """Mix down by if_hz, low-pass, decimate by DECIM (streaming, bounded memory). Output delay: 63 input samples (FIR group delay)."""
    h = _taps(fs)
    zi = np.zeros(len(h) - 1, np.complex128)
    out, phase0 = [], 0
    for i in range(0, len(iq), chunk):
        x = np.asarray(iq[i:i + chunk], np.complex128)
        n = np.arange(phase0, phase0 + len(x))
        x = x * np.exp(-1j * 2 * np.pi * if_hz * (n % fs) / fs)
        y, zi = signal.lfilter(h, 1.0, x, zi=zi)
        start = (-phase0) % DECIM
        out.append(y[start::DECIM].astype(np.complex64))
        phase0 += len(x)
    return np.concatenate(out) if out else np.zeros(0, np.complex64)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _cfo(seg: np.ndarray, fs2: float) -> float:
    """Carrier offset of an alternating (0101) CPFSK h=1 preamble: lag-2 then lag-22 (2 bits) autocorrelation."""
    n = np.arange(len(seg))
    f1 = np.angle(np.sum(seg[2:] * np.conj(seg[:-2]))) * fs2 / (2 * np.pi * 2)
    s = seg * np.exp(-1j * 2 * np.pi * f1 * n / fs2)
    lag = 2 * (int(fs2) // BIT_RATE)
    f2 = np.angle(np.sum(s[lag:] * np.conj(s[:-lag]))) * fs2 / (2 * np.pi * lag)
    return float(f1 + f2)


def _bits_to_int(b: np.ndarray) -> int:
    return int("".join("1" if x else "0" for x in b), 2) if len(b) else 0


def demodulate(iq: np.ndarray, fs: int = FS, if_hz: float = IF_HZ, return_cfo: bool = False) -> list[tuple]:
    """[(sample_index, payload, crc_ok)] per detected burst; sample_index = start of the sync word in iq.
    return_cfo: tuples get a 4th element, the carrier offset estimated on the preamble (Hz)."""
    y = _baseband(iq, fs, if_hz)
    fs2, n2 = fs / DECIM, sps(fs) // DECIM
    if sps(fs) % DECIM:
        raise ValueError("samples per bit must be a multiple of the decimation")
    if len(y) < 200 * n2:
        return []
    p = np.abs(y) ** 2
    w = 8 * n2
    c = np.cumsum(np.concatenate([[0.0], p.astype(np.float64)]))
    sm = np.empty_like(p, dtype=np.float64)
    sm[:] = (c[np.minimum(np.arange(len(p)) + w // 2, len(p))] - c[np.maximum(np.arange(len(p)) - w // 2, 0)]) / w
    floor = max(float(np.percentile(sm, 5)), 1e-20)
    runs = _runs(sm > 2.0 * floor)
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] < 4 * n2:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    k = np.arange(n2)
    h_hi, h_lo = np.exp(1j * 2 * np.pi * DEV_HZ * k / fs2), np.exp(-1j * 2 * np.pi * DEV_HZ * k / fs2)
    out = []
    for a, b in merged:
        if b - a < 150 * n2:
            continue
        s0 = a + 6 * n2
        pre = y[s0:s0 + 64 * n2]
        cfo = _cfo(pre, fs2)
        a0 = max(0, a - 2 * n2)
        z = y[a0:min(len(y), b + 16 * n2)]
        z = z * np.exp(-1j * 2 * np.pi * cfo * np.arange(len(z)) / fs2)
        c_hi = np.convolve(z, np.conj(h_hi)[::-1], "valid")
        c_lo = np.convolve(z, np.conj(h_lo)[::-1], "valid")
        soft = np.abs(c_hi) ** 2 - np.abs(c_lo) ** 2
        i0 = s0 - a0
        score = [np.abs(soft[i0 + q:i0 + q + 64 * n2:n2]).sum() for q in range(n2)]
        ph = (i0 + int(np.argmax(score))) % n2
        bits = (soft[ph::n2] > 0).astype(np.int8)
        search = min(len(bits) - 32, 200)
        if search <= 0:
            continue
        win = np.lib.stride_tricks.sliding_window_view(bits[:search + 32], 32)[:search]
        dist = np.count_nonzero(win != _SYNC_BITS, axis=1)
        j = int(np.argmin(dist))
        if dist[j] > 3:
            continue
        idx = (a0 + ph + j * n2) * DECIM - 63
        body = bits[j + 32:]
        if len(body) < 16:
            out.append((idx, b"", False) + ((cfo,) if return_cfo else ()))
            continue
        n = _bits_to_int(body[:16])
        need = 16 + 8 * n + 32
        if n > MAX_LEN or len(body) < need:
            out.append((idx, b"", False) + ((cfo,) if return_cfo else ()))
            continue
        payload = np.packbits(body[16:16 + 8 * n]).tobytes()
        crc = _bits_to_int(body[16 + 8 * n:need])
        ok = crc == zlib.crc32(n.to_bytes(2, "big") + payload)
        out.append((int(idx), payload, bool(ok)) + ((cfo,) if return_cfo else ()))
    return out


def demodulate_blocks(read, n_samples: int, fs: int = FS, if_hz: float = IF_HZ, block_s: float = 20.0,
                      overlap_s: float = 1.0) -> list[tuple]:
    """demodulate over a long capture in overlapping blocks (bounded memory); read(i0, i1) -> complex IQ.
    A burst is kept by the block in whose non-overlapped part its sync word starts (bursts < overlap_s)."""
    blk, ov = int(block_s * fs), int(overlap_s * fs)
    out = []
    for i0 in range(0, max(n_samples, 1), blk):
        i1 = min(n_samples, i0 + blk + ov)
        for t in demodulate(read(i0, i1), fs, if_hz):
            if t[0] < blk or i1 >= n_samples:
                out.append((t[0] + i0,) + tuple(t[1:]))
        if i1 >= n_samples:
            break
    return out
