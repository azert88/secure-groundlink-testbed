"""Attacker waveforms as IQ files (no hardware). Transmit them with bench.tx_play --device attacker (operator only).

    python -m bench.make_attack_iq --kind tone      --audio-hz 4000 --out out/bench/atk_tone
    python -m bench.make_attack_iq --kind narrow    --audio-hz 4000 --width-hz 100 --out out/bench/atk_nb
    python -m bench.make_attack_iq --kind wideband  --out out/bench/atk_wb          (barrage over the whole audio band)

Frequencies are given in the station's AUDIO band (Hz above the USB carrier): the RF offset is if_hz + audio_hz.
The attacker Pluto's own carrier offset shifts the landing frequency by a few kHz: the recorded truth is the
measured one (bench_features.csv / spectrogram), not the requested one.
Relative level: --amplitude (0-1 of full scale); the received SIR is set by TX gain + attenuators and is MEASURED.
Record-and-replay needs no generator: capture with rx_capture --device attacker, then tx_play that file.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy import signal

from bench import dsp, iqfile
from bench.safety import DEFAULT_CONFIG, load_config


def waveform(kind: str, cfg: dict, seconds: float, audio_hz: float, width_hz: float, amplitude: float,
             seed: int) -> np.ndarray:
    fs = float(cfg["rf"]["sample_rate"])
    n = int(seconds * fs)
    t = np.arange(n) / fs
    f_if = float(cfg["rf"]["if_hz"])
    rng = np.random.default_rng(seed)
    if kind == "tone":
        return amplitude * np.exp(2j * np.pi * (f_if + audio_hz) * t)
    noise = rng.normal(size=n) + 1j * rng.normal(size=n)
    if kind == "narrow":
        lp = signal.firwin(1023, width_hz / 2, fs=fs)
        x = signal.lfilter(lp, 1.0, noise) * np.exp(2j * np.pi * (f_if + audio_hz) * t)
    elif kind == "wideband":
        lp = signal.firwin(255, dsp.AUDIO_BW / 2, fs=fs)
        x = signal.lfilter(lp, 1.0, noise) * np.exp(2j * np.pi * (f_if + dsp.AUDIO_BW / 2) * t)
    else:
        raise ValueError(kind)
    x = x / np.percentile(np.abs(x), 99.9)
    return amplitude * dsp.limit(x, 1.0)[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", required=True, choices=["tone", "narrow", "wideband"])
    ap.add_argument("--audio-hz", type=float, default=4000.0)
    ap.add_argument("--width-hz", type=float, default=100.0)
    ap.add_argument("--seconds", type=float, default=1.0, help="1 s is enough with tx_play --cyclic")
    ap.add_argument("--amplitude", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=20261120)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args()
    cfg = load_config(args.config)
    x = waveform(args.kind, cfg, args.seconds, args.audio_hz, args.width_hz, args.amplitude, args.seed)
    full = float(cfg["rf"]["pluto_full_scale"])
    data, _ = iqfile.paths(args.out)
    Path(data).parent.mkdir(parents=True, exist_ok=True)
    dsp.to_cs16(x, full).tofile(data)
    iqfile.write_meta(args.out, cfg["rf"]["sample_rate"], cfg["rf"]["center_hz"], description=f"attack {args.kind}",
                      extra={"full_scale": full, "kind": args.kind, "audio_hz": args.audio_hz,
                             "width_hz": args.width_hz, "amplitude": args.amplitude, "seed": args.seed})
    print(f"{data}: {args.kind}, {args.seconds} s")


if __name__ == "__main__":
    main()
