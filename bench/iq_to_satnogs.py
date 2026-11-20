"""Ground station, offline step: IQ recording -> SatNOGS-format observation folder for detect/run.py.

    python -m bench.iq_to_satnogs --iq out/bench/rx_001 --norad 39444 --obs-id 900000001 --out out/bench/obs_001

Output folder (same layout as a SatNOGS download, README section 3):
  audio.ogg            48 kHz mono Ogg Vorbis, USB-demodulated with pilot-based CFO correction
  demoddata/           one file per decoded frame, data_<obs-id>_<YYYY-MM-DDTHH-MM-SS>_g<k>
  metadata.json        id, norad_cat_id, start, end (+ "bench" block, ignored by the detectors)
  bench_features.csv   per-second radio evidence: CFO, pilot SNR, IQ power (not read by the Phase 1 detectors)
  bench_frames.csv     per-frame reception time in milliseconds (the 1 s filename resolution is a SatNOGS limit)

Frame decoding uses gr-satellites (`--wavfile ... --kiss_out ... --start_time ... --throttle`, real-time replay so the
KISS timestamps are right). `--frames-from DIR` instead copies frames from an existing demoddata folder, re-timed to
this capture (simulation only: no decoder in the loop; reported as such).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

from bench import dsp, iqfile, kiss
from bench.safety import DEFAULT_CONFIG, load_config
from detect.frames_io import filename_time


def station_lowpass(audio: np.ndarray, cutoff_hz: float | None) -> np.ndarray:
    """Station audio filter (zero-phase, 8th-order Butterworth); None = no filter."""
    if not cutoff_hz:
        return audio
    sos = signal.butter(8, float(cutoff_hz), fs=dsp.AUDIO_SR, output="sos")
    return signal.sosfiltfilt(sos, audio).astype(np.float32)


def demodulate(iq_base: Path, cfg: dict) -> tuple[np.ndarray, list[dict]]:
    raw = iqfile.open_cs16(iq_base)
    down = dsp.Downconverter(cfg)
    meta = iqfile.read_meta(iq_base)["global"]
    fs_scale = float(meta.get("bench:full_scale", cfg["rf"]["pluto_full_scale"]))
    block = int(cfg["rf"]["sample_rate"]) * 2                      # 1 s of interleaved int16
    parts = []
    for i in range(0, len(raw) - block + 1, block):                # drop a trailing partial second
        parts.append(down.process(dsp.from_cs16(raw[i:i + block], fs_scale)).astype(np.float32))
    audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    ref = float(np.percentile(np.abs(audio), 99.99)) if len(audio) else 1.0
    return (audio * (0.5 / ref if ref > 0 else 1.0)).astype(np.float32), down.features


def frame_name(obs_id: int, t: datetime, used: dict) -> str:
    stamp = t.strftime("%Y-%m-%dT%H-%M-%S")
    k = used.get(stamp, 0)
    used[stamp] = k + 1
    return f"data_{obs_id}_{stamp}_g{k}"


def decode_gr_satellites(audio_path: Path, sat_name: str, start: datetime, exe: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        x, sr = sf.read(str(audio_path), dtype="float32")
        sf.write(str(wav), x, sr, subtype="FLOAT")
        kfile = Path(td) / "frames.kiss"
        cmd = [exe, sat_name, "--wavfile", str(wav), "--samp_rate", "48e3", "--kiss_out", str(kfile),
               "--start_time", start.strftime("%Y-%m-%dT%H:%M:%S"), "--throttle"]
        print("running (real time, as long as the pass):", " ".join(cmd))
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        frames = kiss.read_frames(kfile.read_bytes()) if kfile.exists() else []
    return [{"t": datetime.fromtimestamp(f["t_ms"] / 1000, timezone.utc) if f["t_ms"] else None,
             "t_ms": f["t_ms"], "data": f["data"]} for f in frames]


def frames_from_dir(src: Path, src_start: datetime, start: datetime, delay_s: float = 0.0) -> list[dict]:
    out = []
    for p in sorted(src.iterdir()):
        t0 = filename_time(p.name)
        if t0 is None:
            continue
        t = start + (t0 - src_start) + timedelta(seconds=delay_s)
        out.append({"t": t, "t_ms": int(t.timestamp() * 1000), "data": p.read_bytes()})
    return out


def write_folder(out: Path, audio: np.ndarray, features: list[dict], frames: list[dict], obs_id: int, norad: int,
                 start: datetime, bench_info: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sf.write(str(out / "audio.ogg"), audio, dsp.AUDIO_SR, format="OGG", subtype="VORBIS")
    dd = out / "demoddata"
    if dd.exists():
        shutil.rmtree(dd)
    dd.mkdir()
    used: dict = {}
    rows = []
    for f in sorted((f for f in frames if f["t"] is not None), key=lambda f: f["t"]):
        name = frame_name(obs_id, f["t"], used)
        (dd / name).write_bytes(f["data"])
        rows.append({"filename": name, "t_ms": f["t_ms"], "n_bytes": len(f["data"])})
    with open(out / "bench_frames.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "t_ms", "n_bytes"])
        w.writeheader()
        w.writerows(rows)
    if features:
        with open(out / "bench_features.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(features[0]))
            w.writeheader()
            w.writerows(features)
    end = start + timedelta(seconds=len(audio) / dsp.AUDIO_SR)
    meta = {"id": obs_id, "norad_cat_id": norad, "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "bench": bench_info}
    (out / "metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iq", required=True, help="IQ base path (no extension)")
    ap.add_argument("--norad", type=int, required=True)
    ap.add_argument("--obs-id", type=int, required=True, help="bench observation id (use 9xxxxxxxx, never a SatNOGS id)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--frames-from", default=None, help="SIMULATION: copy frames from this demoddata folder")
    ap.add_argument("--frames-src-start", default=None, help="SIMULATION: source observation start (ISO, Z)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    meta = iqfile.read_meta(args.iq)
    start = iqfile.start_time(meta)
    audio, feats = demodulate(Path(args.iq), cfg)
    audio = station_lowpass(audio, (cfg["rx"].get("audio_lowpass_hz") or {}).get(args.norad))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sf.write(str(out / "audio.ogg"), audio, dsp.AUDIO_SR, format="OGG", subtype="VORBIS")
    if args.frames_from:
        src_start = datetime.fromisoformat(args.frames_src_start.replace("Z", "+00:00"))
        frames, decoder = frames_from_dir(Path(args.frames_from), src_start, start), "copied (simulation)"
    else:
        name = cfg["decoder"]["names"][args.norad]
        frames, decoder = decode_gr_satellites(out / "audio.ogg", name, start, cfg["decoder"]["gr_satellites"]), "gr-satellites"
    write_folder(out, audio, feats, frames, args.obs_id, args.norad, start,
                 {"iq": str(Path(args.iq).name), "decoder": decoder,
                  "cfo_hz_median": float(np.median([f["cfo_hz"] for f in feats])) if feats else None})
    print(f"{out}: {len(audio) / dsp.AUDIO_SR:.0f} s audio, {len(frames)} frames ({decoder})")


if __name__ == "__main__":
    main()
