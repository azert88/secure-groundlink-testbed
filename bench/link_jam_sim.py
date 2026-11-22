"""S4 development simulation of the mode B link under benign conditions and wideband jamming. SIMULATION ONLY.

Definition: docs/GATES_PHASE2_S4.md section B (signed, models/gates_p2_s4/lock.json). Nothing is transmitted.
One run = one 60-frame pass (6 windows of 10 frames) through the real PHY (bench/link_phy modulate / demodulate):
  - payloads: FUNCUBE-1 frames (data/tables/frames.csv, clean variant) of one source observation, sealed by
    bench/link_signed.Sender, cycled to 60 frames; slot = 0.1 s idle + burst + 0.1 s idle;
  - satellite carrier offset U[-15, 15] kHz per pass; pilot tone at +60 kHz + offset, amplitude 0.2 x burst amplitude
    (diagnostic only, never used by detect/jam_v1);
  - receiver noise white over 528 kHz, -45 dBFS in 24 kHz; SNR = burst power / noise power in 24 kHz;
  - jammer power: SIR = burst power / jammer power in 24 kHz around the carrier.
The pass is generated and demodulated one window (~5 s, ~2.7 M samples) at a time: bounded memory.
Not modelled: ADC clipping, AGC, multipath, sample-clock offset, Pluto spurs and LO leakage (W13).
"""

from __future__ import annotations

import os
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bench import dsp, zone_power  # noqa: E402
from bench import link_phy as PHY  # noqa: E402
from bench import link_signed as LS  # noqa: E402
from trust.session_keys import EpochState  # noqa: E402

SEED_TAG = b"s4-dev"
FS = PHY.FS
N24_DB = -45.0                          # receiver noise power in 24 kHz (dBFS)
REF_BW = PHY.REF_BW
IDLE_S = 0.1                            # idle before and after each burst
PASS_FRAMES, WINDOW = 60, 10
LINK_SNR_DB = 15.0                      # strong link under jamming
PILOT_HZ, PILOT_REL = 60000.0, 0.2
CO_CHANNEL_BW = 30000.0
PULSE_PERIOD_S = 0.25
SPLITS = {"calib": 0, "train": 1, "smoke": 9}
BENIGN = ("nominal", "low_snr", "attenuation_step", "fading", "pass_ramp", "noise_rise", "gain_step")
JAM_KINDS = ("continuous", "pulsed", "fullband")
SIRS = (-10, -5, 0, 5, 10, 20)


def jam_name(kind: str, sir: int) -> str:
    return f"jam_{kind}_{sir:+d}"


CONDITIONS = BENIGN + tuple(jam_name(k, s) for k in JAM_KINDS for s in SIRS)


def parse_jam(cond: str) -> tuple[str, int] | None:
    if not cond.startswith("jam_"):
        return None
    _, kind, sir = cond.split("_")
    return kind, int(sir)


def rng_for(split: str, cond: str, run: int) -> np.random.Generator:
    return np.random.default_rng([zlib.crc32(SEED_TAG), SPLITS[split], CONDITIONS.index(cond), run])


def schedule(cond: str, rng: np.random.Generator, n: int = PASS_FRAMES) -> dict:
    """Per-frame link SNR (dB), RX gain (dB), noise rise (dB) and the jammer settings of one pass (gate B.2)."""
    k = np.arange(n)
    snr, gain, rise = np.full(n, LINK_SNR_DB), np.zeros(n), np.zeros(n)
    jam = None
    if cond == "nominal":
        pass
    elif cond == "low_snr":
        snr[:] = rng.uniform(2.0, 8.0)
    elif cond == "attenuation_step":
        snr[int(rng.integers(20, 41)):] = 3.0
    elif cond == "fading":
        rho = 0.8
        h = np.empty(n, complex)
        h[0] = (rng.standard_normal() + 1j * rng.standard_normal()) / np.sqrt(2)
        for i in range(1, n):
            w = (rng.standard_normal() + 1j * rng.standard_normal()) / np.sqrt(2)
            h[i] = rho * h[i - 1] + np.sqrt(1 - rho ** 2) * w
        snr = 14.0 + 10 * np.log10(np.abs(h) ** 2 + 1e-12)
    elif cond in ("pass_ramp", "gain_step"):
        snr = 16.0 * np.sin(np.pi * (k + 0.5) / n)
        if cond == "gain_step":
            gain[k >= n // 2] = 10.0
    elif cond == "noise_rise":
        rise[20:40] = rng.uniform(1.0, 6.0)
    else:
        kind, sir = parse_jam(cond)
        jam = {"kind": kind, "sir_db": float(sir), "offset_hz": float(rng.uniform(-3000, 3000)),
               "phase_s": float(rng.uniform(0, PULSE_PERIOD_S))}
    return {"snr_db": snr, "gain_db": gain, "rise_db": rise, "jam": jam, "cfo_hz": float(rng.uniform(-15000, 15000))}


def source_obs(split: str) -> list[int]:
    import json
    sp = json.loads((ROOT / "data" / "splits.json").read_text(encoding="utf-8"))
    return [int(x) for x in sp["satellites"]["39444"]["splits"]["train" if split == "smoke" else split]]


def payloads(obs: int, n: int = PASS_FRAMES) -> list[bytes]:
    frames = LS.load_frames(obs)
    if not frames:
        raise ValueError(f"no frames for obs {obs}")
    cyc = [frames[i % len(frames)][1] for i in range(n)]
    with tempfile.TemporaryDirectory() as d:
        sender = LS.Sender(os.urandom(32), EpochState(Path(d) / "s.json"))
        return sender.seal_pass([(5000 * i, p) for i, p in enumerate(cyc)])


_LP = None


def _co_channel_lp() -> np.ndarray:
    global _LP
    if _LP is None:
        _LP = signal.firwin(255, CO_CHANNEL_BW / 2, fs=FS)
    return _LP


def _jammer(n: int, s0: int, jam: dict, burst_power: float, carrier_hz: float, rng) -> np.ndarray:
    """Jammer samples [s0, s0 + n) of the pass; power in 24 kHz around the carrier = burst_power / SIR."""
    j24 = burst_power / 10 ** (jam["sir_db"] / 10)
    z = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    if jam["kind"] == "fullband":
        x = z * np.sqrt(j24 * FS / REF_BW)
    else:
        h = _co_channel_lp()
        y = signal.lfilter(h, 1.0, z)
        y *= np.sqrt(j24 * CO_CHANNEL_BW / REF_BW / (np.sum(np.abs(h) ** 2)))   # white unit power -> sum |h|^2
        t = (s0 + np.arange(n)) / FS
        x = y * np.exp(2j * np.pi * (carrier_hz + jam["offset_hz"]) * t)
        if jam["kind"] == "pulsed":
            x = x * (((t + jam["phase_s"]) % PULSE_PERIOD_S) < PULSE_PERIOD_S / 2)
    return x


def simulate_run(split: str, cond: str, run: int, n_frames: int = PASS_FRAMES, obs: int | None = None) -> dict:
    """One pass; returns the raw ingredients for detect/jam_v1 (frames, zone blocks) plus diagnostics."""
    rng = rng_for(split, cond, run)
    sch = schedule(cond, rng, n_frames)
    obs = obs if obs is not None else int(rng.choice(source_obs(split)))
    envs = payloads(obs, n_frames)
    n24 = 10 ** (N24_DB / 10)
    sigma2 = n24 * FS / REF_BW
    carrier = PHY.IF_HZ + sch["cfo_hz"]
    frames, blocks, pilot_snr = [], [], []
    s_abs = 0
    for w0 in range(0, n_frames, WINDOW):
        parts, slots = [], []
        for k in range(w0, min(n_frames, w0 + WINDOW)):
            a = np.sqrt(n24 * 10 ** (sch["snr_db"][k] / 10))
            burst = PHY.modulate([envs[k]], FS, gap_s=0.0, lead_s=IDLE_S).astype(np.complex128)
            n = len(burst)
            t = (s_abs + np.arange(n)) / FS
            x = a * burst * np.exp(2j * np.pi * sch["cfo_hz"] * t)
            x += PILOT_REL * a * np.exp(2j * np.pi * (PILOT_HZ + sch["cfo_hz"]) * t)
            s2 = sigma2 * 10 ** (sch["rise_db"][k] / 10)
            x += np.sqrt(s2 / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
            if sch["jam"] is not None:
                x += _jammer(n, s_abs, sch["jam"], n24 * 10 ** (LINK_SNR_DB / 10), carrier, rng)
            x *= 10 ** (sch["gain_db"][k] / 20)
            i_burst = int(IDLE_S * FS)
            slots.append({"k": k, "s0": s_abs, "s1": s_abs + n, "b0": s_abs + i_burst, "b1": s_abs + n - i_burst,
                          "gain_db": float(sch["gain_db"][k])})
            parts.append(x.astype(np.complex64))
            s_abs += n
        iq = np.concatenate(parts)
        ws0 = slots[0]["s0"]
        det = PHY.demodulate(iq)
        ok = {(int(idx) + ws0, bytes(p)) for idx, p, crc in det if crc}
        for sl in slots:
            got = any(sl["s0"] <= idx < sl["s1"] and p == envs[sl["k"]] for idx, p in ok)
            frames.append({"k": sl["k"], "t0": sl["s0"] / FS, "t1": sl["s1"] / FS, "b0": sl["b0"] / FS,
                           "b1": sl["b1"] / FS, "lost": not got})
        bp = zone_power.block_power(iq, FS, zone_power.ZONES, zone_power.BLOCK_S, t0=ws0 / FS)
        # RX gain per block: the gain of the slot containing the block start (gain changes only at window bounds)
        starts = np.array([sl["s0"] for sl in slots]) / FS
        idx = np.clip(np.searchsorted(starts, bp["t0"], side="right") - 1, 0, len(slots) - 1)
        bp["gain_db"] = np.array([slots[i]["gain_db"] for i in idx])
        blocks.append(bp)
        sec = int(FS)
        pilot_snr.append(float(np.median([dsp.pilot_estimate(iq[i:i + sec], FS, PILOT_HZ)["pilot_snr_db"]
                                          for i in range(0, max(1, len(iq) - sec + 1), sec)])))
    bl = {k: np.concatenate([b[k] for b in blocks]).tolist() for k in blocks[0]}
    return {"split": split, "condition": cond, "run": run, "source_obs": obs, "cfo_hz": sch["cfo_hz"],
            "jam": sch["jam"], "snr_db": sch["snr_db"].round(3).tolist(), "frames": frames, "blocks": bl,
            "pilot_snr_db_windows": pilot_snr, "n_lost": int(sum(f["lost"] for f in frames))}
