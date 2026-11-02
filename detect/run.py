"""Prototype entry point: run every detector and the fusion on ONE raw recording folder, without labels.

Folder layout (as a SatNOGS observation): audio.ogg, demoddata/ (optional), metadata.json (optional: satellite NORAD
id, pass start, observation id). Missing demoddata -> audio detectors only; missing audio -> frame detectors only;
the report says which detectors were unavailable.
Detectors: replay (Phase J), sequence (Phase K), interference v2 (Phase L, decision 92; --model to change),
narrowband line screen (Phase C screen, frozen in models/fusion/fusion.json); fusion: detect/fusion.py.
Output: one JSON (verdict, alerts with names, reasons and confidence, per-frame / per-window decisions) and an
optional short Markdown report.

Example:  python detect/run.py --obs-dir data/raw/<obs_id> --out out.json --md out.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detect import fusion as F  # noqa: E402
from detect import line_screen as LS  # noqa: E402
from detect.audio_front import common_chain, steepest_drop, window_psds  # noqa: E402
from detect.features import FEATURES, window_features  # noqa: E402
from detect.frames_io import read_metadata, stream_from_dir  # noqa: E402
from detect.hash_store import HashStore, qc_view  # noqa: E402
from detect.interference_det import Detector  # noqa: E402
from detect import replay_det, sequence_det  # noqa: E402

DEFAULT_MODEL = Path("models/interference_v2/model.pkl")


def audio_windows(path: Path, cfg: dict) -> tuple[pd.DataFrame, np.ndarray, int, dict]:
    """Same windows as detect/audio_front.windows_from_audio, plus the chained audio and f_edge for the line screen."""
    x, sr = sf.read(str(path), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    psd = cfg["qc"]["psd"]
    freqs, pxx = window_psds(x, sr, psd)
    db = 10 * np.log10(pxx + 1e-30)
    f_edge, f_drop = LS.estimate_f_edge(freqs, db, psd)
    del pxx, db
    lo, hi = float(psd["f_low_hz"]), f_drop - cfg["tables"]["feature_band_top_margin_hz"]
    y = common_chain(x, sr, cfg["tables"]["chain"])
    del x
    w = window_features(y, sr, {**psd, "chunk_windows": cfg["tables"]["features_chunk_windows"]}, lo, hi)
    df = pd.DataFrame({"win_idx": np.arange(len(w["t_start_s"])), **{k: w[k] for k in ["t_start_s"] + FEATURES}})
    return df, y, sr, {"sample_rate": int(sr), "duration_s": round(len(y) / sr, 3), "band_hz": [lo, round(hi, 2)],
                       "f_edge_hz": round(f_edge, 1), "f_drop_hz": round(f_drop, 1)}


def run(obs_dir: Path, cfg: dict, model: Path = DEFAULT_MODEL, fusion_path: Path = F.DEFAULT_FUSION,
        norad: int | None = None, full: bool = False) -> dict:
    t0 = time.perf_counter()
    tracemalloc.start()
    obs_dir = Path(obs_dir)
    meta = read_metadata(obs_dir)
    norad = norad if norad is not None else meta.get("norad")
    qc = qc_view(cfg)
    fcfg = F.load(fusion_path)
    out = {"obs_dir": obs_dir.name, "norad": norad, "detectors": {}, "unavailable": {}, "timing_s": {}}
    rep = seq = inter = lines = None
    tau_r = tau_s = float("inf")
    spc = None
    # ---- frames
    if (obs_dir / "demoddata").is_dir():
        t = time.perf_counter()
        frames, norad_f = stream_from_dir(obs_dir / "demoddata", qc, norad)
        norad = norad if norad is not None else norad_f
        rc, sc = cfg["detectors"]["replay"], cfg["detectors"]["sequence"]
        store = HashStore.load(Path(rc["store"])) if Path(rc["store"]).exists() else None
        rep = replay_det.detect(frames, norad, qc, replay_det.load_params(cfg), store, meta.get("start_utc"),
                                meta.get("obs_id"))
        tau_r = json.loads(Path(rc["threshold"]).read_text(encoding="utf-8"))["tau"]
        out["timing_s"]["replay"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        # sequence v1 (official); evidence disabled by the frozen fusion config (decision 100: content)
        off = {k: False for k in fcfg.get("sequence_disabled_evidence", [])}
        seq = sequence_det.detect(frames, norad, qc, json.loads(Path(sc["profile"]).read_text(encoding="utf-8")),
                                  use=off or None)
        tau_s = json.loads(Path(sc["threshold"]).read_text(encoding="utf-8"))["tau"]
        spc = sequence_det.sat_params(norad, qc)["spc"]
        out["timing_s"]["sequence"] = round(time.perf_counter() - t, 3)
        out["detectors"]["replay"] = {"n_frames": rep["n_frames"], "tau": tau_r, "store_used": store is not None,
                                      "n_alert_frames": sum(r["score"] >= tau_r for r in rep["frames"])}
        out["detectors"]["sequence"] = {"n_frames": seq["n_frames"], "tau": tau_s, "diagnostics": seq["diagnostics"],
                                        "n_alert_frames": sum(r["score"] >= tau_s for r in seq["frames"])}
        out["norad"] = norad
    else:
        out["unavailable"]["replay, sequence"] = "no demoddata/ folder (frame decoding with gr-satellites is a later option)"
    # ---- audio
    f_edge = band_hi = None
    if (obs_dir / "audio.ogg").exists():
        t = time.perf_counter()
        win, y, sr, info = audio_windows(obs_dir / "audio.ogg", cfg)
        f_edge, band_hi = info["f_edge_hz"], info["band_hz"][1]
        out["timing_s"]["audio_front"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        inter = Detector.load(model).run(win, norad)
        out["timing_s"]["interference"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        dl = fcfg["line_screen"]["downlink"].get(str(norad), [])
        lines = LS.screen_audio(y, sr, (info["band_hz"][0], band_hi), cfg["qc"], dl)
        del y
        out["timing_s"]["line_screen"] = round(time.perf_counter() - t, 3)
        out["audio"] = info
        out["detectors"]["interference"] = {"model": Path(model).as_posix(), "n_windows": inter["n_windows"],
                                            "tau": inter["tau"], "smoothing": inter["smoothing"],
                                            "n_alert_windows": inter["n_alert_windows"], "n_events": len(inter["events"])}
        out["detectors"]["line_screen"] = {"n_lines": len(lines["lines"]), "max_frac_windows": lines.get("max_frac_windows"),
                                           "n_flag_lines": lines.get("n_flag_lines", 0)}
    else:
        out["unavailable"]["interference, line_screen"] = "no audio.ogg"
    t = time.perf_counter()
    out["fusion"] = F.fuse(rep, seq, inter, lines, fcfg, tau_r, tau_s, spc, f_edge, band_hi)
    out["timing_s"]["fusion"] = round(time.perf_counter() - t, 4)
    out["verdict"] = out["fusion"]["verdict"]
    if full:
        out["raw"] = {"replay": rep, "sequence": seq, "interference": inter, "lines": lines}
    out["runtime_s"] = round(time.perf_counter() - t0, 2)
    out["peak_traced_mb"] = round(tracemalloc.get_traced_memory()[1] / 2 ** 20, 1)
    tracemalloc.stop()
    return out


def verify_models(lock: Path = Path("models/fusion/lock.json")) -> tuple[bool, str]:
    """Refuse to run with models, thresholds or code whose signed lock does not verify (pinned Ed25519 key)."""
    from trust import manifest as tm
    if not lock.exists():
        return False, f"{lock.as_posix()} missing: models are not signed (use --no-verify for development)"
    m = json.loads(lock.read_text(encoding="utf-8"))
    if not tm.verify(m):
        return False, f"{lock.as_posix()}: signature does not verify with the pinned public key"
    bad = tm.file_mismatches(m)
    return (not bad), (f"signed files changed: {bad}" if bad else "ok")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the detectors and the fusion on one raw recording folder (no labels)")
    ap.add_argument("--obs-dir", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--model", default=str(DEFAULT_MODEL), help="interference model (default: v2)")
    ap.add_argument("--fusion", default=str(F.DEFAULT_FUSION))
    ap.add_argument("--norad", type=int, default=None, help="satellite NORAD id if metadata.json is missing")
    ap.add_argument("--out", default=None, help="JSON output (default: stdout)")
    ap.add_argument("--md", default=None, help="short Markdown report")
    ap.add_argument("--full", action="store_true", help="include the raw per-frame / per-window detector outputs")
    ap.add_argument("--no-verify", action="store_true", help="skip the signed-model check (development only)")
    a = ap.parse_args(argv)
    if not a.no_verify:
        ok, msg = verify_models()
        if not ok:
            print(json.dumps({"error": msg}), file=sys.stderr)
            return 2
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    res = run(Path(a.obs_dir), cfg, Path(a.model), Path(a.fusion), a.norad, a.full)
    txt = json.dumps(res, indent=1, default=str)
    Path(a.out).write_text(txt + "\n", encoding="utf-8") if a.out else print(txt)
    if a.md:
        Path(a.md).write_text(F.markdown(res), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
