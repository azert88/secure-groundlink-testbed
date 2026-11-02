"""Phase N fusion (docs/DETECTOR_SPEC.md section 5): one recording's detector outputs -> decisions and alerts.

Inputs (all from ONE recording, no labels): replay and sequence per-frame results (detect/replay_det.py,
detect/sequence_det.py), interference v2 per-window results (detect/interference_det.py) and the line screen
(detect/line_screen.py). Frozen parameters in models/fusion/fusion.json (written by eval/fusion_dev.py).

Rules (fixed, nothing learned here except the confidence maps):
  frames   each alerted frame is named with the locked naming rule (detect/naming.py): replay / replay_cross_pass ->
           alert "replay"; every other name -> alert "sequence" with that subtype.
  windows  interference v2 alert windows (k-of-n smoothed) and the hot windows of every flagged line form
           "interference" alerts; a line and a v2 event at the same frequency (+- line_match_hz) are one alert.
  confidence  per alert: isotonic map (fitted on TRAIN + CALIB recordings, out-of-fold scores for interference v2)
           from the alert's evidence score to P(recording carries that attack type); line-screen alerts use the
           dev precision of the screen. Recording confidence = 1 - prod(1 - p) over its alerts.
  decision two tiers per alert. quarantine: replay (copied frames must not be used), line screen, and sequence /
           interference-model alerts whose evidence score reaches quarantine_min_score (recording-level operating
           point chosen on TRAIN + CALIB for <= 1 % of not-attacked recordings). flag: the other alerts (above the
           frozen detector thresholds, below the recording-level point: review). Frame / window / recording take
           the strongest decision of their alerts; accept if none.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from detect.naming import name_frame

DEFAULT_FUSION = Path("models/fusion/fusion.json")
REPLAY_NAMES = {"replay", "replay_cross_pass"}


def load(path: Path = DEFAULT_FUSION) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def conf_map(m: dict | None, x: float) -> float:
    """Piecewise-linear isotonic map {x: [...], y: [...]} (clipped at the ends)."""
    if m is None or x is None or not np.isfinite(x):
        return float("nan")
    return float(np.interp(x, m["x"], m["y"]))


def line_allowed(line: dict, f_edge_hz: float | None, band_hi_hz: float, ls: dict) -> bool:
    """A flagged line counts if it lies below the frozen frequency cap (chosen on train + calib)."""
    if not line["flag"]:
        return False
    if ls["cap"] == "f_edge" and f_edge_hz is not None:
        return line["f_hi_hz"] <= f_edge_hz - ls["f_edge_margin_hz"]
    if ls["cap"] == "band_guard":
        return line["f_hi_hz"] <= band_hi_hz - ls["band_guard_hz"]
    return True


def _decision(kind: str, score: float | None, cfg: dict) -> str:
    """kind: replay, line_screen, sequence or interference_model; score: the alert's evidence score."""
    if kind == "replay" or (kind == "line_screen" and cfg["line_screen"].get("quarantine", True)):
        return "quarantine"
    q = cfg.get("quarantine_min_score", {}).get(kind)
    return "quarantine" if (q is not None and score is not None and score >= q) else "flag"


# --------------------------------------------------------------------------------------------- frames
def frame_alerts(replay: dict | None, sequence: dict | None, tau_r: float, tau_s: float, spc: float | None) -> list[dict]:
    """Named alerted frames (union of both detectors), in stream order."""
    rf = {r["filename"]: r for r in (replay or {}).get("frames", [])}
    sf = {r["filename"]: r for r in (sequence or {}).get("frames", [])}
    order = list(rf) + [f for f in sf if f not in rf]
    out = []
    for fn in order:
        r, s = rf.get(fn), sf.get(fn)
        ra = r is not None and r["score"] >= tau_r
        sa = s is not None and s["score"] >= tau_s
        if not (ra or sa):
            continue
        name = name_frame(ra, r["evidence"] if r else [], sa, s["evidence"] if s else [],
                          s.get("phase_dev_s") if s else None, s.get("dev_s") if s else None, spc)
        kind = "replay" if name in REPLAY_NAMES else "sequence"
        src = r if (kind == "replay" or not sa) else s
        tau = tau_r if src is r else tau_s
        online = next((t for t, v in src.get("trajectory", []) if v >= tau), None)
        out.append({"filename": fn, "t_utc": src["t_utc"], "t_rel_s": src["t_rel_s"], "name": name, "kind": kind,
                    "replay_score": r["score"] if r else None, "sequence_score": s["score"] if s else None,
                    "online_alert_rel_s": online, "reason": src["reason"]})
    return out


def _group_frames(fa: list[dict]) -> list[dict]:
    """Alert events: consecutive alerted frames with the same name."""
    ev = []
    for f in fa:
        if ev and ev[-1]["name"] == f["name"]:
            ev[-1]["frames"].append(f)
        else:
            ev.append({"name": f["name"], "kind": f["kind"], "frames": [f]})
    return ev


# --------------------------------------------------------------------------------------------- fuse
def fuse(replay: dict | None, sequence: dict | None, interference: dict | None, lines: dict | None,
         cfg: dict, tau_r: float, tau_s: float, spc: float | None, f_edge_hz: float | None,
         band_hi_hz: float | None) -> dict:
    """Recording verdict, alerts with names, plain reasons and calibrated confidence; per-frame / per-window decisions."""
    maps, ls = cfg["confidence"], cfg["line_screen"]
    alerts, frames_dec, windows_dec = [], [], []

    # frames
    fa = frame_alerts(replay, sequence, tau_r, tau_s, spc)
    for ev in _group_frames(fa):
        if ev["kind"] == "replay":
            x = max(f["replay_score"] or 0.0 for f in ev["frames"])
            conf, dec = conf_map(maps.get("replay"), x), "quarantine"
        elif all((f["sequence_score"] or 0) < tau_s for f in ev["frames"]):
            # named by the sequence rule but alerted by the replay detector only (one repeated frame)
            x = max(f["replay_score"] or 0.0 for f in ev["frames"])
            conf, dec = conf_map(maps.get("replay"), x), "quarantine"
        else:
            x = max((f["sequence_score"] if f["sequence_score"] is not None else 0.0) for f in ev["frames"])
            conf, dec = conf_map(maps.get("sequence"), x), _decision("sequence", x, cfg)
        online = [f["online_alert_rel_s"] for f in ev["frames"] if f["online_alert_rel_s"] is not None]
        alerts.append({"alert": ev["kind"], "name": ev["name"], "confidence": round(conf, 4), "decision": dec,
                       "evidence_score": round(float(x), 4), "n_frames": len(ev["frames"]),
                       "t_first_utc": ev["frames"][0]["t_utc"], "t_last_utc": ev["frames"][-1]["t_utc"],
                       "t_first_rel_s": ev["frames"][0]["t_rel_s"],
                       "first_online_alert_rel_s": min(online) if online else None,
                       "frames": [f["filename"] for f in ev["frames"]],
                       "reason": f"{ev['name']}: " + ev["frames"][0]["reason"]})
        frames_dec += [{"filename": f["filename"], "decision": dec, "name": f["name"], "confidence": round(conf, 4)}
                       for f in ev["frames"]]

    # windows: interference v2 events
    win_conf: dict[int, tuple[float, str]] = {}
    t_start = None
    if interference and "events" in interference:
        w = interference.get("windows", {})
        t_start = np.asarray(w.get("t_start_s", []), dtype=float)
        hop = float(np.median(np.diff(t_start))) if len(t_start) > 1 else 0.5
        for e in interference["events"]:
            conf = conf_map(maps.get("interference_model"), e["max_probability"])
            idx = np.flatnonzero((t_start >= e["t_start_s"] - 1e-6) & (t_start <= e["t_end_s"] - 2 * hop + 1e-6))
            alerts.append({"alert": "interference", "name": "interference", "evidence": ["window_model"],
                           "confidence": round(conf, 4),
                           "decision": _decision("interference_model", e["max_probability"], cfg),
                           "evidence_score": e["max_probability"], "t_start_s": e["t_start_s"], "t_end_s": e["t_end_s"],
                           "peak_freq_hz": e["peak_freq_hz"], "n_windows": e["n_windows"], "reason": e["reason"],
                           "_idx": idx})
    # windows: line screen
    if lines and lines.get("lines"):
        dur = (lines["n_windows"] + 1) * 0.5
        for ln in lines["lines"]:
            if not line_allowed(ln, f_edge_hz, band_hi_hz if band_hi_hz is not None else np.inf, ls):
                continue
            conf = float(ls["precision_dev"])
            match = [a for a in alerts if a["alert"] == "interference" and "window_model" in a.get("evidence", [])
                     and ln["f_lo_hz"] - ls["line_match_hz"] <= a["peak_freq_hz"] <= ln["f_hi_hz"] + ls["line_match_hz"]]
            idx = np.asarray(ln["hot_windows"], dtype=int)
            reason = "narrowband interference (line screen): " + _line_reason(ln, dur)
            if match:
                a = match[0]
                a["evidence"].append("line_screen")
                a["confidence"] = round(1 - (1 - a["confidence"]) * (1 - conf), 4)
                if _decision("line_screen", None, cfg) == "quarantine":
                    a["decision"] = "quarantine"
                a["reason"] += "; " + reason
                a["_idx"] = np.union1d(a["_idx"], idx)
                a["line_hz"] = [ln["f_lo_hz"], ln["f_hi_hz"]]
            else:
                alerts.append({"alert": "interference", "name": "interference", "evidence": ["line_screen"],
                               "confidence": round(conf, 4), "decision": _decision("line_screen", None, cfg),
                               "evidence_score": ln["frac_windows"], "line_hz": [ln["f_lo_hz"], ln["f_hi_hz"]],
                               "peak_freq_hz": round((ln["f_lo_hz"] + ln["f_hi_hz"]) / 2, 1),
                               "n_windows": int(len(idx)), "reason": reason, "_idx": idx,
                               "t_start_s": float(idx.min() * 0.5) if len(idx) else 0.0,
                               "t_end_s": float(idx.max() * 0.5 + 1.0) if len(idx) else dur})
    for a in alerts:
        if "_idx" in a:
            for i in a.pop("_idx").tolist():
                old = win_conf.get(int(i))
                key = (a["decision"] == "quarantine", a["confidence"])
                if old is None or key > (old[1] == "quarantine", old[0]):
                    win_conf[int(i)] = (a["confidence"], a["decision"])
    for i in sorted(win_conf):
        c, d = win_conf[i]
        windows_dec.append({"win_idx": i, "t_start_s": i * 0.5, "decision": d, "confidence": c})

    p_none = float(np.prod([1 - a["confidence"] for a in alerts if np.isfinite(a["confidence"])]))
    conf_rec = 1 - p_none if alerts else 0.0
    if any(a["decision"] == "quarantine" for a in alerts):
        verdict = "quarantine"
    elif alerts:
        verdict = "flag"
    else:
        verdict = "accept"
    return {"verdict": verdict, "confidence_attacked": round(conf_rec, 4),
            "alerts": alerts, "alert_names": sorted({a["name"] if a["alert"] != "interference" else "interference"
                                                      for a in alerts}),
            "frames": frames_dec, "windows": _compress(windows_dec)}


def _line_reason(ln: dict, dur: float) -> str:
    return (f"line at {ln['f_lo_hz']:.0f}-{ln['f_hi_hz']:.0f} Hz ({ln['width_hz']:.0f} Hz wide at -3 dB), "
            f"{ln['peak_db']:.1f} dB above its local baseline on average and more than +10 dB in "
            f"{100 * ln['frac_windows']:.0f} % of the {dur:.0f} s recording, outside the satellite's downlink band")


def _compress(wd: list[dict]) -> list[dict]:
    """Consecutive windows with the same decision -> one range."""
    out = []
    for w in wd:
        if out and out[-1]["decision"] == w["decision"] and w["win_idx"] == out[-1]["win_last"] + 1:
            out[-1]["win_last"] = w["win_idx"]
            out[-1]["t_end_s"] = w["t_start_s"] + 1.0
            out[-1]["confidence"] = max(out[-1]["confidence"], w["confidence"])
        else:
            out.append({"win_first": w["win_idx"], "win_last": w["win_idx"], "t_start_s": w["t_start_s"],
                        "t_end_s": w["t_start_s"] + 1.0, "decision": w["decision"], "confidence": w["confidence"]})
    return out


def markdown(res: dict) -> str:
    """Short plain-words report of one recording."""
    f = res["fusion"]
    lines = [f"# Recording {res.get('obs_dir', '')}", "",
             f"**Verdict: {f['verdict'].upper()}** (confidence attacked {f['confidence_attacked']:.2f})", ""]
    for k, v in res.get("unavailable", {}).items():
        lines.append(f"- {k}: unavailable ({v})")
    if not f["alerts"]:
        lines.append("No alert.")
    for a in f["alerts"]:
        where = (f"frames {a['t_first_utc']} .. {a['t_last_utc']} ({a['n_frames']})" if "n_frames" in a and "t_first_utc" in a
                 else f"{a.get('t_start_s', 0):.0f}-{a.get('t_end_s', 0):.0f} s")
        lines.append(f"- **{a['name']}** [{a['decision']}, confidence {a['confidence']:.2f}] {where}: {a['reason']}")
    lines += ["", f"Runtime {res.get('runtime_s', 0)} s, peak traced memory {res.get('peak_traced_mb', 0)} MB."]
    return "\n".join(lines) + "\n"
