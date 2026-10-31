"""Positive control of the Phase C narrowband-line screen on real audio (decision 52).

For each case in positive_control.cases: take one real audio-QC observation of the satellite
(timing reliable, most canonical frames), add a narrowband interferer at each SNR in
positive_control.snr_db, run the same line screen as data/qc.py, and record whether a narrow
line covering f0 is flagged.

SNR (decision 56): snr_db = 10 log10(P_ref / P_interf), P_ref = median in-band power over all
windows of the clean observation, in [f_low, f_edge]. Interferer from attacks.interference
(tone or 4th-order Butterworth noise band, full pass). Seed from config.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
from qc import load_downlink, narrowband_lines, window_psds  # noqa: E402
from attacks.interference import apply_interference, reference_powers  # noqa: E402


def control_case(x: np.ndarray, sr: int, qc: dict, f_edge: float, ref: dict, case: dict,
                 snr_db: float, rng, downlink: list | None = None) -> dict:
    """Inject one interferer (attacks.interference, same code as Phase F) and run the line screen."""
    segs = [[0.0, len(x) / sr]]
    op = {"shape": case["shape"], "f0_hz": case["f0_hz"], "bw_hz": case.get("bw_hz"), "snr_db": snr_db,
          "segments": segs, "phase": float(rng.uniform(0, 2 * np.pi)), "subtype": case["shape"]}
    y, ev = apply_interference(x, sr, op, ref, rng)
    freqs, psd = window_psds(y, sr, qc["psd"])
    res = narrowband_lines(freqs, 10 * np.log10(psd + 1e-20), f_edge, qc, downlink)
    half = max(case.get("bw_hz") or 0.0, 2 * (freqs[1] - freqs[0])) / 2
    hit = [l for l in res["lines"] if l["f_lo_hz"] - half <= case["f0_hz"] <= l["f_hi_hz"] + half]
    return {"snr_db": snr_db, "inr_db": ev["inr_db"], "p_ref": ref["p_ref"], "p_interf": ev["p_interf"],
            "line_found": bool(hit), "flagged": any(l["narrow"] and not l["in_signal_band"] for l in hit),
            "in_signal_band": any(l["in_signal_band"] for l in hit), "line_flag_obs": res["line_flag"],
            "lines_at_f0": hit, "downlink": res["downlink"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    qc, pc = {**cfg["qc"], **cfg["canonical"]}, cfg["positive_control"]
    rows = json.loads(Path(qc["report_json"]).read_text(encoding="utf-8"))["observations"]
    out = {"seed": cfg["seed"], "cases": []}
    for ci, case in enumerate(pc["cases"]):
        cand = [r for r in rows if r["norad"] == case["norad"] and r["audio_qc"] and r["timing_reliable"]
                and r["n_frames"] > 0]
        o = sorted(cand, key=lambda r: (-r["n_frames"], r["obs_id"]))[0]
        obs_dir = Path(cfg["download"]["raw_dir"]) / str(o["obs_id"])
        x, sr = sf.read(str(obs_dir / "audio.ogg"), dtype="float32")
        p = {**qc["psd"], "chunk_windows": cfg["tables"]["features_chunk_windows"]}
        ref = reference_powers(x, sr, p, qc["psd"]["f_low_hz"], o["f_edge_hz"], o["f_edge_hz"])
        rng = np.random.default_rng(cfg["seed"] + ci)
        dl = load_downlink(Path(qc["downlink_file"])).get(case["norad"], [])
        res = [control_case(x, sr, qc, o["f_edge_hz"], ref, case, s, rng, dl) for s in pc["snr_db"]]
        out["cases"].append({**case, "obs_id": o["obs_id"], "station_id": o["station_id"],
                             "f_edge_hz": o["f_edge_hz"], "clean_line_flag": o["line_flag"], "results": res})
        print(f"[ctrl] {case} obs {o['obs_id']}: "
              + ", ".join(f"{r['snr_db']:+g} dB {'FLAG' if r['flagged'] else 'in-band' if r['in_signal_band'] else '-'}"
                          for r in res), file=sys.stderr)
        del x
    Path(pc["out_json"]).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
