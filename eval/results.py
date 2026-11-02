"""Phase O: one results table with every metric the challenge asks for, assembled from the existing reports.

Inputs (all produced earlier, nothing is re-run): eval/out/standard_test/report.json, replay/report_test.json,
sequence/report_test.json, interference_v2/report_test.json, fusion/report_dev.json, evasion_near_copy/report.json,
trust/overhead.json.  Outputs: eval/results.json and eval/results.md.
CIs: per-observation bootstrap 95 % from the reports, or Clopper-Pearson 95 % for counts; 0 / n bound by the rule of
three (3 / n) and the exact one-sided 95 % bound 1 - 0.05^(1/n).

Usage:  python eval/results.py [--out-dir eval]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scipy.stats import beta

R = Path("eval/out")


def cp(k: int, n: int) -> list[float]:
    """Clopper-Pearson 95 % interval: [rate, lo, hi]."""
    lo = 0.0 if k == 0 else beta.ppf(0.025, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(0.975, k + 1, n - k)
    return [round(k / n, 4), round(float(lo), 4), round(float(hi), 4)]


def zero_bounds(n: int) -> dict:
    return {"rule_of_three_upper_95": round(3 / n, 4), "exact_one_sided_upper_95": round(1 - 0.05 ** (1 / n), 4)}


def build() -> dict:
    L = lambda p: json.loads((R / p).read_text(encoding="utf-8"))  # noqa: E731
    st, rp, sq = L("standard_test/report.json"), L("replay/report_test.json"), L("sequence/report_test.json")
    iv, nc, tr = L("interference_v2/report_test.json"), L("evasion_near_copy/report.json"), L("trust/overhead.json")
    dev = L("fusion/report_dev.json")["summary"]
    nab = sq["ablations"]["no_content"]["pooled"]
    n_neg = st["n_negative_recordings"]
    n_clean = sum(st["verdicts"]["clean"].values())
    return {
        "source": "eval/out/* reports (TEST split unless stated)",
        "standard_test": {
            "n_attacked": st["n_standard_recordings"], "n_negative": n_neg,
            "replay_detected": st["standard_replay_detected"], "sequence_detected": st["standard_sequence_detected"],
            "sequence_named": st["standard_sequence_named_correctly"],
            "interference_detected": st["standard_interference_detected"], "all_three": st["standard_all_three_detected"],
            "attacked_not_accepted": st["standard_alarm"], "replay_frames_pooled": st["replay_frames_pooled"],
            "sequence_by_subtype": st["sequence_by_subtype"], "interference_by_duration": st["interference_by_duration"],
            "per_satellite": st["per_satellite"], "ece_recording": st["ece_recording"]},
        "false_alarms": {
            "recording_test": {"not_accepted": cp(0, n_neg), "clean": cp(0, n_clean), "benign": cp(0, n_neg - n_clean),
                               "by_station": st["negatives_false_alarm_by_station"], **zero_bounds(n_neg)},
            "recording_dev_train_calib": {
                "n": dev["n_not_attacked"], "not_accepted": round(dev["recording_false_alarm_rate"], 4),
                "quarantined": round(dev["recording_quarantine_false_alarm_rate"], 4),
                "stations_with_false_alarm": {k: v["rate"] for k, v in dev["false_alarm_by_station"].items() if v["rate"] > 0}},
            "frame_test": {"replay": cp(rp["main"]["pooled"]["fp"], rp["n_not_attacked_frames"]),
                           "replay_by_category": rp["main"]["fpr_by_category"],
                           "sequence_as_fused_content_off": cp(nab["fp"], nab["fp"] + nab["tn"]),
                           "sequence_v1_with_content": round(sq["main"]["pooled"]["fpr"], 4),
                           "false_frame_alerts_per_attacked_recording": st["false_frame_alerts_per_standard_recording"]},
            "window_test_interference_v2": {"clean": round(iv["v2"]["raw"]["fpr_clean"], 5),
                                            "benign": round(iv["v2"]["raw"]["fpr_benign"], 5),
                                            "by_station": iv["v2"]["per_station_fpr_raw"]}},
        "pd_vs_sir": {"window_level_v2_snrgrid": iv["v2"]["pd_vs_snr"],
                      "recording_level_standard_test_10dB_bins": st["interference_by_snr10"]},
        "evasion": {
            "J_cross_pass_replay": rp["main"]["recall_by_subtype"]["cross_pass_evasion"],
            "K_sequence": "no dedicated evasion variant; blind spots: deletion 0/21, TECHSAT 1B cannot alert (decision 81)",
            "L_interference_window_tpr_smoothed": {"v1": round(iv["v1"]["evasion_smoothed_tpr"], 3),
                                                   "v2": round(iv["v2"]["evasion_smoothed_tpr"], 3)},
            "N_weak_interference_snr_20_30_dB": st["interference_by_snr10"].get("20"),
            "O_near_copy_replay": nc["modes"]},
        "latency": st["latency"], "efficiency": st["efficiency"],
        "security_overhead": {
            "envelope_bytes_per_frame": tr["overhead_bytes_per_frame"],
            "envelope_overhead_pct": {k: v["overhead_pct"] for k, v in tr["per_frame_size"].items()},
            "seal_us": {k: v["seal_us"] for k, v in tr["per_frame_size"].items()},
            "verify_us": {k: v["verify_us"] for k, v in tr["per_frame_size"].items()},
            "ed25519_sign_us": tr["ed25519_sign_us"], "ed25519_verify_us": tr["ed25519_verify_us"],
            "signature_bytes": tr["signature_bytes"]}}


def markdown(r: dict) -> str:
    c = lambda v: f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"  # noqa: E731
    s, fa, ev = r["standard_test"], r["false_alarms"], r["evasion"]
    nc, so = ev["O_near_copy_replay"], r["security_overhead"]
    rt, lat, eff = fa["recording_test"], r["latency"], r["efficiency"]
    w = fa["window_test_interference_v2"]
    worst = max(w["by_station"].items(), key=lambda kv: kv[1])
    rows = [
        ("**Standard test** (TEST, %d attacked recordings) — replay detected" % s["n_attacked"], c(s["replay_detected"])),
        ("sequence anomaly detected / named correctly", f"{c(s['sequence_detected'])} / {c(s['sequence_named'])}"),
        ("interference detected", f"{c(s['interference_detected'])} (segment {s['interference_by_duration']['segment']['detected']:.3f}, "
                                  f"full pass {s['interference_by_duration']['full_pass']['detected']:.3f})"),
        ("all three detected", c(s["all_three"])),
        ("attacked recording not accepted", c(s["attacked_not_accepted"])),
        ("replay frames P / R / F1", " / ".join(f"{s['replay_frames_pooled'][k]:.3f}" for k in ("precision", "recall", "f1"))),
        ("**False alarms, recording** (TEST, %d clean + benign)" % s["n_negative"],
         f"{c(rt['not_accepted'])}; clean {c(rt['clean'])}, benign {c(rt['benign'])}; every station {max(rt['by_station'].values()):.0%}"),
        ("upper bound for 0 / %d (95 %%)" % s["n_negative"],
         f"rule of three {rt['rule_of_three_upper_95']:.1%}, exact one-sided {rt['exact_one_sided_upper_95']:.1%}"),
        ("false alarms, recording, development (train + calib, %d)" % fa["recording_dev_train_calib"]["n"],
         f"not accepted {fa['recording_dev_train_calib']['not_accepted']:.1%}, quarantined "
         f"{fa['recording_dev_train_calib']['quarantined']:.1%}; stations {fa['recording_dev_train_calib']['stations_with_false_alarm']}"),
        ("false alarms, frame (TEST)", f"replay {c(fa['frame_test']['replay'])}; sequence as fused (content off) "
                                       f"{c(fa['frame_test']['sequence_as_fused_content_off'])}; locked v1 with content "
                                       f"{fa['frame_test']['sequence_v1_with_content']:.2%}"),
        ("false alarms, window (interference v2, TEST)",
         f"clean {w['clean']:.2%}, benign {w['benign']:.2%}; worst station {worst[0]} {worst[1]:.2%}"),
        ("Pd vs SIR, window level (snr_db: Pd)", ", ".join(f"{k}: {v:.2f}" for k, v in r["pd_vs_sir"]["window_level_v2_snrgrid"].items())),
        ("Pd vs SIR, recording level (standard test, 10 dB bins)",
         ", ".join(f"[{k}, {int(k) + 10}): {v['detected']:.2f} (n={v['n']})" for k, v in r["pd_vs_sir"]["recording_level_standard_test_10dB_bins"].items())),
        ("Evasion J: cross-pass replay (frames)", f"recall {ev['J_cross_pass_replay']['recall']:.2f} (n={ev['J_cross_pass_replay']['n']}, 1 event)"),
        ("Evasion K: sequence", ev["K_sequence"]),
        ("Evasion L: interference (intermittent, drift, strongest bin, near-threshold), window TPR",
         f"v1 {ev['L_interference_window_tpr_smoothed']['v1']:.2f}, v2 (used) {ev['L_interference_window_tpr_smoothed']['v2']:.2f}"),
        ("Evasion N: weak interference, snr_db 20–30 dB", f"{ev['N_weak_interference_snr_20_30_dB']['detected']:.2f} "
                                                         f"(n={ev['N_weak_interference_snr_20_30_dB']['n']})"),
        ("**Evasion O: near-copy replay** (1 byte changed per copy, %s recordings)" % nc["near_copy"]["events_detected"].split("/")[1].strip(),
         f"named replay {c(nc['near_copy']['replay_detected'])} vs exact copies {c(nc['exact']['replay_detected'])}; "
         f"still not accepted {c(nc['near_copy']['not_accepted'])} (sequence alerts; FUNCUBE-1 "
         f"{nc['near_copy']['not_accepted_by_satellite']['39444']:.2f}, TECHSAT 1B {nc['near_copy']['not_accepted_by_satellite']['25397']:.2f})"),
        ("Latency", f"replay {lat['replay_delay_s_median']:.0f} s (first copy); interference model median "
                    f"{lat['interference_delay_s_median']:.0f} s (max {lat['interference_delay_s_max']:.0f} s); line screen {lat['line_screen']}"),
        ("Runtime and memory per recording", f"median {eff['runtime_s_median']:.1f} s (max {eff['runtime_s_max']:.0f} s, 4 parallel workers); "
                                             f"peak traced memory {eff['peak_traced_mb_max']:.0f} MB"),
        ("Security: per-frame HMAC envelope", f"{so['envelope_bytes_per_frame']} B per frame ({so['envelope_overhead_pct']['256']:.0f} % of 256 B, "
                                              f"{so['envelope_overhead_pct']['44']:.0f} % of 44 B); seal {so['seal_us']['256']:.1f} µs, verify {so['verify_us']['256']:.1f} µs"),
        ("Security: Ed25519 signed manifests / model locks", f"sign {so['ed25519_sign_us']:.0f} µs, verify {so['ed25519_verify_us']:.0f} µs, "
                                                             f"{so['signature_bytes']} B signature"),
    ]
    head = ["# Results summary (Phase 1)", "",
            "Assembled by `eval/results.py` from the reports in `eval/out/` (TEST split unless stated); full values in "
            "`eval/results.json`. CI: per-observation bootstrap 95 % `mean [lo, hi]`, or Clopper-Pearson 95 % for counts.", "",
            "| metric | value |", "|---|---|"]
    tail = ["", "Near-copy diagnosis (Phase O, decision 102, nothing retuned): without hash matches the copies enter the "
            "counter-time line fit; when they are a large share of the pass the fit fails, which disables the counter-regression "
            "evidence. Planned fix: a robust line (dominant line + lower-counter off-line cluster = copies), as a new locked version."]
    return "\n".join(head + [f"| {a} | {b} |" for a, b in rows] + tail) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assemble eval/results.{json,md} from the existing reports")
    ap.add_argument("--out-dir", default="eval")
    a = ap.parse_args(argv)
    r = build()
    out = Path(a.out_dir)
    (out / "results.json").write_text(json.dumps(r, indent=1, default=float) + "\n", encoding="utf-8")
    (out / "results.md").write_text(markdown(r), encoding="utf-8")
    print(f"wrote {out.as_posix()}/results.json and results.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
