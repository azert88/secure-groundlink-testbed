"""Phase D: clean tables (docs/DATASET_SPEC.md section 4). Deterministic, keyed by (obs_id, variant_id).

Inputs: data/raw/ (never modified), data/qc_report.json (Phase C), data/splits.json if present.
Outputs (tables.out_dir): observations.csv, frames.csv, windows.csv; labels in tables.labels_csv.

Observations: every observation kept for audio (Phase C `selected`) or passing frame QC.
Frames (all such observations): canonical stream from detect.canonical (decision 45/50),
  sorted by timestamp then filename; burst_id: new burst when the gap is > burst_gap_s;
  dt_prev_s = time since the previous frame; dt_burst_s = start of this burst - start of the
  previous burst. When timing_reliable is False (decision 46): dt_prev_s, dt_burst_s and
  burst_active are NaN, and FUNCUBE-1 frames are ordered by counter (frames without a counter last).
Windows (audio-QC passes only): common chain (spec 6.1: x headroom, Ogg Vorbis re-encode at a
  fixed level, decode), then detect.features over [f_low, f_drop - margin] (decision 48).
Labels: clean variant, every frame and window is_attack = 0.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
from qc import load_frames  # noqa: E402
from detect.counters import counter_codec  # noqa: E402
from detect.features import FEATURES, burst_active, window_features  # noqa: E402
from attacks.common import burst_intervals, clean_label, finalize_frames  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"


def common_chain(x: np.ndarray, sr: int, chain: dict) -> np.ndarray:
    """Spec 6.1: multiply by the fixed headroom, encode Ogg Vorbis, decode (deterministic decode).

    Written in small blocks: one large Vorbis write overflows the native stack (libsndfile on Windows).
    """
    buf = io.BytesIO()
    y = (x * chain["headroom"]).astype(np.float32)
    with sf.SoundFile(buf, "w", sr, 1, format="OGG", subtype="VORBIS",
                      compression_level=chain["vorbis_compression_level"]) as fh:
        for i in range(0, len(y), 4096):
            fh.write(y[i:i + 4096])
    buf.seek(0)
    y, _ = sf.read(buf, dtype="float32")
    return y


def load_clean_frames(obs_dir: Path, norad: int, qc: dict, t0: datetime) -> list[dict]:
    """Canonical frame stream (detect.canonical) as attack-ready dicts: t in seconds from t0, hex payload,
    clean labels (attacks.common)."""
    out = []
    for f in load_frames(obs_dir, norad, qc):
        out.append({"t": (f["t"] - t0).total_seconds(), "filename": f["filename"],
                    "hex": (obs_dir / "demoddata" / f["filename"]).read_bytes().hex(),
                    "decoder_branches": f["decoder_branches"], "n_copies": f["n_copies"],
                    "copy_delays_s": list(f["copy_delays_s"]), **clean_label()})
    return out


def frame_rows(frames: list[dict], t0: datetime, timing_reliable: bool, order_by_counter: bool, gap_s: float,
               counter_fn) -> list[dict]:
    """frames.csv rows (any variant): attacks.common.finalize_frames + table formatting."""
    rows = []
    for r in finalize_frames(frames, timing_reliable, order_by_counter, gap_s, counter_fn):
        rows.append({**r, "timestamp_utc": (t0 + timedelta(seconds=r["t"])).strftime(FMT), "t_rel_s": r["t"],
                     "copy_delays_s": ";".join(f"{d:g}" for d in r["copy_delays_s"])})
    return rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_observation(o: dict, cfg: dict, splits: dict) -> tuple[dict, list[dict], dict | None]:
    """observations.csv row, frames.csv rows and window features of one observation."""
    qc, tb = {**cfg["qc"], **cfg["canonical"]}, cfg["tables"]
    obs_dir = Path(cfg["download"]["raw_dir"]) / str(o["obs_id"])
    meta = json.loads((obs_dir / "metadata.json").read_text(encoding="utf-8"))
    t0 = datetime.strptime(meta["start"], FMT).replace(tzinfo=timezone.utc)
    is_fc = o["norad"] == qc["funcube"]["norad"]
    frows = frame_rows(load_clean_frames(obs_dir, o["norad"], qc, t0), t0, bool(o["timing_reliable"]), is_fc,
                       tb["burst_gap_s"], counter_codec(o["norad"], qc)[0])
    band_hi = (o["f_drop_hz"] - tb["feature_band_top_margin_hz"]) if o.get("f_drop_hz") else None
    obs_row = {"obs_id": o["obs_id"], "norad": o["norad"], "satellite": cfg["_names"][o["norad"]],
               "mode": f"{meta['transmitter_mode']} {meta['transmitter_baud']:g}",
               "station_id": o["station_id"], "start_utc": meta["start"], "end_utc": meta["end"],
               "duration_s": o.get("audio_duration_s"), "sample_rate": o.get("sample_rate"),
               "n_frames": len(frows), "audio_sha256": sha256_file(obs_dir / "audio.ogg"),
               "f_edge_hz": o.get("f_edge_hz"), "f_drop_hz": o.get("f_drop_hz"),
               "feature_band_lo_hz": qc["psd"]["f_low_hz"], "feature_band_hi_hz": band_hi,
               "qc_status": o["qc_status"], "audio_qc": int(o["audio_qc"]), "frame_qc": int(o["frame_qc"]),
               "fragments": int(o["fragments"]), "timing_reliable": int(bool(o["timing_reliable"])),
               "selected_audio": int(o["selected"]), "n_rewritten_frames": o["n_rewritten_frames"],
               "split": splits.get(o["obs_id"], "")}
    win = None
    if o["audio_qc"]:
        x, sr = sf.read(str(obs_dir / "audio.ogg"), dtype="float32")
        y = common_chain(x, sr, tb["chain"])
        del x
        p = {**qc["psd"], "chunk_windows": tb["features_chunk_windows"]}
        win = window_features(y, sr, p, qc["psd"]["f_low_hz"], band_hi)
        del y
        if o["timing_reliable"]:
            win["burst_active"] = burst_active(win["t_start_s"], qc["psd"]["win_s"],
                                               burst_intervals(frows, tb["burst_margin_s"]))
        else:
            win["burst_active"] = np.full(len(win["t_start_s"]), np.nan)
    return obs_row, frows, win


FRAME_COLS = ["obs_id", "variant_id", "idx", "filename", "timestamp_utc", "t_rel_s", "burst_id", "dt_prev_s",
              "dt_burst_s", "length", "sha256", "hex", "counter", "valid", "decoder_branches", "n_copies",
              "copy_delays_s"]
WINDOW_COLS = ["obs_id", "variant_id", "win_idx", "t_start_s", "burst_active"] + FEATURES
LABEL_COLS = ["obs_id", "variant_id", "unit", "index", "is_attack", "attack_type", "subtype", "snr_db",
              "inr_db", "overlap_frac", "evasion"]


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, float_format="%.6g", lineterminator="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--obs", type=int, nargs="*", help="only these observations (debug); full run otherwise")
    ap.add_argument("--out-dir", help="override tables.out_dir (labels go next to it)")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg["_names"] = {39444: "FUNCUBE-1", 25397: "TECHSAT 1B"}
    tb = cfg["tables"]
    out = Path(args.out_dir or tb["out_dir"])
    labels_path = out / "labels.csv" if args.out_dir else Path(tb["labels_csv"])
    qcr = json.loads(Path(cfg["qc"]["report_json"]).read_text(encoding="utf-8"))
    sp_path = Path(cfg["splits"]["out_json"])
    splits = {}
    if sp_path.exists():
        for sat in json.loads(sp_path.read_text(encoding="utf-8"))["satellites"].values():
            for name, ids in sat["splits"].items():
                splits.update({i: name for i in ids})
    obs = sorted((o for o in qcr["observations"] if o.get("selected") or o.get("frame_qc")),
                 key=lambda o: o["obs_id"])
    if args.obs:
        obs = [o for o in obs if o["obs_id"] in set(args.obs)]
    v = tb["clean_variant"]
    o_rows, f_rows, w_frames, l_rows = [], [], [], []
    for k, o in enumerate(obs, 1):
        orow, frows, win = build_observation(o, cfg, splits)
        o_rows.append(orow)
        for r in frows:
            f_rows.append({"obs_id": o["obs_id"], "variant_id": v, **r})
            l_rows.append({"obs_id": o["obs_id"], "variant_id": v, "unit": "frame", "index": r["idx"],
                           "is_attack": 0, "overlap_frac": 0.0, "evasion": 0})
        if win is not None:
            n = len(win["t_start_s"])
            w = pd.DataFrame({"obs_id": o["obs_id"], "variant_id": v, "win_idx": np.arange(n),
                              **{c: win[c] for c in ["t_start_s", "burst_active"] + FEATURES}})
            w_frames.append(w)
            l_rows += [{"obs_id": o["obs_id"], "variant_id": v, "unit": "window", "index": i, "is_attack": 0,
                        "overlap_frac": 0.0, "evasion": 0} for i in range(n)]
        print(f"[D] {k}/{len(obs)} obs {o['obs_id']}: {len(frows)} frames, "
              f"{0 if win is None else len(win['t_start_s'])} windows", file=sys.stderr)

    write_csv(pd.DataFrame(o_rows), out / "observations.csv")
    write_csv(pd.DataFrame(f_rows, columns=FRAME_COLS).astype({"counter": "Int64"}), out / "frames.csv")
    write_csv(pd.concat(w_frames, ignore_index=True)[WINDOW_COLS] if w_frames else pd.DataFrame(columns=WINDOW_COLS),
              out / "windows.csv")
    write_csv(pd.DataFrame(l_rows, columns=LABEL_COLS), labels_path)
    summary = {"n_observations": len(o_rows), "n_frames": len(f_rows),
               "n_windows": int(sum(len(w) for w in w_frames)), "n_labels": len(l_rows),
               "by_satellite": {str(n): {"obs": sum(r["norad"] == n for r in o_rows),
                                         "with_windows": sum(r["norad"] == n and r["audio_qc"] == 1 for r in o_rows),
                                         "frames": sum(r["n_frames"] for r in o_rows if r["norad"] == n)}
                                for n in sorted({r["norad"] for r in o_rows})}}
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
