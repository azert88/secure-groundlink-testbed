"""S4 development evaluation of detect/jam_v1 on the mode B simulation (docs/GATES_PHASE2_S4.md B, E(a), E(d)).

Refuses to run unless the signed gate addendum verifies (models/gates_p2_s4/lock.json). Simulation only.
  python eval/s4_dev_eval.py --stage sim                 # all runs, resumable (one JSON per run), one worker
  python eval/s4_dev_eval.py --stage report              # P0 from calib, FA / detection on train runs
  python eval/s4_dev_eval.py --smoke                     # 1 run of 4 conditions on the 'smoke' seeds, timing only
Runs: calib 20 per benign condition (threshold set); train 50 per benign condition, 20 per jamming condition.
Output: out/s4_dev/runs/<split>/<condition>/<run>.json, out/s4_dev/report.{json,md}.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import link_jam_sim as SIM  # noqa: E402
from detect import jam_v1  # noqa: E402

OUT = Path("out/s4_dev")
RUNS = {"calib": {c: 20 for c in SIM.BENIGN},
        "train": {**{c: 50 for c in SIM.BENIGN}, **{c: 20 for c in SIM.CONDITIONS if c not in SIM.BENIGN}}}
SMOKE = ("nominal", "low_snr", SIM.jam_name("continuous", 0), SIM.jam_name("fullband", -10))


def require_gate() -> None:
    sys.path.insert(0, str(ROOT / "eval"))
    from lock import require_lock
    require_lock("gates_p2_s4")


def run_path(out: Path, split: str, cond: str, run: int) -> Path:
    return out / "runs" / split / cond / f"{run:03d}.json"


def stage_sim(out: Path, splits: list[str], conditions: list[str] | None) -> None:
    jobs = [(s, c, r) for s in splits for c, n in RUNS[s].items() if conditions is None or c in conditions
            for r in range(n)]
    todo = [j for j in jobs if not run_path(out, *j).exists()]
    print(f"{len(jobs)} runs, {len(jobs) - len(todo)} done, {len(todo)} to do", flush=True)
    t_start = time.time()
    for i, (s, c, r) in enumerate(todo):
        res = SIM.simulate_run(s, c, r)
        p = run_path(out, s, c, r)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(res), encoding="utf-8")
        tmp.replace(p)
        el = time.time() - t_start
        print(f"[{i + 1}/{len(todo)}] {s} {c} run {r}: lost {res['n_lost']}/{len(res['frames'])}  "
              f"elapsed {el / 60:.1f} min, eta {el / (i + 1) * (len(todo) - i - 1) / 60:.1f} min", flush=True)


def load_runs(out: Path, split: str) -> dict:
    res = {}
    for c in RUNS[split]:
        ps = sorted((out / "runs" / split / c).glob("*.json"))
        res[c] = [json.loads(p.read_text(encoding="utf-8")) for p in ps]
    return res


def rows_of(run: dict, params: dict, remove_gain: bool = True) -> list[dict]:
    return jam_v1.window_rows(run["frames"], run["blocks"], params, remove_gain)


def cp_upper(k: int, n: int, conf: float = 0.95) -> float:
    """One-sided Clopper-Pearson upper bound."""
    return 1.0 if k >= n else float(stats.beta.ppf(conf, k + 1, n - k))


def wilson(k: int, n: int, z: float = 1.959964) -> list[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def fit(calib: dict, params: dict, key: str = "P", remove_gain: bool = True, l0: float | None = None) -> float:
    l0 = params["loss_threshold"] if l0 is None else l0
    vals = [r[key] for runs in calib.values() for run in runs for r in rows_of(run, params, remove_gain) if r["L"] >= l0]
    return jam_v1.fit_p0(vals, params)


def score(runs: list[dict], p0: float, params: dict, key: str = "P", remove_gain: bool = True,
          l0: float | None = None) -> dict:
    k = n = kp = 0
    pilot = []
    for run in runs:
        rows = jam_v1.apply(rows_of(run, params, remove_gain), p0, params, key, l0)
        k += sum(r["jamming_suspected"] for r in rows)
        n += len(rows)
        kp += jam_v1.pass_alert(rows)
        pilot += run["pilot_snr_db_windows"]
    return {"windows": n, "alert_windows": k, "rate": k / n if n else float("nan"), "passes": len(runs),
            "alert_passes": kp, "pass_rate": kp / len(runs) if runs else float("nan"),
            "pilot_snr_db_median": float(np.median(pilot)) if pilot else float("nan")}


def stage_report(out: Path) -> dict:
    params = jam_v1.load_params()
    calib, train = load_runs(out, "calib"), load_runs(out, "train")
    missing = {s: {c: RUNS[s][c] - len(v) for c, v in d.items() if len(v) < RUNS[s][c]}
               for s, d in (("calib", calib), ("train", train))}
    p0 = fit(calib, params)
    rep = {"gate": "docs/GATES_PHASE2_S4.md", "p0_db": p0, "missing_runs": missing, "fa": {}, "detection": {},
           "ablations": {}}
    for c in SIM.BENIGN:
        s = score(train[c], p0, params)
        s["cp_upper_95"] = cp_upper(s["alert_windows"], s["windows"])
        rep["fa"][c] = s
    worst = max(rep["fa"], key=lambda c: rep["fa"][c]["rate"])
    rep["gate_a"] = {"worst_condition": worst, "worst_fa": rep["fa"][worst]["rate"],
                     "pass": rep["fa"][worst]["rate"] <= 0.01}
    for c in SIM.CONDITIONS:
        if c in SIM.BENIGN:
            continue
        s = score(train[c], p0, params)
        s["wilson_95"] = wilson(s["alert_windows"], s["windows"])
        s["pass_wilson_95"] = wilson(s["alert_passes"], s["passes"])
        rep["detection"][c] = s
    # ablations, reported without a gate
    ab = rep["ablations"]
    ab["gain_not_removed"] = {"p0_db": fit(calib, params, remove_gain=False)}
    ab["gain_not_removed"]["gain_step_fa"] = score(train["gain_step"], ab["gain_not_removed"]["p0_db"], params,
                                                   remove_gain=False)["rate"]
    pl = fit(calib, params, key="P_lost")
    ab["per_frame_lost_power"] = {"p0_db": pl, "fa": {c: score(train[c], pl, params, "P_lost")["rate"] for c in SIM.BENIGN},
                                  "detection": {c: score(train[c], pl, params, "P_lost")["rate"]
                                                for c in rep["detection"]}}
    for l0 in (0.2, 0.5):
        p = fit(calib, params, l0=l0)
        ab[f"L0_{l0}"] = {"p0_db": p, "fa": {c: score(train[c], p, params, l0=l0)["rate"] for c in SIM.BENIGN},
                          "detection": {c: score(train[c], p, params, l0=l0)["rate"] for c in rep["detection"]}}
    (out / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    write_md(out / "report.md", rep)
    return rep


def write_md(path: Path, rep: dict) -> None:
    L = ["# S4 dev report (jam_v1, mode B simulation)", "", f"P0 = {rep['p0_db']:.2f} dB (calib runs). "
         f"Missing runs: {rep['missing_runs']}", "", "## (a) False alarms (train runs)",
         "| condition | windows | alerts | FA | CP 95 % upper | passes alerted | pilot SNR (dB, diag.) |", "|---|---|---|---|---|---|---|"]
    for c, s in rep["fa"].items():
        L.append(f"| {c} | {s['windows']} | {s['alert_windows']} | {s['rate']:.4f} | {s['cp_upper_95']:.4f} | "
                 f"{s['alert_passes']}/{s['passes']} | {s['pilot_snr_db_median']:.1f} |")
    g = rep["gate_a"]
    L += ["", f"Gate (a): worst {g['worst_condition']} FA {g['worst_fa']:.4f} -> {'PASS' if g['pass'] else 'FAIL'}",
          "", "## (d) Detection (no threshold)", "| condition | windows alerted | Wilson 95 % | passes alerted | pilot SNR |",
          "|---|---|---|---|---|"]
    for c, s in rep["detection"].items():
        w = s["wilson_95"]
        L.append(f"| {c} | {s['alert_windows']}/{s['windows']} ({s['rate']:.3f}) | {w[0]:.3f}-{w[1]:.3f} | "
                 f"{s['alert_passes']}/{s['passes']} | {s['pilot_snr_db_median']:.1f} |")
    L += ["", "## Ablations (not gated)", "```", json.dumps(rep["ablations"], indent=1), "```"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def smoke(out: Path) -> None:
    params = jam_v1.load_params()
    for c in SMOKE:
        t = time.time()
        res = SIM.simulate_run("smoke", c, 0)
        rows = rows_of(res, params)
        print(f"{c}: {time.time() - t:.1f} s, lost {res['n_lost']}/{len(res['frames'])}, "
              f"L {[round(r['L'], 1) for r in rows]}, P {[round(r['P'], 1) for r in rows]}, "
              f"pilot SNR {[round(x, 1) for x in res['pilot_snr_db_windows']]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["sim", "report"])
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--split", choices=["calib", "train", "all"], default="all")
    ap.add_argument("--conditions", nargs="*")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    require_gate()
    out = Path(a.out)
    if a.smoke:
        smoke(out)
    elif a.stage == "sim":
        stage_sim(out, ["calib", "train"] if a.split == "all" else [a.split], a.conditions)
    elif a.stage == "report":
        rep = stage_report(out)
        print(json.dumps(rep["gate_a"]))
    else:
        ap.error("--stage or --smoke required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
