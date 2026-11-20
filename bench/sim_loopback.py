"""Offline bench simulation (no hardware): SatNOGS pass -> TX IQ -> simulated conducted channel -> station folder.

    python -m bench.sim_loopback --obs-dir data/sample/14932721 --out out/bench_sim/14932721 --snr-db 30 --cfo-hz 7300

Purpose: test the mode A chain and the station software before the Plutos are wired, and measure what the bench itself
does to the Phase 1 detectors (expected: the bench is a new "station", decision in docs/PROGRESS.md).
Channel: carrier offset (fixed + linear drift), Wiener phase noise, AWGN at an in-band SNR (24 kHz audio band).
The frame decoder is NOT simulated: frames are copied from the source pass and re-timed (`--frames-from`).
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from bench import dsp, iqfile
from bench.iq_to_satnogs import demodulate, frames_from_dir, station_lowpass, write_folder
from bench.prepare_tx import prepare
from bench.safety import DEFAULT_CONFIG, load_config


def channel(tx_base: Path, rx_base: Path, cfg: dict, snr_db: float, cfo_hz: float, drift_hz_per_s: float,
            phase_noise_rad_per_sqrt_s: float, seed: int, start: datetime) -> dict:
    rng = np.random.default_rng(seed)
    fs = float(cfg["rf"]["sample_rate"])
    full = float(cfg["rf"]["pluto_full_scale"])
    raw = iqfile.open_cs16(tx_base)
    block = int(fs) * 2
    # in-band SNR -> noise variance over the full sample-rate band
    p_sig = float(np.mean(np.abs(dsp.from_cs16(raw[:min(len(raw), block * 20)], full)) ** 2))
    sigma2 = p_sig / (10 ** (snr_db / 10)) * fs / dsp.AUDIO_BW
    rx_data, _ = iqfile.paths(rx_base)
    rx_data.parent.mkdir(parents=True, exist_ok=True)
    n0, phase = 0, 0.0
    scale = 1.0 / max(1.0, 1.2 * np.sqrt(p_sig + sigma2) * 4)   # keep headroom at the "ADC"
    with open(rx_data, "wb") as fh:
        for i in range(0, len(raw), block):
            x = dsp.from_cs16(raw[i:i + block], full)
            n = np.arange(len(x)) + n0
            f_inst = cfo_hz + drift_hz_per_s * n / fs
            pn = np.cumsum(rng.normal(0, phase_noise_rad_per_sqrt_s / np.sqrt(fs), len(x)))
            ph = phase + 2 * np.pi * np.cumsum(f_inst) / fs + pn
            phase = float(ph[-1])
            y = x * np.exp(1j * ph) + rng.normal(0, np.sqrt(sigma2 / 2), len(x)) + 1j * rng.normal(0, np.sqrt(sigma2 / 2), len(x))
            dsp.to_cs16(np.clip(y.real * scale, -1, 1) + 1j * np.clip(y.imag * scale, -1, 1), full).tofile(fh)
            n0 += len(x)
    info = {"full_scale": full, "snr_db_inband": snr_db, "cfo_hz": cfo_hz, "drift_hz_per_s": drift_hz_per_s,
            "phase_noise_rad_per_sqrt_s": phase_noise_rad_per_sqrt_s, "seed": seed, "simulated": True}
    iqfile.write_meta(rx_base, fs, cfg["rf"]["center_hz"], start=start, description="SIMULATED station capture",
                      extra=info)
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--obs-id", type=int, default=900000001)
    ap.add_argument("--snr-db", type=float, default=30.0, help="in-band (24 kHz) SNR added by the bench")
    ap.add_argument("--cfo-hz", type=float, default=7300.0, help="Pluto-to-Pluto offset (+-25 ppm each at 434 MHz)")
    ap.add_argument("--drift-hz-per-s", type=float, default=0.5)
    ap.add_argument("--phase-noise", type=float, default=0.05, help="rad / sqrt(s)")
    ap.add_argument("--seed", type=int, default=20261120)
    ap.add_argument("--start", default="2026-11-20T12:00:00Z")
    ap.add_argument("--audio-lowpass-hz", type=float, default=None, help="override rx.audio_lowpass_hz")
    ap.add_argument("--keep-iq", action="store_true", help="keep the TX / RX IQ files (about 2 MB per second each)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args()
    cfg = load_config(args.config)
    obs = Path(args.obs_dir)
    out = Path(args.out)
    work = out.parent / (out.name + "_iq")
    meta = json.loads((obs / "metadata.json").read_text(encoding="utf-8"))
    x, sr = sf.read(str(obs / "audio.ogg"), dtype="float32")
    x = x.mean(axis=1) if x.ndim > 1 else x
    tx = prepare(x, sr, cfg, work / "tx", {"source_obs": meta["id"]})
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(timezone.utc)
    ch = channel(work / "tx", work / "rx", cfg, args.snr_db, args.cfo_hz, args.drift_hz_per_s, args.phase_noise,
                 args.seed, start)
    audio, feats = demodulate(work / "rx", cfg)
    lp = args.audio_lowpass_hz if args.audio_lowpass_hz is not None else \
        (cfg["rx"].get("audio_lowpass_hz") or {}).get(int(meta["norad_cat_id"]))
    audio = station_lowpass(audio, lp)
    src_start = datetime.fromisoformat(meta["start"].replace("Z", "+00:00"))
    frames = frames_from_dir(obs / "demoddata", src_start, start)
    write_folder(out, audio, feats, frames, args.obs_id, int(meta["norad_cat_id"]), start,
                 {"simulated": True, "source_obs": meta["id"], "decoder": "copied (simulation)", "channel": ch, "tx": tx,
                  "cfo_hz_median": float(np.median([f["cfo_hz"] for f in feats])), "audio_lowpass_hz": lp})
    if not args.keep_iq:
        shutil.rmtree(work)
    cfo = [f["cfo_hz"] for f in feats]
    print(json.dumps({"out": str(out), "audio_s": len(audio) / 48000, "frames": len(frames),
                      "cfo_est_median_hz": float(np.median(cfo)), "cfo_true_hz": args.cfo_hz,
                      "pilot_snr_db_median": float(np.median([f["pilot_snr_db"] for f in feats]))}, indent=1))


if __name__ == "__main__":
    main()
