"""Mode A, step 1 (no hardware): turn a SatNOGS audio.ogg into an IQ file for the Pluto "satellite".

    python -m bench.prepare_tx --obs-dir data/sample/14932721 --out out/bench/tx_14932721

Writes <out>.sigmf-data / .sigmf-meta (ci16_le at rf.sample_rate). Runs on the PC or a Pi; no radio is opened.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from bench import dsp, iqfile
from bench.safety import DEFAULT_CONFIG, load_config


def prepare(audio: np.ndarray, sr: int, cfg: dict, out_base: Path, extra: dict | None = None) -> dict:
    if sr != dsp.AUDIO_SR:
        raise ValueError(f"audio sample rate {sr} != {dsp.AUDIO_SR}")
    z, gain = dsp.scaled_analytic(audio.astype(np.float64), float(cfg["rf"]["audio_peak"]))
    up = dsp.Upconverter(cfg)
    fs_scale = float(cfg["rf"]["pluto_full_scale"])
    data_path, _ = iqfile.paths(out_base)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    peak, clipped, n = 0.0, 0, 0
    with open(data_path, "wb") as fh:
        for blk in dsp.chunks(z, dsp.AUDIO_SR):                  # 1 s of audio per chunk
            iq = up.process(blk)
            peak = max(peak, float(np.max(np.abs(iq))))
            iq, c = dsp.limit(iq)
            clipped, n = clipped + c, n + len(iq)
            dsp.to_cs16(iq, fs_scale).tofile(fh)
    info = {"full_scale": fs_scale, "audio_gain": gain, "iq_peak_before_limit": peak, "clipped_fraction": clipped / max(n, 1),
            "duration_s": len(audio) / sr, **(extra or {})}
    iqfile.write_meta(out_base, cfg["rf"]["sample_rate"], cfg["rf"]["center_hz"],
                      description="mode A: SatNOGS audio as USB + CW pilot", extra=info)
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--out", required=True, help="output base path (no extension)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args()
    cfg = load_config(args.config)
    obs = Path(args.obs_dir)
    x, sr = sf.read(str(obs / "audio.ogg"), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    meta = json.loads((obs / "metadata.json").read_text(encoding="utf-8"))
    info = prepare(x, sr, cfg, Path(args.out),
                   {"source_obs": meta.get("id"), "norad": meta.get("norad_cat_id"), "source_start": meta.get("start")})
    print(json.dumps(info, indent=1))


if __name__ == "__main__":
    main()
