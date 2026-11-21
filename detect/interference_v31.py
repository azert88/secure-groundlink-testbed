"""Interference v3.1 (S3): v3 (detect/interference_v3.py, unchanged) with the two changes of the signed addendum
docs/GATES_PHASE2_S3_V31.md (models/gates_p2_s3_v31/lock.json); everything else is v3.

1. band edge: f_drop of each enrollment pass is measured on the common-chain (re-encoded) spectra the template is
   built from (`f_drop_chained`), not on the raw audio (params band_edge = "chained"; "raw" = v3 behaviour);
2. empty bins: bins whose template level m(f) < min_level_db (-60 dB, fixed) are excluded from T_w; a smoothing group
   counts only if none of its bins is excluded; no bin left -> T_w = 0 (params min_level_db = null: v3 behaviour).

The two switches also give the ungated ablations "fix 1 only" / "fix 2 only" (eval/s3_dev_eval.py --version v3.1).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from detect import interference_v3 as V3
from detect.audio_front import steepest_drop

PARAMS = Path("models/interference_v31/params.json")
THRESHOLD = Path("models/interference_v31/threshold.json")
ABLATIONS = {"v3.1": {}, "fix1_only": {"min_level_db": None}, "fix2_only": {"band_edge": "raw"}}


def load_params(path: Path = PARAMS, variant: str = "v3.1") -> dict:
    return {**json.loads(Path(path).read_text(encoding="utf-8")), **ABLATIONS[variant]}


def f_drop_chained(spec: dict, psd: dict) -> float:
    """steepest_drop of the per-bin median (over windows) of a pass's chained window PSD in dB."""
    return steepest_drop(spec["freqs"], np.median(np.asarray(spec["db"], dtype=np.float32), axis=0), psd)


def fit_template(passes: list[dict], p: dict, psd: dict) -> dict:
    """v3 template (V3.fit_template) with the v3.1 band edge and empty-bin mask (`keep`, over the band's bins)."""
    if p.get("band_edge", "chained") == "chained":
        passes = [{**q, "f_drop": f_drop_chained(q, psd)} for q in passes]
    t = V3.fit_template(passes, p, float(psd["f_low_hz"]))
    t["f_drop_used"] = [float(q["f_drop"]) for q in passes]
    lvl = p.get("min_level_db")
    t["keep"] = np.ones(len(t["m"]), bool) if lvl is None else (t["m"] >= float(lvl))
    return t


def score(db: np.ndarray, freqs: np.ndarray, tmpl: dict, p: dict) -> tuple[np.ndarray, np.ndarray]:
    """T_w and the frequency of its maximum (V3.score, excluded bins removed)."""
    band, keep = tmpl["band"], tmpl["keep"]
    r = db[:, band] - np.median(db[:, band], axis=1, keepdims=True)
    e = (r - tmpl["m"]) / tmpl["sigma"]
    w = int(p["smooth_bins"])
    ok = keep.astype(np.float64)
    if w > 1:
        c = np.cumsum(np.pad(e, ((0, 0), (1, 0))), axis=1, dtype=np.float64)
        e = (c[:, w:] - c[:, :-w]) / w
        ck = np.cumsum(np.pad(ok, (1, 0)))
        ok = ck[w:] - ck[:-w]
    valid = ok >= w - 1e-9 if w > 1 else ok > 0
    fb = freqs[band]
    if not valid.any():
        return np.zeros(len(db)), np.full(len(db), np.nan)
    e = np.where(valid[None, :], e, -np.inf)
    i = e.argmax(axis=1)
    return e.max(axis=1).astype(np.float64), fb[i + (w - 1) // 2]


def run_on_recording(v2_res: dict, y: np.ndarray, sr: int, station: int | None, norad: int | None, start_utc: str | None,
                     cfg: dict, archive: list[dict] | None = None, params: dict | None = None,
                     threshold: dict | None = None) -> dict:
    """v3.1 on one recording, given interference v2's result (same contract as V3.run_on_recording)."""
    p = params or load_params()
    th = threshold or json.loads(THRESHOLD.read_text(encoding="utf-8"))
    psd = cfg["qc"]["psd"]
    v2_alert = np.asarray(v2_res["windows"]["alert"], dtype=bool)
    out = {"version": "v3.1", "k": 0, "enrollment": [], "fallback": True}
    t_score = freqs_at = None
    if station is not None and norad is not None and start_utc:
        enr = V3.select_enrollment(start_utc, int(station), int(norad),
                                   archive if archive is not None else V3.build_archive(), p["k_max"])
        if len(enr) >= p["k_min"]:
            tmpl = fit_template([V3.pass_spectra(Path(a["audio"]), cfg, V3.SPECTRA_CACHE / "enroll") for a in enr], p, psd)
            freqs, _, db = V3.psd_db_full(y, sr, psd, cfg["tables"]["features_chunk_windows"])
            t_score, freqs_at = score(db, freqs, tmpl, p)
            n = min(len(t_score), len(v2_alert))
            t_score, freqs_at, v2_alert = t_score[:n], freqs_at[:n], v2_alert[:n]
            out.update(k=len(enr), enrollment=[a["obs_id"] for a in enr], fallback=False, band_hz=tmpl["band_hz"],
                       n_excluded_bins=int((~tmpl["keep"]).sum()))
    alert, t_alert = V3.decide(v2_alert, t_score, th["tau_t"], p["k"], p["n"])
    out.update(tau_t=th["tau_t"], n_alert_windows=int(alert.sum()), n_template_alert_windows=int(t_alert.sum()),
               alert=alert, template_alert=t_alert,
               template_score=None if t_score is None else np.round(t_score, 3).tolist(),
               template_peak_hz=None if freqs_at is None else freqs_at.tolist())
    return out


merge_into_v2 = V3.merge_into_v2
