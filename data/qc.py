"""Phase C: quality control and curation of downloaded observations (docs/DATASET_SPEC.md section 3).

QC is split by task (decision 37); every observation gets both flags.
  Audio QC (interference, benign variants, audio features) fails if:
    - audio missing or unreadable, sample rate != 48000 or not mono
    - |audio duration - (end - start)| > max_duration_diff_s
  Frame QC (replay, sequence) fails if fewer than min_frames canonical frames.
  TECHSAT 1B observations that fail frame QC are kept and flagged as fragments (decision 38).
Flag (no automatic rejection) for manual waterfall review, on audio-QC passes only, if a
persistent narrowband line at most line_max_width_hz wide exists: in-band bins whose
median-normalized PSD exceeds +line_threshold_db in more than line_min_frac_windows of the
1 s windows (decision 40; wider lines, e.g. FUNCUBE-1's own BPSK signal, are reported only).

Canonical frames: identical (timestamp, sha256) files (e.g. _g0 / _g1 decoder branches) count once.
timing_reliable: few seconds hold more canonical frames than the satellite can physically send.
Per-frame counters: FUNCUBE-1 seqnumber * 24 + frame type; TECHSAT 1B uint32 LE seconds
counter at bytes 6..9 of a 44-byte beacon with the ASCII marker TechSat (decision 39).

qc_status (audio use): ok / rejected / flagged (pending review) / flagged_reviewed_ok /
flagged_reviewed_rejected. Human review decisions are read from qc.review_file ({obs_id: ok | rejected}).

Outputs: qc_report.csv (one row per observation), qc_report.json, qc_report.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml
from scipy.ndimage import median_filter
from scipy.signal import welch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from select_satellites import burst_stats, frame_time  # noqa: E402
from detect.canonical import canonical_frames  # noqa: E402
from detect.counters import funcube_counter, techsat_counter  # noqa: E402,F401  (re-exported)


# ---- frames ------------------------------------------------------------------------------

def load_frames(obs_dir: Path, norad: int, qc: dict) -> list[dict]:
    """Canonical frames of one observation (detect.canonical), sorted by timestamp then filename."""
    files = []
    for path in sorted((obs_dir / "demoddata").iterdir()):
        t = frame_time(path.name)
        if t is None:
            continue
        data = path.read_bytes()
        fr = {"filename": path.name, "t": t, "sha256": hashlib.sha256(data).hexdigest(),
              "length": len(data), "counter": None}
        if norad == qc["funcube"]["norad"]:
            fr["counter"] = funcube_counter(data, qc["funcube"])
        elif norad == qc["techsat"]["norad"]:
            fr["counter"] = techsat_counter(data, qc["techsat"])
        fr["valid"] = fr["counter"] is not None
        files.append(fr)
    delay = qc["max_rewrite_delay_s"]
    return canonical_frames(files, delay[norad] if isinstance(delay, dict) else delay)


def timing_stats(frames: list[dict], max_per_second: int, fc: dict | None,
                 max_overfull_frac: float = 0.05, min_consistent: float = 0.9,
                 counter_rule: bool = True) -> dict:
    """Frames per second, timing_reliable flag, counter checks.

    timing_reliable: at most max_overfull_frac of the occupied seconds hold more frames than the
    satellite can send (an isolated late write is tolerated, batched writes are not), and, when
    counter_rule is set (FUNCUBE-1, decision 34), at least min_consistent of counter-consecutive
    pairs agree with the timestamps. Without counter_rule the consistency is reported only.
    """
    per_sec = Counter(f["t"] for f in frames)
    max_ps = max(per_sec.values()) if per_sec else 0
    overfull = sum(v > max_per_second for v in per_sec.values())
    out = {"max_frames_per_second": max_ps, "n_seconds_overfull": overfull,
           "timing_reliable": overfull <= max_overfull_frac * max(1, len(per_sec))}
    if fc is not None:
        ctr = [f for f in frames if f["counter"] is not None]
        by_ctr = sorted(ctr, key=lambda f: f["counter"])
        steps = [b["counter"] - a["counter"] for a, b in zip(by_ctr, by_ctr[1:])]
        # time vs counter consistency: dt should be about s_per_count * dcounter
        consistent = sum(1 for a, b in zip(by_ctr, by_ctr[1:])
                         if abs((b["t"] - a["t"]).total_seconds()
                                - fc["s_per_count"] * (b["counter"] - a["counter"])) <= 1.5)
        out.update(n_counter=len(ctr), counter_unique=len({f["counter"] for f in ctr}),
                   counter_step1_frac=round(sum(s == 1 for s in steps) / len(steps), 3) if steps else None,
                   counter_time_consistent_frac=round(consistent / len(steps), 3) if steps else None,
                   time_order_matches_counter=[f["counter"] for f in ctr] == sorted(f["counter"] for f in ctr))
        if counter_rule and steps and out["counter_time_consistent_frac"] < min_consistent:
            out["timing_reliable"] = False
    return out


# ---- audio ---------------------------------------------------------------------------------

def window_psds(x: np.ndarray, sr: int, p: dict):
    """Welch PSD (Hann) of 1 s windows with 0.5 s hop. Returns (freqs, psd[n_windows, n_bins])."""
    win, hop = int(p["win_s"] * sr), int(p["hop_s"] * sr)
    starts = np.arange(0, len(x) - win + 1, hop)
    out = []
    freqs = None
    for k in range(0, len(starts), p["chunk_windows"]):
        idx = starts[k:k + p["chunk_windows"]]
        block = np.stack([x[s:s + win] for s in idx])
        freqs, pxx = welch(block, fs=sr, window="hann", nperseg=p["nperseg"],
                           noverlap=p["noverlap"], axis=-1)
        out.append(pxx.astype(np.float32))
    return freqs, np.concatenate(out) if out else np.empty((0, 0))


def steepest_drop(freqs: np.ndarray, med_db: np.ndarray, p: dict) -> float:
    """Frequency of the steepest fall of the median PSD above f_low + drop_min_hz.

    The PSD is first median-filtered over drop_smooth_hz, so narrow lines and their flanks
    cannot be taken for the band edge.
    """
    k = max(1, int(round(p["drop_smooth_hz"] / (freqs[1] - freqs[0])))) | 1      # odd width
    smooth = median_filter(med_db, size=k, mode="nearest")
    slope = np.diff(smooth)
    ok = freqs[:-1] >= p["f_low_hz"] + p["drop_min_hz"]
    i = int(np.argmin(np.where(ok, slope, np.inf)))
    return float((freqs[i] + freqs[i + 1]) / 2)


def estimate_f_edge(freqs: np.ndarray, psd_db: np.ndarray, p: dict, max_iter: int = 20) -> tuple[float, float]:
    """Highest frequency whose median PSD (over windows) is within edge_drop_db of the in-band median.

    Returns (f_edge, f_drop). The search is bounded by the steepest drop f_drop of the median PSD,
    so a lossy-codec cut-off (near-zero bins above the data band, e.g. TECHSAT 1B above ~12 kHz)
    cannot dominate the reference median. The in-band median depends on the edge itself, so
    iterate to a fixed point: start with [f_low, f_drop], then recompute over [f_low, f_edge].
    """
    med = np.median(psd_db, axis=0)
    f_drop = steepest_drop(freqs, med, p)
    above_low = (freqs >= p["f_low_hz"]) & (freqs <= f_drop)
    edge = f_drop
    for _ in range(max_iter):
        ref = np.median(med[above_low & (freqs <= edge)])
        ok = np.where(above_low & (med >= ref - p["edge_drop_db"]))[0]
        new_edge = float(freqs[ok[-1]]) if len(ok) else f_drop
        if new_edge == edge:
            break
        edge = new_edge
    return edge, f_drop


def local_baseline_norm(sub_db: np.ndarray, df: float, p: dict) -> np.ndarray:
    """PSD (dB) minus, per window and bin, the median over bins within +-baseline_hz of it,
    excluding the bins within +-baseline_exclude_hz (the bin's own line). Band edges use the
    neighbours that exist."""
    k_out = int(round(p["baseline_hz"] / df))
    k_in = int(p["baseline_exclude_hz"] // df)
    offsets = [k for k in range(-k_out, k_out + 1) if abs(k) > k_in]
    n_win, n_bin = sub_db.shape
    out = np.empty_like(sub_db)
    for s in range(0, n_win, p["chunk_windows"]):
        block = sub_db[s:s + p["chunk_windows"]]
        padded = np.pad(block, ((0, 0), (k_out, k_out)), constant_values=np.nan)
        neigh = np.stack([padded[:, k_out + k:k_out + k + n_bin] for k in offsets])
        out[s:s + p["chunk_windows"]] = block - np.nanmedian(neigh, axis=0)
    return out


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, stop) index pairs of the contiguous True runs of a 1-D mask."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(edges == 1)[0], np.where(edges == -1)[0]))


def signal_bands(sub_db: np.ndarray, f_band: np.ndarray, df: float, qc: dict) -> list[list[float]]:
    """The observation's own downlink: runs wider than line_max_width_hz whose PSD, normalized by
    the per-window in-band median, exceeds +line_threshold_db in more than signal_band_min_frac
    of the windows (the signal is present only during part of a pass)."""
    glob = sub_db - np.median(sub_db, axis=1, keepdims=True)
    frac = (glob > qc["line_threshold_db"]).mean(axis=0)
    return [[round(float(f_band[a]), 1), round(float(f_band[b - 1] + df), 1)]
            for a, b in runs(frac > qc["signal_band_min_frac"])
            if (b - a) * df > qc["line_max_width_hz"]]


def width_3db(profile_db: np.ndarray, a: int, b: int) -> tuple[int, int]:
    """Bin range [lo, hi] around the peak of profile_db[a:b] that stays within 3 dB of the peak."""
    i = a + int(np.argmax(profile_db[a:b]))
    thr = profile_db[i] - 3.0
    lo, hi = i, i
    while lo > 0 and profile_db[lo - 1] >= thr:
        lo -= 1
    while hi < len(profile_db) - 1 and profile_db[hi + 1] >= thr:
        hi += 1
    return lo, hi


def narrowband_lines(freqs, psd_db, f_edge, qc: dict, downlink: list | None = None) -> dict:
    """Bins whose locally normalized PSD exceeds +threshold in more than min_frac of windows.

    Local baseline: median over +-baseline_hz around each bin, without +-baseline_exclude_hz
    (decision 43). Contiguous hot bins form one hot run. Its width is measured at -3 dB from the
    line peak of the time-averaged normalized profile (decision 55), not at the threshold.
    A line sets line_flag if it is at most line_max_width_hz wide (decision 40) and does not lie
    inside the satellite's downlink region learned from TRAIN observations (`downlink`, decision 55),
    widened by signal_band_margin_hz; such lines are tagged in_signal_band.
    observed_bands (per-observation signal-band detection) is reported only, never used for tagging.
    """
    p = qc["psd"]
    band = (freqs >= p["f_low_hz"]) & (freqs <= f_edge - p["edge_margin_hz"])
    sub = psd_db[:, band]
    f_band = freqs[band]
    df = float(freqs[1] - freqs[0])
    norm = local_baseline_norm(sub, df, p)
    frac = (norm > qc["line_threshold_db"]).mean(axis=0)
    profile = 10 * np.log10(np.mean(10 ** (norm / 10), axis=0))      # time-averaged, linear mean
    hot = frac > qc["line_min_frac_windows"]
    m = qc["signal_band_margin_hz"]
    lines = []
    for a, b in runs(hot):
        lo3, hi3 = width_3db(profile, a, b)
        lo, hi = float(f_band[lo3]), float(f_band[hi3])
        width = hi - lo + df
        narrow = width <= qc["line_max_width_hz"]
        in_sig = narrow and any(d_lo - m <= lo and hi <= d_hi + m for d_lo, d_hi in (downlink or []))
        lines.append({"f_lo_hz": round(lo, 1), "f_hi_hz": round(hi, 1), "width_hz": round(width, 1),
                      "run_lo_hz": round(float(f_band[a]), 1), "run_hi_hz": round(float(f_band[b - 1]), 1),
                      "peak_db": round(float(profile[a:b].max()), 1),
                      "max_frac": round(float(frac[a:b].max()), 3), "narrow": narrow, "in_signal_band": in_sig})
    flagged = [l for l in lines if l["narrow"] and not l["in_signal_band"]]
    return {"line_flag": bool(flagged), "n_flag_lines": len(flagged),
            "n_in_signal_band_lines": sum(l["in_signal_band"] for l in lines),
            "n_wide_lines": sum(not l["narrow"] for l in lines), "n_line_bins": int(hot.sum()),
            "max_line_frac": round(float(frac.max()), 3) if len(frac) else 0.0,
            "observed_bands": signal_bands(sub, f_band, df, qc), "downlink": downlink or [], "lines": lines}


def load_downlink(path: Path) -> dict[int, list]:
    """Per-satellite downlink region learned from TRAIN observations (data/downlink_bands.py)."""
    if not path.exists():
        print(f"[C] warning: {path} missing, no line is tagged in_signal_band", file=sys.stderr)
        return {}
    return {int(k): v["downlink"] for k, v in json.loads(path.read_text(encoding="utf-8"))["satellites"].items()}


def audio_checks(path: Path) -> dict:
    out = {"audio_ok": False, "sample_rate": None, "channels": None, "audio_duration_s": None}
    try:
        info = sf.info(str(path))
    except Exception as exc:  # unreadable or missing
        out["audio_error"] = type(exc).__name__
        return out
    out.update(sample_rate=info.samplerate, channels=info.channels,
               audio_duration_s=round(info.frames / info.samplerate, 3), audio_ok=True)
    return out


# ---- per observation -----------------------------------------------------------------------

def scheduled_s(o: dict) -> float:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(o["end"], fmt) - datetime.strptime(o["start"], fmt)).total_seconds()


def qc_observation(obs_dir: Path, qc: dict) -> dict:
    o = json.loads((obs_dir / "metadata.json").read_text(encoding="utf-8"))
    norad = o["norad_cat_id"]
    row = {"obs_id": o["id"], "norad": norad, "station_id": o["ground_station"],
           "start_utc": o["start"], "end_utc": o["end"], "scheduled_s": scheduled_s(o),
           "waterfall": f"{obs_dir.as_posix()}/waterfall.png", "audio_reasons": [], "frame_reasons": []}
    ar = row["audio_reasons"]
    row.update(audio_checks(obs_dir / "audio.ogg"))
    if not row["audio_ok"]:
        ar.append("audio missing or unreadable")
    else:
        if row["sample_rate"] != qc["sample_rate"]:
            ar.append(f"sample rate {row['sample_rate']} != {qc['sample_rate']}")
        if row["channels"] != qc["channels"]:
            ar.append(f"{row['channels']} channels, not mono")
        row["duration_diff_s"] = round(row["audio_duration_s"] - row["scheduled_s"], 3)
        if abs(row["duration_diff_s"]) > qc["max_duration_diff_s"]:
            ar.append(f"audio duration differs from schedule by {row['duration_diff_s']:+.1f} s")

    frames = load_frames(obs_dir, norad, qc)
    n_files = len(list((obs_dir / "demoddata").iterdir()))
    row.update(n_frame_files=n_files, n_frames=len(frames),
               n_valid_frames=sum(f["valid"] for f in frames),
               frame_len_median=statistics.median(f["length"] for f in frames) if frames else None,
               dup_branch_frames=sum(f["decoder_branches"] > 1 for f in frames),
               n_rewritten_frames=sum(f["n_copies"] > 0 for f in frames),
               rewrite_delays_s=sorted(d for f in frames for d in f["copy_delays_s"]))
    if len(frames) < qc["min_frames"]:
        row["frame_reasons"].append(f"{len(frames)} canonical frames < {qc['min_frames']}")
    row["audio_qc"], row["frame_qc"] = not row["audio_reasons"], not row["frame_reasons"]
    row["fragments"] = norad == qc["techsat"]["norad"] and not row["frame_qc"]
    is_fc = norad == qc["funcube"]["norad"]
    ctr_cfg = qc["funcube"] if is_fc else qc["techsat"] if norad == qc["techsat"]["norad"] else None
    row.update(timing_stats(frames, qc["max_frames_per_second"][norad], ctr_cfg,
                            qc["timing_max_overfull_frac"], qc["timing_min_counter_consistent"],
                            counter_rule=is_fc))

    # Narrowband-line screening only for observations that pass audio QC.
    row.update(line_flag=None, f_edge_hz=None, lines=[])
    if row["audio_qc"]:
        x, sr = sf.read(str(obs_dir / "audio.ogg"), dtype="float32")
        freqs, psd = window_psds(x, sr, qc["psd"])
        psd_db = 10 * np.log10(psd + 1e-20)
        f_edge, f_drop = estimate_f_edge(freqs, psd_db, qc["psd"])
        row["f_edge_hz"], row["f_drop_hz"] = round(f_edge, 1), round(f_drop, 1)
        row.update(narrowband_lines(freqs, psd_db, row["f_edge_hz"], qc, qc.get("_downlink", {}).get(norad)))
        del x, psd, psd_db
    row["_frames"] = frames
    return row


def final_status(row: dict, review: dict) -> str:
    """Status for audio use: audio-QC failures are rejected, narrow lines wait for review."""
    if row["audio_reasons"]:
        return "rejected"
    if not row["line_flag"]:
        return "ok"
    decision = review.get(row["obs_id"])
    if decision == "ok":
        return "flagged_reviewed_ok"
    if decision == "rejected":
        return "flagged_reviewed_rejected"
    return "flagged"


def remeasure_bursts(rows: list[dict], norad: int, n: int, sel: dict) -> dict:
    """Burst period on the n observations with most valid frames, from n different stations.

    Frame QC is not required (most TECHSAT 1B passes decode few frames). Cross-check: median
    step between consecutive distinct counter values (TECHSAT 1B: seconds counter).
    """
    chosen, stations = [], set()
    for r in sorted((r for r in rows if r["norad"] == norad and r.get("n_valid_frames", 0) >= 3),
                    key=lambda r: (-r["n_valid_frames"], r["obs_id"])):
        if r["station_id"] not in stations:
            chosen.append(r)
            stations.add(r["station_id"])
        if len(chosen) == n:
            break
    out = []
    for r in chosen:
        t0 = datetime.strptime(r["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        rel = [(f["t"] - t0).total_seconds() for f in r["_frames"]]
        ctr = sorted({f["counter"] for f in r["_frames"] if f["counter"] is not None})
        steps = [b - a for a, b in zip(ctr, ctr[1:])]
        out.append({"obs_id": r["obs_id"], "station_id": r["station_id"], "n_valid_frames": r["n_valid_frames"],
                    "counter_step_median": statistics.median(steps) if steps else None, "counter_steps": steps,
                    **burst_stats(rel, sel["burst_gap_s"], sel["burst_multiple_tol_s"])})
    periods = [b["period_s"] for b in out if b["period_s"] is not None]
    return {"norad": norad, "period_s": statistics.median(periods) if periods else None, "obs": out}


def reason_counts(rows: list[dict], key: str) -> dict:
    """Count reasons with their numbers stripped ('audio duration differs from schedule by -164 s' -> one key)."""
    return dict(Counter(x.split(" by ")[0].split(" < ")[0].split(" != ")[0].lstrip("0123456789+-. ")
                        for r in rows for x in r[key]))


CSV_FIELDS = ["obs_id", "norad", "station_id", "start_utc", "end_utc", "scheduled_s",
              "audio_duration_s", "duration_diff_s", "sample_rate", "channels", "n_frame_files",
              "n_frames", "n_valid_frames", "frame_len_median", "dup_branch_frames", "n_rewritten_frames",
              "max_frames_per_second", "n_seconds_overfull", "timing_reliable", "n_counter",
              "counter_step1_frac", "counter_time_consistent_frac", "f_edge_hz", "f_drop_hz", "line_flag",
              "n_flag_lines", "n_in_signal_band_lines", "n_wide_lines", "max_line_frac", "observed_bands",
              "audio_qc", "frame_qc", "fragments",
              "qc_status", "selected", "audio_reasons", "frame_reasons", "waterfall"]


def rewrite_rates(rows: list[dict]) -> list[dict]:
    """Natural rewrite rate per station: share of canonical frames that had a later byte-identical copy."""
    by = {}
    for r in rows:
        if r.get("n_frames"):
            s = by.setdefault((r["norad"], r["station_id"]), {"norad": r["norad"], "station_id": r["station_id"],
                                                              "n_obs": 0, "n_frames": 0, "n_rewritten": 0, "delays_s": []})
            s["n_obs"] += 1
            s["n_frames"] += r["n_frames"]
            s["n_rewritten"] += r["n_rewritten_frames"]
            s["delays_s"] += r["rewrite_delays_s"]
    out = sorted(by.values(), key=lambda s: (s["norad"], -s["n_rewritten"], s["station_id"]))
    for s in out:
        s["rate"] = round(s["n_rewritten"] / s["n_frames"], 3)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    qc, dl = {**cfg["qc"], **cfg["canonical"]}, cfg["download"]
    qc["_downlink"] = load_downlink(Path(qc["downlink_file"]))
    review_path = Path(qc["review_file"])
    review = {int(k): v for k, v in (yaml.safe_load(review_path.read_text(encoding="utf-8")) or {}).items()} \
        if review_path.exists() else {}
    plan = json.loads((Path(dl["report_dir"]) / "plan.json").read_text(encoding="utf-8"))
    raw = Path(dl["raw_dir"])

    rows = []
    for norad, obs in plan.items():
        for i, p in enumerate(obs, 1):
            obs_dir = raw / str(p["obs_id"])
            if not (obs_dir / "metadata.json").exists():
                rows.append({"obs_id": p["obs_id"], "norad": int(norad), "station_id": p["station"],
                             "audio_reasons": ["not downloaded"], "frame_reasons": ["not downloaded"],
                             "audio_qc": False, "frame_qc": False, "fragments": False, "line_flag": None,
                             "lines": [], "timing_reliable": None, "_frames": [], "n_frames": 0,
                             "n_valid_frames": 0})
                continue
            r = qc_observation(obs_dir, qc)
            rows.append(r)
            print(f"[C] {norad} {i}/{len(obs)} obs {p['obs_id']}: audio {r['audio_reasons'] or 'pass'}, "
                  f"frames {r['frame_reasons'] or 'pass'}", file=sys.stderr)

    # Status, then keep the first N audio-QC passes per satellite (plan order).
    kept = Counter()
    for r in rows:
        r["qc_status"] = final_status(r, review)
        limit = qc["keep_first"].get(r["norad"])
        passing = r["qc_status"] in ("ok", "flagged_reviewed_ok", "flagged")
        r["selected"] = passing and (limit is None or kept[r["norad"]] < limit)
        if r["selected"]:
            kept[r["norad"]] += 1

    bursts = [remeasure_bursts(rows, n, k, cfg["selection"]) for n, k in qc["burst_remeasure"].items()]
    summary = {}
    for norad in plan:
        rr = [r for r in rows if r["norad"] == int(norad)]
        fq = [r for r in rr if r["frame_qc"]]
        summary[norad] = {"n_obs": len(rr),
                          "n_audio_qc_pass": sum(r["audio_qc"] for r in rr),
                          "n_frame_qc_pass": len(fq),
                          "n_both_pass": sum(r["audio_qc"] for r in fq),
                          "audio_reason_counts": reason_counts(rr, "audio_reasons"),
                          "frame_reason_counts": reason_counts(rr, "frame_reasons"),
                          "n_fragments": sum(r["fragments"] for r in rr),
                          "n_flagged": sum(r["qc_status"].startswith("flagged") for r in rr),
                          "n_selected_audio": sum(r["selected"] for r in rr),
                          "n_timing_reliable_frame_qc": sum(bool(r["timing_reliable"]) for r in fq),
                          "n_timing_unreliable_frame_qc": sum(not r["timing_reliable"] for r in fq),
                          "n_timing_unreliable_all": sum(r["timing_reliable"] is False for r in rr),
                          "n_in_signal_band_lines": sum(r.get("n_in_signal_band_lines", 0) for r in rr),
                          "n_rewritten_frames": sum(r.get("n_rewritten_frames", 0) for r in rr),
                          "n_canonical_frames": sum(r["n_frames"] for r in rr)}
        edges = [r["f_edge_hz"] for r in rr if r.get("f_edge_hz")]
        drops = [r["f_drop_hz"] for r in rr if r.get("f_drop_hz")]
        if edges:
            summary[norad].update(f_edge_hz={"min": min(edges), "median": statistics.median(edges), "max": max(edges)},
                                  f_drop_hz={"min": min(drops), "median": statistics.median(drops), "max": max(drops)})
    rewrites = rewrite_rates(rows)

    with open(qc["report_csv"], "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "audio_reasons": "; ".join(r["audio_reasons"]),
                        "frame_reasons": "; ".join(r["frame_reasons"]),
                        "observed_bands": "; ".join(f"{a:.0f}-{b:.0f}" for a, b in r.get("observed_bands", []))})
    Path(qc["report_json"]).write_text(json.dumps(
        {"summary": summary, "burst_remeasure": bursts, "rewrite_rates": rewrites,
         "observations": [{k: v for k, v in r.items() if k != "_frames"} for r in rows]},
        indent=1, default=str), encoding="utf-8")

    md = ["# Phase C — QC report", "",
          "| NORAD | obs | audio QC pass | frame QC pass | both | fragments | flagged | selected (audio) "
          "| timing reliable / unreliable (frame QC) |", "|---|---|---|---|---|---|---|---|---|"]
    for n, s in summary.items():
        md.append(f"| {n} | {s['n_obs']} | {s['n_audio_qc_pass']} | {s['n_frame_qc_pass']} | {s['n_both_pass']} | "
                  f"{s['n_fragments']} | {s['n_flagged']} | {s['n_selected_audio']} | "
                  f"{s['n_timing_reliable_frame_qc']} / {s['n_timing_unreliable_frame_qc']} |")
    md += ["", "## f_edge per satellite (Hz; f_drop = steepest drop of the median PSD)", "",
           "| NORAD | f_edge min | median | max | f_drop median |", "|---|---|---|---|---|"]
    md += [f"| {n} | {s['f_edge_hz']['min']:.0f} | {s['f_edge_hz']['median']:.0f} | {s['f_edge_hz']['max']:.0f} | "
           f"{s['f_drop_hz']['median']:.0f} |" for n, s in summary.items() if "f_edge_hz" in s]
    md += ["", "## Rejection reasons (counts)", ""]
    for n, s in summary.items():
        md.append(f"- {n} audio: " + (", ".join(f"{k}: {v}" for k, v in s["audio_reason_counts"].items()) or "none"))
        md.append(f"- {n} frame: " + (", ".join(f"{k}: {v}" for k, v in s["frame_reason_counts"].items()) or "none"))
    md += ["", "## Failed observations", "", "| obs | NORAD | station | audio reasons | frame reasons |",
           "|---|---|---|---|---|"]
    md += [f"| {r['obs_id']} | {r['norad']} | {r['station_id']} | {'; '.join(r['audio_reasons'])} | "
           f"{'; '.join(r['frame_reasons'])} |" for r in rows if r["audio_reasons"] or r["frame_reasons"]]
    md += ["", "## Frame QC passes: timing", "",
           "| obs | NORAD | station | frames | valid | max/s | overfull s | counter-time consistent | timing_reliable |",
           "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['obs_id']} | {r['norad']} | {r['station_id']} | {r['n_frames']} | {r['n_valid_frames']} | "
           f"{r['max_frames_per_second']} | {r['n_seconds_overfull']} | {r.get('counter_time_consistent_frac')} | "
           f"{r['timing_reliable']} |" for r in rows if r["frame_qc"]]
    md += ["", "## Flagged for waterfall review (narrowband line <= "
           f"{qc['line_max_width_hz']:.0f} Hz)", "",
           "| obs | NORAD | station | status | narrow lines (Hz, width, max frac) | waterfall |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["qc_status"].startswith("flagged"):
            lines = "; ".join(f"{l['f_lo_hz']:.0f}-{l['f_hi_hz']:.0f} ({l['width_hz']:.0f} Hz, {l['max_frac']:.0%})"
                              for l in r["lines"] if l["narrow"] and not l["in_signal_band"])
            md.append(f"| {r['obs_id']} | {r['norad']} | {r['station_id']} | {r['qc_status']} | {lines} | {r['waterfall']} |")
    md += ["", "## Narrow lines inside the own signal band (in_signal_band, not flagged)", "",
           "| obs | NORAD | station | downlink region, TRAIN (Hz) | lines (Hz, -3 dB width, max frac) |", "|---|---|---|---|---|"]
    for r in rows:
        tagged = [l for l in r["lines"] if l["in_signal_band"]]
        if tagged:
            md.append(f"| {r['obs_id']} | {r['norad']} | {r['station_id']} | "
                      + "; ".join(f"{a:.0f}-{b:.0f}" for a, b in r["downlink"]) + " | "
                      + "; ".join(f"{l['f_lo_hz']:.0f}-{l['f_hi_hz']:.0f} ({l['width_hz']:.0f} Hz, {l['max_frac']:.0%})"
                                  for l in tagged) + " |")
    md += ["", f"## Natural rewrites per station (byte-identical copy within max_rewrite_delay_s {qc['max_rewrite_delay_s']})", "",
           "| NORAD | station | obs | canonical frames | rewritten | rate | delays (s) |", "|---|---|---|---|---|---|---|"]
    md += [f"| {s['norad']} | {s['station_id']} | {s['n_obs']} | {s['n_frames']} | {s['n_rewritten']} | {s['rate']:.1%} | "
           f"{', '.join(f'{d:g}' for d in sorted(set(s['delays_s'])))} |" for s in rewrites]
    md += ["", "## Wide lines (reported, not flagged)", "", "| obs | NORAD | lines (Hz, width) |", "|---|---|---|"]
    for r in rows:
        wide = [l for l in r["lines"] if not l["narrow"]]
        if wide:
            md.append(f"| {r['obs_id']} | {r['norad']} | "
                      + "; ".join(f"{l['f_lo_hz']:.0f}-{l['f_hi_hz']:.0f} ({l['width_hz']:.0f} Hz)" for l in wide) + " |")
    md += ["", "## Burst period remeasured (different stations)", ""]
    for b in bursts:
        md.append(f"- NORAD {b['norad']}: median period {b['period_s']} s")
        md += [f"  - obs {x['obs_id']} (station {x['station_id']}): {x['n_frames']} frames "
               f"({x['n_valid_frames']} valid), {x['n_bursts']} bursts, period {x['period_s']} s, "
               f"regular {x['frac_regular']}, counter step median {x['counter_step_median']} s" for x in b["obs"]]
    Path(qc["report_md"]).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
