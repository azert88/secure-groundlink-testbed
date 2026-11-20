"""W16 check (decision P2-02): does faint white noise on a clean TECHSAT 1B recording move the audio band edge and
make interference v2 raise false alarms, measured with the pinned environment (real soundfile / libsndfile)?

For snr_db in [40, 30] and seed in [1, 2, 3], adds Gaussian white noise with std = rms(audio) * 10**(-snr_db/20)
(numpy default_rng(seed)) to data/sample/15080917/audio.ogg, writes a copy of the observation folder (audio.ogg
re-encoded Ogg Vorbis, demoddata/, metadata.json) to out/w16/<snr>_<seed>/, plus out/w16/clean/ (re-encoded,
no noise: control for the re-encoding). Runs detect/run.py on each folder, one at a time, and prints one table.

Usage: python eval/w16_noise_check.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/sample/15080917"
OUT = ROOT / "out/w16"
SNRS_DB = [40, 30]
SEEDS = [1, 2, 3]
BLOCK = 65536


def write_obs(dst: Path, audio: np.ndarray, sr: int) -> None:
    """Copy of the observation folder with a re-encoded Ogg Vorbis audio.ogg."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    # Block-wise: a single sf.write of ~20 M samples to Vorbis overflows the stack in libsndfile 1.2.2 on Windows.
    with sf.SoundFile(str(dst / "audio.ogg"), "w", sr, 1 if audio.ndim == 1 else audio.shape[1],
                      format="OGG", subtype="VORBIS") as f:
        for i in range(0, len(audio), BLOCK):
            f.write(audio[i:i + BLOCK])
    if (SRC / "demoddata").is_dir():
        shutil.copytree(SRC / "demoddata", dst / "demoddata")
    if (SRC / "metadata.json").exists():
        shutil.copy2(SRC / "metadata.json", dst / "metadata.json")


def run_detect(obs: Path, out_json: Path) -> dict:
    cmd = [sys.executable, str(ROOT / "detect/run.py"), "--obs-dir", str(obs), "--out", str(out_json)]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 and not out_json.exists():
        raise RuntimeError(f"detect/run.py failed on {obs}: {r.stderr.strip()[-500:]}")
    return json.loads(out_json.read_text(encoding="utf-8"))


def main() -> int:
    x, sr = sf.read(str(SRC / "audio.ogg"), dtype="float32")
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    print(f"source: {SRC.relative_to(ROOT).as_posix()}  sr={sr}  shape={x.shape}  rms={rms:.5f}  "
          f"soundfile={sf.__version__}  libsndfile={sf.__libsndfile_version__}")
    OUT.mkdir(parents=True, exist_ok=True)

    cases = [("clean", None, None)] + [(f"{snr}_{seed}", snr, seed) for snr in SNRS_DB for seed in SEEDS]

    rows = []
    for name, snr, seed in cases:  # one at a time: build, write, run, release
        if snr is None:
            audio = x
        else:
            std = rms * 10 ** (-snr / 20)
            audio = (x + np.random.default_rng(seed).normal(0.0, std, size=x.shape)).astype(np.float32)
        obs = OUT / name
        write_obs(obs, audio, sr)
        res = run_detect(obs, OUT / f"{name}.json")
        a = res.get("audio", {})
        inter = res.get("detectors", {}).get("interference", {})
        alerts = res.get("fusion", {}).get("alerts", [])
        names = sorted({al.get("name", al.get("alert", "?")) for al in alerts})
        rows.append((name, res.get("verdict"), a.get("band_hz"), a.get("f_edge_hz"),
                     inter.get("n_alert_windows"), ",".join(names) or "-", float(np.max(np.abs(audio)))))
        del audio

    hdr = f"{'name':<8} {'verdict':<11} {'band_hz':<22} {'f_edge_hz':>10} {'n_alert_win':>11}  {'peak':>6}  alerts"
    print(hdr)
    print("-" * len(hdr))
    for name, verdict, band, fe, nw, al, peak in rows:
        print(f"{name:<8} {str(verdict):<11} {str(band):<22} {str(fe):>10} {str(nw):>11}  {peak:6.3f}  {al}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
