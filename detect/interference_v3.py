"""Interference v3: per-station clean templates (S3, docs/S3_PLAN.md, signed gate docs/GATES_PHASE2_S3.md).

Interference v2 (detect/interference_det.py, models/interference_v2) is unchanged and runs first; v3 adds a template
alert and ORs it with v2's: v3 alert = v2 alert (3 of 5 at its tau) OR template alert (3 of 5 at tau_T).

Template of a scored pass p (station s, satellite n): built from the up to 3 most recent passes of (s, n) with start
strictly before p's start that pass Phase 1 audio QC (`select_enrollment`). Each enrollment pass goes through the
common chain (detect/audio_front.common_chain), then the Welch window PSD of detect/features.window_spectra (same
estimator as the v1 / v2 features). Band B = [f_low, median f_drop of the enrollment passes - margin] (one band per
receiver, W7). In B, each window spectrum in dB minus its band median (gain-invariant) is r_w(f); the template is the
per-bin median m(f) and spread sigma(f) = max(1.4826 MAD over all enrollment windows, sigma_min_db).
Window score T_w = max over f of the mean of (r_w(f) - m(f)) / sigma(f) over `smooth_bins` adjacent bins: it does not
use the scored recording's own median, so a whole-pass line stays visible (W6), and sigma is the receiver's own
spread (W5, W10). k = 0 (no earlier pass): v2 only (fallback).

Station identity: v3 needs to know which receiver it runs on (the station's own templates). The Phase 1 detectors
never read station data (detect/frames_io.read_metadata); v3 takes it from metadata.json `ground_station` or from
the caller.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from detect.audio_front import common_chain, steepest_drop, window_psds
from detect.features import window_spectra
from detect.interference_det import k_of_n

PARAMS = Path("models/interference_v3/params.json")
THRESHOLD = Path("models/interference_v3/threshold.json")
HOLDOUT_START = "2026-11-01T00:00:00Z"
FMT = "%Y-%m-%dT%H:%M:%SZ"
SPECTRA_CACHE = Path("data/cache/s3_spectra")


def load_params(path: Path = PARAMS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------- spectra
def psd_db_full(y: np.ndarray, sr: int, psd: dict, chunk_windows: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(freqs, t_start_s, db[n_windows, n_bins]) over 0 .. sr/2, the window PSD of detect/features.py, float32."""
    t, rows, freqs = [], [], np.fft.rfftfreq(psd["nperseg"], 1 / sr)
    for freqs, idx, p, _ in window_spectra(y, sr, {**psd, "chunk_windows": chunk_windows}):
        t.append(idx / sr)
        rows.append((10 * np.log10(p + 1e-30)).astype(np.float32))
    if not rows:
        return freqs, np.empty(0), np.empty((0, len(freqs)), np.float32)
    return freqs, np.concatenate(t), np.concatenate(rows)


def f_drop_raw(x: np.ndarray, sr: int, psd: dict) -> float:
    """f_drop of the raw (not chained) audio, as detect/audio_front.feature_band and data/qc.py."""
    freqs, pxx = window_psds(x, sr, psd)
    return steepest_drop(freqs, np.median(10 * np.log10(pxx + 1e-30), axis=0), psd)


def pass_spectra(audio_path: Path, cfg: dict, cache: Path | None = None) -> dict:
    """Spectra of one enrollment pass as received (raw audio -> common chain), cached as float16 npz."""
    key = Path(cache) / f"{Path(audio_path).parent.name}.npz" if cache else None
    if key is not None and key.exists():
        z = np.load(key)
        return {"freqs": z["freqs"], "t": z["t"], "db": z["db"].astype(np.float32), "f_drop": float(z["f_drop"])}
    x, sr = sf.read(str(audio_path), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    psd = cfg["qc"]["psd"]
    fd = f_drop_raw(x, sr, psd)
    y = common_chain(x, sr, cfg["tables"]["chain"])
    del x
    freqs, t, db = psd_db_full(y, sr, psd, cfg["tables"]["features_chunk_windows"])
    if key is not None:
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_name(key.stem + ".part.npz")
        np.savez(tmp, freqs=freqs, t=t, db=db.astype(np.float16), f_drop=fd)
        tmp.replace(key)
    return {"freqs": freqs, "t": t, "db": db, "f_drop": fd}


# ------------------------------------------------------------------------------------------- enrollment
def select_enrollment(scored_start: str, station: int, norad: int, archive: list[dict], k: int = 3) -> list[dict]:
    """Up to k most recent archive passes of (station, norad), start strictly before scored_start, audio QC passed,
    never in the Phase 2 held-out window. archive rows: obs_id, station, norad, start (ISO Z), audio_qc."""
    rows = [a for a in archive if a["station"] == station and a["norad"] == norad and a["audio_qc"]
            and a["start"] < scored_start and a["start"] < HOLDOUT_START]
    return sorted(rows, key=lambda a: (a["start"], a["obs_id"]), reverse=True)[:k]


def build_archive(dirs=(Path("data/raw"), Path("data/raw_enroll")),
                  qc_files=(Path("data/qc_report.csv"), Path("data/raw_enroll/qc.csv"))) -> list[dict]:
    """Local passes with metadata.json + audio.ogg and a recorded Phase 1 audio QC result."""
    qc = {}
    for f in qc_files:
        if Path(f).exists():
            for r in csv.DictReader(open(f, newline="", encoding="utf-8")):
                qc[int(r["obs_id"])] = str(r["audio_qc"]) == "True"
    out = []
    for d in dirs:
        for m in sorted(Path(d).glob("*/metadata.json")):
            if not (m.parent / "audio.ogg").exists():
                continue
            o = json.loads(m.read_text(encoding="utf-8"))
            out.append({"obs_id": int(o["id"]), "station": int(o["ground_station"]), "norad": int(o["norad_cat_id"]),
                        "start": o["start"], "audio_qc": qc.get(int(o["id"]), False),
                        "audio": (m.parent / "audio.ogg").as_posix()})
    return out


# ------------------------------------------------------------------------------------------- template
def fit_template(passes: list[dict], p: dict, f_low: float) -> dict:
    """Template from enrollment pass spectra (pass_spectra outputs, same freqs)."""
    freqs = passes[0]["freqs"]
    hi = float(np.median([q["f_drop"] for q in passes])) - p["band_top_margin_hz"]
    band = (freqs >= f_low) & (freqs <= hi)
    r = np.concatenate([q["db"][:, band] - np.median(q["db"][:, band], axis=1, keepdims=True) for q in passes])
    m = np.median(r, axis=0)
    sigma = np.maximum(1.4826 * np.median(np.abs(r - m), axis=0), p["sigma_min_db"])
    return {"band_hz": [float(f_low), hi], "band": band, "m": m.astype(np.float32), "sigma": sigma.astype(np.float32),
            "n_passes": len(passes), "n_windows": int(len(r))}


def score(db: np.ndarray, freqs: np.ndarray, tmpl: dict, p: dict) -> tuple[np.ndarray, np.ndarray]:
    """T_w and the frequency of its maximum, per window of one recording (db from psd_db_full)."""
    band = tmpl["band"]
    r = db[:, band] - np.median(db[:, band], axis=1, keepdims=True)
    e = (r - tmpl["m"]) / tmpl["sigma"]
    w = int(p["smooth_bins"])
    if w > 1:
        c = np.cumsum(np.pad(e, ((0, 0), (1, 0))), axis=1, dtype=np.float64)
        e = (c[:, w:] - c[:, :-w]) / w
    i = e.argmax(axis=1)
    fb = freqs[band]
    return e.max(axis=1).astype(np.float64), fb[i + (w - 1) // 2]


def decide(v2_alert: np.ndarray, t_score: np.ndarray | None, tau_t: float, k: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(v3 alert, template alert) per window; t_score None (k = 0) -> v2 alone."""
    v2_alert = np.asarray(v2_alert, dtype=bool)
    if t_score is None:
        return v2_alert, np.zeros(len(v2_alert), dtype=bool)
    t_alert = k_of_n(np.asarray(t_score) >= tau_t, k, n)
    return v2_alert | t_alert, t_alert


def events(alert: np.ndarray, t_start: np.ndarray, hop: float) -> list[dict]:
    out, i = [], 0
    while i < len(alert):
        if not alert[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(alert) and alert[j + 1]:
            j += 1
        out.append({"i0": i, "i1": j, "t_start_s": float(t_start[i]), "t_end_s": float(t_start[j] + 2 * hop)})
        i = j + 1
    return out


# ------------------------------------------------------------------------------------------- one recording (prototype)
def run_on_recording(v2_res: dict, y: np.ndarray, sr: int, station: int | None, norad: int | None, start_utc: str | None,
                     cfg: dict, archive: list[dict] | None = None, params: dict | None = None,
                     threshold: dict | None = None) -> dict:
    """v3 on one recording, given interference v2's result (detect/interference_det.Detector.run, windows included)."""
    p = params or load_params()
    th = threshold or json.loads(THRESHOLD.read_text(encoding="utf-8"))
    w = v2_res["windows"]
    v2_alert = np.asarray(w["alert"], dtype=bool)
    out = {"version": "v3", "k": 0, "enrollment": [], "fallback": True}
    t_score = freqs_at = None
    if station is not None and norad is not None and start_utc:
        enr = select_enrollment(start_utc, int(station), int(norad), archive if archive is not None else build_archive(),
                                p["k_max"])
        if len(enr) >= p["k_min"]:
            tmpl = fit_template([pass_spectra(Path(a["audio"]), cfg, SPECTRA_CACHE / "enroll") for a in enr], p,
                                float(cfg["qc"]["psd"]["f_low_hz"]))
            freqs, _, db = psd_db_full(y, sr, cfg["qc"]["psd"], cfg["tables"]["features_chunk_windows"])
            t_score, freqs_at = score(db, freqs, tmpl, p)
            n = min(len(t_score), len(v2_alert))
            t_score, freqs_at, v2_alert = t_score[:n], freqs_at[:n], v2_alert[:n]
            out.update(k=len(enr), enrollment=[a["obs_id"] for a in enr], fallback=False, band_hz=tmpl["band_hz"])
    alert, t_alert = decide(v2_alert, t_score, th["tau_t"], p["k"], p["n"])
    out.update(tau_t=th["tau_t"], n_alert_windows=int(alert.sum()), n_template_alert_windows=int(t_alert.sum()),
               alert=alert, template_alert=t_alert,
               template_score=None if t_score is None else np.round(t_score, 3).tolist(),
               template_peak_hz=None if freqs_at is None else freqs_at.tolist())
    return out


def iso(ts) -> str:
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).strftime(FMT)
    return str(ts)


def merge_into_v2(v2_res: dict, v3: dict) -> dict:
    """v2 result with the v3 alert: v2 events kept; template-only runs added as events (fusion reads them as
    interference-model events whose evidence score is v2's max probability over the run, so they enter the flag
    tier unless v2 itself is confident)."""
    w = v2_res["windows"]
    n = len(v3["alert"])
    t = np.asarray(w["t_start_s"][:n], dtype=float)
    prob = np.asarray(w["probability"][:n], dtype=float)
    hop = float(np.median(np.diff(t))) if n > 1 else 0.5
    v2a = np.asarray(w["alert"][:n], dtype=bool)
    extra = []
    for e in events(v3["template_alert"] & ~v2a, t, hop):
        i0, i1 = e["i0"], e["i1"]
        top = i0 + int(np.argmax(v3["template_score"][i0:i1 + 1]))
        extra.append({"t_start_s": e["t_start_s"], "t_end_s": e["t_end_s"], "n_windows": i1 - i0 + 1,
                      "max_probability": round(float(prob[i0:i1 + 1].max()), 4),
                      "peak_freq_hz": float(v3["template_peak_hz"][top]),
                      "reason": (f"narrowband interference (station template, k = {v3['k']}): window spectrum "
                                 f"{v3['template_score'][top]:.1f} robust SD above this station's clean passes at "
                                 f"{v3['template_peak_hz'][top]:.0f} Hz")})
    out = dict(v2_res)
    out["events"] = sorted(v2_res["events"] + extra, key=lambda e: e["t_start_s"])
    out["n_alert_windows"] = int(v3["alert"].sum())
    out["windows"] = {**w, "alert": v3["alert"].astype(int).tolist()}
    out["v3"] = {k: v for k, v in v3.items() if k not in ("alert", "template_alert", "template_score", "template_peak_hz")}
    return out

