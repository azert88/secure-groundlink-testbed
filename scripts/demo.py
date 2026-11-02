"""Offline demo (about 1-2 minutes, CPU only): attack the sample recordings, then run the detectors and the fusion.

For each observation in data/sample/ (one FUNCUBE-1, one TECHSAT 1B pass, sanitized metadata):
  1. detect/run.py on the CLEAN recording            -> expected verdict: accept (no false alarm)
  2. attacks/run_attack.py standard (replay + one sequence anomaly + narrowband interference), seeded
  3. detect/run.py on the ATTACKED recording         -> verdict, alerts and reasons, compared with the truth file
Outputs in out/demo/ (attacked folders, truth files, spectrograms, detector JSON / Markdown, demo_summary.json).
Same as `make demo`.  Usage:  python scripts/demo.py [--seed 7] (observation k uses seed + k) [--only 14932721]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sh(args: list[str]) -> None:
    r = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"command failed: {' '.join(args)}")


def detect(folder: Path, out: Path) -> dict:
    sh(["detect/run.py", "--obs-dir", folder.as_posix(), "--out", f"{out.as_posix()}.json", "--md", f"{out.as_posix()}.md"])
    return json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))


def show(title: str, res: dict) -> None:
    fu = res["fusion"]
    print(f"  {title}: verdict {fu['verdict'].upper()} (confidence attacked {fu['confidence_attacked']:.2f}), "
          f"{len(fu['alerts'])} alert(s), {res['runtime_s']:.0f} s")
    for a in fu["alerts"]:
        print(f"    - {a['name']} [{a['decision']}, conf {a['confidence']:.2f}]: {a['reason'][:150]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline demo on data/sample/")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sample-dir", default="data/sample")
    ap.add_argument("--out-dir", default="out/demo")
    ap.add_argument("--only", type=int, default=None, help="one observation id of the sample")
    a = ap.parse_args(argv)
    sample, out = ROOT / a.sample_dir, ROOT / a.out_dir
    out.mkdir(parents=True, exist_ok=True)
    obs = sorted(p for p in sample.iterdir() if (p / "metadata.json").exists() and (a.only is None or p.name == str(a.only)))
    if not obs:
        raise SystemExit(f"no observation in {sample}")
    t0, summary = time.perf_counter(), []
    for k, p in enumerate(obs):
        sat = json.loads((p / "metadata.json").read_text(encoding="utf-8"))
        print(f"\n=== observation {p.name} ({sat.get('tle0', '').lstrip('0 ').strip()}, NORAD {sat['norad_cat_id']})")
        clean = detect(p, out / f"{p.name}_clean.detect")
        show("clean recording", clean)
        att = out / f"{p.name}_standard"
        sh(["attacks/run_attack.py", "standard", "--obs-dir", p.as_posix(), "--out", att.as_posix(), "--seed", str(a.seed + k)])
        truth = json.loads(Path(f"{att}.truth.json").read_text(encoding="utf-8"))
        ops = truth["operations"]
        print("  attack applied (truth file, not read by the detector): " + "; ".join(
            f"replay of {o['length']} frames ({o['delta_s']:+.0f} s)" if o["type"] == "replay" else
            f"sequence {o['subtype']}" if o["type"] == "sequence" else
            f"interference {o['shape']} at {o['f0_hz']:.0f} Hz, snr_db {o['snr_db']:+.1f}" for o in ops))
        res = detect(att, out / f"{p.name}_standard.detect")
        show("attacked recording", res)
        found = {al["alert"] for al in res["fusion"]["alerts"]}
        summary.append({"obs_id": p.name, "clean_verdict": clean["fusion"]["verdict"],
                        "attacked_verdict": res["fusion"]["verdict"], "attacks": [o["type"] for o in ops],
                        "alert_kinds": sorted(found), "spectrogram": f"{att.relative_to(ROOT).as_posix()}.spectrogram.png"})
    (out / "demo_summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(f"\nDemo done in {time.perf_counter() - t0:.0f} s. Outputs: {out.relative_to(ROOT).as_posix()}/ "
          "(detector reports *.detect.md, spectrograms *.spectrogram.png, demo_summary.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
